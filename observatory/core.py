"""Chat Observatory core: OTLP/JSON parsing, PII redaction, failure clustering, regression-test generation."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

STOP = {"the", "a", "an", "to", "of", "i", "my", "do", "how", "and", "is"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
STATUSES = {"ok", "error", "flagged"}


def redact(text: str) -> str:
    return PHONE.sub("[PHONE]", EMAIL.sub("[EMAIL]", text))


@dataclass
class Trace:
    id: str
    prompt: str
    response: str
    status: str  # ok | error | flagged
    latency_ms: float = 0.0


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _nanos(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _attrs(attributes) -> dict:
    out = {}
    for item in _list(attributes):
        item = _dict(item)
        value = _dict(item.get("value"))
        out[str(item.get("key", ""))] = next(iter(value.values())) if value else ""
    return out


def parse_otlp(payload: dict) -> list[Trace]:
    """Parse OTLP/JSON (resourceSpans -> scopeSpans -> spans); tolerant of malformed input, never raises."""
    traces = []
    for rs in _list(_dict(payload).get("resourceSpans")):
        for ss in _list(_dict(rs).get("scopeSpans")):
            for span in map(_dict, _list(_dict(ss).get("spans"))):
                a = _attrs(span.get("attributes"))
                if "gen_ai.prompt" not in a and "gen_ai.completion" not in a:
                    continue
                millis = (_nanos(span.get("endTimeUnixNano")) - _nanos(span.get("startTimeUnixNano"))) / 1e6
                is_err = _dict(span.get("status")).get("code") in (2, "STATUS_CODE_ERROR")
                status = "error" if is_err else str(a.get("quality.status", "ok"))
                traces.append(Trace(
                    id=str(span.get("traceId", ""))[:64],
                    prompt=redact(str(a.get("gen_ai.prompt", "")))[:4000],
                    response=redact(str(a.get("gen_ai.completion", "")))[:4000],
                    status=status if status in STATUSES else "flagged",
                    latency_ms=max(0.0, millis)))
    return traces


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))  # nearest-rank (ceil)
    return float(ordered[int(rank) - 1])


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def cluster(traces: list[Trace], threshold: float = 0.5) -> list[list[Trace]]:
    """Greedy single-pass clustering of failing traces by token-set similarity."""
    groups: list[tuple[set[str], list[Trace]]] = []
    for t in (t for t in traces if t.status != "ok"):
        tk = tokens(t.prompt + " " + t.response)
        for rep, members in groups:
            if jaccard(rep, tk) >= threshold:
                members.append(t)
                break
        else:
            groups.append((tk, [t]))
    return sorted((m for _, m in groups), key=len, reverse=True)


def label(group: list[Trace], k: int = 3) -> str:
    counts = Counter(w for t in group for w in tokens(t.prompt + " " + t.response))
    return "-".join(w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k])


def to_cases(groups: list[list[Trace]], min_size: int = 1) -> list[dict]:
    return [{"id": f"reg_{i}_{label(g)}", "prompt": g[0].prompt, "occurrences": len(g)}
            for i, g in enumerate(groups) if len(g) >= min_size]


def render_pytest(cases: list[dict]) -> str:
    return (
        "import json\nimport pytest\n\n"
        f"CASES = json.loads({json.dumps(cases)!r})\n\n\n"
        '@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])\n'
        "def test_regression(case, model):\n"
        '    """`model` fixture: callable(prompt) -> {"status": ...}."""\n'
        '    assert model(case["prompt"]).get("status") == "ok"\n'
    )
