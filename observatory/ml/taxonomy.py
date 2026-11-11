"""Automated LLM failure taxonomy classifier and root-cause diagnostician."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FailureTaxonomy(str, Enum):
    HALLUCINATION = "hallucination"
    TOOL_ERROR = "tool_error"
    PROMPT_INJECTION = "prompt_injection"
    REFUSAL_SAFETY = "refusal_safety"
    CONTEXT_OVERFLOW = "context_overflow"
    SCHEMA_VIOLATION = "schema_violation"
    LATENCY_ANOMALY = "latency_anomaly"
    GENERAL_ERROR = "general_error"


@dataclass(frozen=True)
class TaxonomyClassification:
    category: FailureTaxonomy
    confidence: float
    root_cause: str
    remediation_hint: str


# Heuristic patterns for enterprise LLM failure modes
_PATTERNS: list[tuple[FailureTaxonomy, re.Pattern, float, str, str]] = [
    # Prompt injection / adversarial attacks
    (
        FailureTaxonomy.PROMPT_INJECTION,
        re.compile(
            r"(ignore\s+(all\s+)?previous\s+instructions|system\s+prompt|jailbreak|DAN\s+mode|"
            r"override\s+guardrails|bypass\s+filter|developer\s+mode\s+enabled)",
            re.IGNORECASE,
        ),
        0.95,
        "Adversarial prompt injection attempt or system prompt extraction detected",
        "Add an input guardrail filter and sanitize system prompt delimiters.",
    ),
    # Hallucination / fabricated facts & citations
    (
        FailureTaxonomy.HALLUCINATION,
        re.compile(
            r"(this citation does not exist|fake citation|according to smith et al\.?\s*203\d|"
            r"fabricated\s+(data|fact|source)|as\s+stated\s+in\s+fake_paper|"
            r"hallucinat(ed|ion)|fictitious\s+reference)",
            re.IGNORECASE,
        ),
        0.90,
        "Fabricated factual claims or non-existent academic citation detected",
        "Enforce strict RAG grounding and verify reference URLs before rendering.",
    ),
    # Tool / Function calling errors
    (
        FailureTaxonomy.TOOL_ERROR,
        re.compile(
            r"(tool\s+call\s+failed|function_call_error|invalid\s+tool\s+arguments|"
            r"failed\s+to\s+execute\s+tool|upstream\s+timeout\s+while\s+calling|api\s+endpoint\s+failed|"
            r"tool_use_failed|external\s+service\s+error)",
            re.IGNORECASE,
        ),
        0.92,
        "Tool/function calling execution failure or upstream API timeout",
        "Implement exponential backoff retries and validate tool parameter schemas.",
    ),
    # Schema / JSON structural violation
    (
        FailureTaxonomy.SCHEMA_VIOLATION,
        re.compile(
            r"(json\.decoder\.JSONDecodeError|invalid\s+json\s+format|unexpected\s+token|"
            r"malformed\s+json|failed\s+pydantic\s+validation|schema\s+validation\s+failed|"
            r"missing\s+required\s+key|unterminated\s+string)",
            re.IGNORECASE,
        ),
        0.94,
        "Structured output JSON schema violation or unparseable payload",
        "Use grammar-guided generation or strict JSON mode in your model parameters.",
    ),
    # Refusal & safety guardrails
    (
        FailureTaxonomy.REFUSAL_SAFETY,
        re.compile(
            r"(i\s+cannot\s+fulfill|as\s+an\s+ai\s+language\s+model|policy\s+violation|"
            r"violates\s+content\s+guidelines|harmful\s+content\s+blocked|unsafe\s+content|"
            r"content\s+moderation\s+flag)",
            re.IGNORECASE,
        ),
        0.88,
        "Model safety guardrail triggered or policy refusal returned",
        "Review safety filter sensitivity and inspect prompt context for false positives.",
    ),
    # Context window overflow
    (
        FailureTaxonomy.CONTEXT_OVERFLOW,
        re.compile(
            r"(maximum\s+context\s+length|token\s+limit\s+exceeded|prompt\s+is\s+too\s+long|"
            r"exceeded\s+the\s+context\s+window|truncat(ed|ion)\s+due\s+to\s+length)",
            re.IGNORECASE,
        ),
        0.96,
        "Context window token capacity exceeded",
        "Implement message summarization or truncate conversation history.",
    ),
    # Latency anomaly
    (
        FailureTaxonomy.LATENCY_ANOMALY,
        re.compile(
            r"(timeout|timed\s+out|request\s+timed\s+out|deadline\s+exceeded|"
            r"gateway\s+timeout|504\s+gateway)",
            re.IGNORECASE,
        ),
        0.85,
        "Execution exceeded SLA latency threshold or encountered network timeout",
        "Configure streaming responses and scale upstream LLM serving capacity.",
    ),
]


def classify_failure(
    prompt: str,
    response: str,
    status: str,
    latency_ms: float = 0.0,
) -> TaxonomyClassification:
    """Classifies a failing or flagged LLM interaction into an enterprise taxonomy."""
    if status == "ok":
        return TaxonomyClassification(
            category=FailureTaxonomy.GENERAL_ERROR,
            confidence=0.0,
            root_cause="Trace is marked ok",
            remediation_hint="No remediation necessary.",
        )

    combined_text = f"{prompt} {response}"

    # Check rule-based pattern matching
    for cat, pattern, confidence, root_cause, hint in _PATTERNS:
        if pattern.search(combined_text):
            return TaxonomyClassification(
                category=cat,
                confidence=confidence,
                root_cause=root_cause,
                remediation_hint=hint,
            )

    # Latency anomaly check based on measurement threshold
    if latency_ms >= 2500.0:
        return TaxonomyClassification(
            category=FailureTaxonomy.LATENCY_ANOMALY,
            confidence=0.80,
            root_cause=f"High latency spike detected: {latency_ms:.0f}ms (threshold 2500ms)",
            remediation_hint="Enable streaming or optimize model quantization level.",
        )

    # General error fallback
    return TaxonomyClassification(
        category=FailureTaxonomy.GENERAL_ERROR,
        confidence=0.50,
        root_cause="Unclassified failure mode",
        remediation_hint="Inspect raw span attributes and prompt inputs for root cause.",
    )
