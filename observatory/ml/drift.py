"""Semantic distribution and latency drift detection for production LLM monitoring."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .embeddings import EmbeddingVector, embed_text


@dataclass(frozen=True)
class DriftReport:
    status: str  # STABLE | WARNING | CRITICAL
    centroid_drift: float  # Cosine distance between baseline and current centroid [0.0, 2.0]
    latency_psi: float  # Population Stability Index on latency distribution
    failure_rate_baseline: float
    failure_rate_current: float
    sample_size_baseline: int
    sample_size_current: int
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "centroid_drift": round(self.centroid_drift, 4),
            "latency_psi": round(self.latency_psi, 4),
            "failure_rate_baseline": round(self.failure_rate_baseline, 4),
            "failure_rate_current": round(self.failure_rate_current, 4),
            "sample_size_baseline": self.sample_size_baseline,
            "sample_size_current": self.sample_size_current,
            "recommendation": self.recommendation,
        }


def _compute_mean_centroid(texts: list[str]) -> EmbeddingVector:
    if not texts:
        return EmbeddingVector(())
    embeddings = [embed_text(t) for t in texts]
    dim = embeddings[0].dim
    accum = [0.0] * dim
    for emb in embeddings:
        for d in range(dim):
            accum[d] += emb.values[d]

    n = float(len(embeddings))
    avg = [x / n for x in accum]
    mag = math.sqrt(sum(x * x for x in avg))
    if mag > 0:
        avg = [x / mag for x in avg]
    return EmbeddingVector(tuple(avg))


def _calculate_psi(baseline_values: list[float], current_values: list[float], bins: int = 5) -> float:
    """Calculates the Population Stability Index (PSI) between two numerical distributions."""
    if not baseline_values or not current_values:
        return 0.0

    min_b, max_b = min(baseline_values), max(baseline_values)
    if max_b - min_b < 1e-4:
        # Zero or near-zero variance in baseline: compare relative mean shift directly
        mean_b = sum(baseline_values) / len(baseline_values)
        mean_c = sum(current_values) / len(current_values)
        return min(1.0, abs(mean_c - mean_b) / (abs(mean_b) + 1.0))

    b_sorted = sorted(baseline_values)
    n_b = len(b_sorted)

    # Calculate unique quantile thresholds from baseline
    raw_cutoffs = [b_sorted[int(i * (n_b - 1) / bins)] for i in range(1, bins)]
    cutoffs = sorted(list(set(raw_cutoffs)))
    if not cutoffs:
        return 0.0

    def bin_counts(values: list[float]) -> list[int]:
        counts = [0] * (len(cutoffs) + 1)
        for val in values:
            placed = False
            for idx, cut in enumerate(cutoffs):
                if val <= cut:
                    counts[idx] += 1
                    placed = True
                    break
            if not placed:
                counts[-1] += 1
        return counts

    b_counts = bin_counts(baseline_values)
    c_counts = bin_counts(current_values)

    n_base = float(len(baseline_values))
    n_curr = float(len(current_values))

    psi = 0.0
    epsilon = 0.0001  # Smoothing factor to prevent log(0)
    for b_c, c_c in zip(b_counts, c_counts):
        b_pct = max(epsilon, b_c / n_base)
        c_pct = max(epsilon, c_c / n_curr)
        psi += (c_pct - b_pct) * math.log(c_pct / b_pct)

    return max(0.0, psi)


def detect_drift(baseline_traces: list[Any], current_traces: list[Any]) -> DriftReport:
    """Detects semantic distribution and latency drift between baseline and current production traffic."""
    if not baseline_traces or not current_traces:
        return DriftReport(
            status="STABLE",
            centroid_drift=0.0,
            latency_psi=0.0,
            failure_rate_baseline=0.0,
            failure_rate_current=0.0,
            sample_size_baseline=len(baseline_traces),
            sample_size_current=len(current_traces),
            recommendation="Insufficient trace volume to establish drift baseline.",
        )

    # 1. Semantic Embedding Centroid Drift
    base_texts = [f"{t.prompt} {t.response}" for t in baseline_traces]
    curr_texts = [f"{t.prompt} {t.response}" for t in current_traces]

    base_centroid = _compute_mean_centroid(base_texts)
    curr_centroid = _compute_mean_centroid(curr_texts)
    drift_distance = base_centroid.cosine_distance(curr_centroid)

    # 2. Latency PSI
    base_latencies = [getattr(t, "latency_ms", 0.0) for t in baseline_traces]
    curr_latencies = [getattr(t, "latency_ms", 0.0) for t in current_traces]
    psi = _calculate_psi(base_latencies, curr_latencies)

    # 3. Failure rates
    base_fails = sum(1 for t in baseline_traces if getattr(t, "status", "") != "ok")
    curr_fails = sum(1 for t in current_traces if getattr(t, "status", "") != "ok")
    base_rate = base_fails / len(baseline_traces)
    curr_rate = curr_fails / len(current_traces)

    # Status classification
    if drift_distance >= 0.35 or psi >= 0.25 or (curr_rate - base_rate) >= 0.15:
        status = "CRITICAL"
        rec = "Critical prompt distribution shift or latency degradation detected! Alert on-call."
    elif drift_distance >= 0.15 or psi >= 0.10 or (curr_rate - base_rate) >= 0.05:
        status = "WARNING"
        rec = "Moderate distribution drift detected. Review incoming query topics and model quality."
    else:
        status = "STABLE"
        rec = "Distribution is stable within acceptable operational bounds."

    return DriftReport(
        status=status,
        centroid_drift=drift_distance,
        latency_psi=psi,
        failure_rate_baseline=base_rate,
        failure_rate_current=curr_rate,
        sample_size_baseline=len(baseline_traces),
        sample_size_current=len(current_traces),
        recommendation=rec,
    )
