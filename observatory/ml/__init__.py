"""Chat Observatory AI/ML engine: embeddings, semantic clustering, taxonomy classification, and drift detection."""
from __future__ import annotations

from .clustering import project_2d, semantic_cluster
from .drift import DriftReport, detect_drift
from .embeddings import EmbeddingVector, cosine_similarity, embed_text
from .evaluator import generate_regression_suite
from .taxonomy import FailureTaxonomy, classify_failure

__all__ = [
    "DriftReport",
    "EmbeddingVector",
    "FailureTaxonomy",
    "classify_failure",
    "cosine_similarity",
    "detect_drift",
    "embed_text",
    "generate_regression_suite",
    "project_2d",
    "semantic_cluster",
]
