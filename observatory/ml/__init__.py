"""Chat Observatory AI/ML engine: embeddings, semantic clustering, taxonomy classification, and drift detection."""
from __future__ import annotations

from .taxonomy import FailureTaxonomy, classify_failure
from .embeddings import EmbeddingVector, embed_text, cosine_similarity
from .clustering import semantic_cluster, project_2d
from .drift import DriftReport, detect_drift
from .evaluator import generate_regression_suite

__all__ = [
    "FailureTaxonomy",
    "classify_failure",
    "EmbeddingVector",
    "embed_text",
    "cosine_similarity",
    "semantic_cluster",
    "project_2d",
    "DriftReport",
    "detect_drift",
    "generate_regression_suite",
]
