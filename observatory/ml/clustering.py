"""Semantic failure clustering using dense vector cosine geometry and 2D projection."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .embeddings import EmbeddingVector, embed_text
from .taxonomy import FailureTaxonomy, classify_failure


@dataclass
class SemanticCluster:
    id: str
    label: str
    taxonomy: FailureTaxonomy
    count: int
    sample_prompt: str
    sample_response: str
    centroid: EmbeddingVector
    x: float  # Projected 2D coordinate for UI visualization [-1.0, 1.0]
    y: float  # Projected 2D coordinate for UI visualization [-1.0, 1.0]
    traces: list[Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "taxonomy": self.taxonomy.value,
            "count": self.count,
            "sample_prompt": self.sample_prompt,
            "sample_response": self.sample_response,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "trace_ids": [t.id for t in self.traces[:20]],
        }


def project_2d(v: EmbeddingVector) -> tuple[float, float]:
    """Projects high-dimensional embedding vector to 2D coordinates [-1.0, 1.0]."""
    if v.dim < 2:
        return (0.0, 0.0)

    # Use deterministic orthogonal projection basis
    even_sum = sum(v.values[i] for i in range(0, v.dim, 2))
    odd_sum = sum(v.values[i] for i in range(1, v.dim, 2))

    # Scale to [-1.0, 1.0] range using hyperbolic tangent
    import math

    x = math.tanh(even_sum * 1.5)
    y = math.tanh(odd_sum * 1.5)
    return (x, y)


def _compute_centroid(vectors: list[EmbeddingVector]) -> EmbeddingVector:
    if not vectors:
        return EmbeddingVector(())
    dim = vectors[0].dim
    accum = [0.0] * dim
    for vec in vectors:
        for d in range(dim):
            accum[d] += vec.values[d]

    n = float(len(vectors))
    avg = [x / n for x in accum]
    mag = sum(x * x for x in avg) ** 0.5
    if mag > 0:
        avg = [x / mag for x in avg]
    return EmbeddingVector(tuple(avg))


def _extract_label(traces: list[Any], k: int = 3) -> str:
    stop_words = {"the", "a", "an", "to", "of", "i", "my", "do", "how", "and", "is", "in", "it", "for", "on", "with"}
    import re

    counts: Counter[str] = Counter()
    for t in traces:
        words = re.findall(r"[a-z0-9]+", f"{t.prompt} {t.response}".lower())
        counts.update(w for w in words if w not in stop_words and len(w) > 2)

    top_words = [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]
    return "-".join(top_words) if top_words else "unclassified-failure"


def semantic_cluster(traces: list[Any], threshold: float = 0.55) -> list[SemanticCluster]:
    """Clusters failing traces using dense semantic vector similarity and centroid updates."""
    failing = [t for t in traces if getattr(t, "status", "") != "ok"]
    if not failing:
        return []

    # Pre-embed traces
    trace_embeddings = [
        (t, embed_text(f"{t.prompt} {t.response}"))
        for t in failing
    ]

    clusters_raw: list[list[tuple[Any, EmbeddingVector]]] = []

    for trace, emb in trace_embeddings:
        assigned = False
        for group in clusters_raw:
            # Check similarity with group centroid
            centroid = _compute_centroid([e for _, e in group])
            sim = emb.cosine_similarity(centroid)
            if sim >= threshold:
                group.append((trace, emb))
                assigned = True
                break
        if not assigned:
            clusters_raw.append([(trace, emb)])

    # Sort clusters by size descending
    clusters_raw.sort(key=len, reverse=True)

    result: list[SemanticCluster] = []
    for idx, group in enumerate(clusters_raw):
        group_traces = [t for t, _ in group]
        centroid = _compute_centroid([e for _, e in group])
        x, y = project_2d(centroid)
        label = _extract_label(group_traces)

        # Classify dominant taxonomy
        tax_counts: Counter[FailureTaxonomy] = Counter()
        for t in group_traces:
            tax = classify_failure(t.prompt, t.response, t.status, getattr(t, "latency_ms", 0.0))
            tax_counts[tax.category] += 1
        dominant_tax = tax_counts.most_common(1)[0][0] if tax_counts else FailureTaxonomy.GENERAL_ERROR

        cluster_obj = SemanticCluster(
            id=f"cluster_{idx}_{label}",
            label=label,
            taxonomy=dominant_tax,
            count=len(group_traces),
            sample_prompt=group_traces[0].prompt,
            sample_response=group_traces[0].response,
            centroid=centroid,
            x=x,
            y=y,
            traces=group_traces,
        )
        result.append(cluster_obj)

    return result
