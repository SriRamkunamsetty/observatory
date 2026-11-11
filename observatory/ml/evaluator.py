"""Enterprise regression test generator with multi-assertion quality gates."""
from __future__ import annotations

import json
from typing import Any

from .clustering import SemanticCluster
from .taxonomy import classify_failure


def generate_regression_suite(clusters: list[SemanticCluster]) -> str:
    """Generates an enterprise-ready, parameterized pytest test suite with multi-assertion quality gates."""
    cases = []
    for c in clusters:
        tax = classify_failure(c.sample_prompt, c.sample_response, "error")
        # Extract a short distinctive failure signature to assert against
        failure_sig = c.sample_response.strip().split("\n")[0][:60]
        cases.append({
            "id": c.id,
            "label": c.label,
            "taxonomy": c.taxonomy.value,
            "prompt": c.sample_prompt,
            "occurrences": c.count,
            "negative_assertion": failure_sig,
            "remediation_hint": tax.remediation_hint,
        })

    cases_json_repr = repr(json.dumps(cases))

    return (
        '"""Auto-generated LLM Regression Quality Gate Suite by Chat Observatory.\n\n'
        'Run this suite in CI/CD against your staging model endpoint to prevent regressions.\n'
        'Example: pytest test_regressions.py --model=https://staging.api.corp/llm\n'
        '"""\n'
        "import json\n"
        "import pytest\n\n"
        f"CASES = json.loads({cases_json_repr})\n\n\n"
        '@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])\n'
        "def test_regression_case(case, model):\n"
        '    """Verifies that the LLM fulfills the prompt without repeating past failure modes.\n\n'
        '    Fixture `model`: Callable[[str], dict] returning {"status": "ok", "response": str, "latency_ms": float}\n'
        '    """\n'
        '    result = model(case["prompt"])\n'
        '    assert isinstance(result, dict), "Model fixture must return a dictionary"\n\n'
        "    # 1. Output Quality Assertion\n"
        '    assert result.get("status") == "ok", (\n'
        '        f"Model failed prompt with status {result.get(\'status\')}. "\n'
        '        f"Remediation: {case.get(\'remediation_hint\')}"\n'
        "    )\n\n"
        "    # 2. Negative Pattern Check (ensure past failure response is not emitted)\n"
        '    past_failure = case.get("negative_assertion", "")\n'
        '    if past_failure and len(past_failure) > 10:\n'
        '        assert past_failure.lower() not in result.get("response", "").lower(), (\n'
        '            f"Model re-emitted known failure signature: {past_failure}"\n'
        "        )\n\n"
        "    # 3. Latency SLA Budget Check (max 3000ms)\n"
        '    latency = result.get("latency_ms")\n'
        "    if latency is not None:\n"
        '        assert latency <= 3000.0, f"Latency SLA exceeded: {latency}ms > 3000ms"\n'
    )
