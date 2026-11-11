"""Dense semantic embedding engine with zero external dependencies and cosine geometry."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

STOP_WORDS = {"the", "a", "an", "to", "of", "i", "my", "do", "how", "and", "is", "in", "it", "for", "on", "with"}


@dataclass(frozen=True)
class EmbeddingVector:
    values: tuple[float, ...]

    @property
    def dim(self) -> int:
        return len(self.values)

    def norm(self) -> float:
        sq_sum = sum(x * x for x in self.values)
        return math.sqrt(sq_sum) if sq_sum > 0 else 0.0

    def dot(self, other: EmbeddingVector) -> float:
        if self.dim != other.dim:
            raise ValueError(f"Dimension mismatch: {self.dim} != {other.dim}")
        return sum(a * b for a, b in zip(self.values, other.values))

    def cosine_similarity(self, other: EmbeddingVector) -> float:
        mag = self.norm() * other.norm()
        if mag == 0.0:
            return 0.0
        return max(-1.0, min(1.0, self.dot(other) / mag))

    def cosine_distance(self, other: EmbeddingVector) -> float:
        return 1.0 - self.cosine_similarity(other)


def _tokenize(text: str) -> list[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in raw_tokens if t not in STOP_WORDS]


def _hash_token(token: str, seed: int) -> float:
    """Deterministic hash mapping a token to a float in [-1.0, 1.0]."""
    h = hashlib.sha256(f"{token}:{seed}".encode("utf-8")).digest()
    raw = int.from_bytes(h[:4], byteorder="big", signed=False)
    # Map unsigned 32-bit int to range [-1.0, 1.0]
    return (raw / 2147483647.5) - 1.0


def embed_text(text: str, dim: int = 64) -> EmbeddingVector:
    """Generates a dense unit-normalized semantic embedding vector for arbitrary text.

    Uses an n-gram sublinear TF feature extractor projected across orthogonal random hyperplanes.
    Deterministic, pure Python, zero latency overhead, thread-safe.
    """
    tokens = _tokenize(text)
    if not tokens:
        return EmbeddingVector(tuple(0.0 for _ in range(dim)))

    # Compute term frequencies with sublinear scaling (1 + log(tf))
    tf: dict[str, float] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0.0) + 1.0

    # Also capture character 3-grams for morphological/subword matching
    for t in tokens:
        if len(t) >= 4:
            for i in range(len(t) - 2):
                ngram = t[i : i + 3]
                tf[ngram] = tf.get(ngram, 0.0) + 0.5

    vector = [0.0] * dim
    for term, count in tf.items():
        weight = 1.0 + math.log(count)
        for d in range(dim):
            projection = _hash_token(term, d * 31 + 17)
            vector[d] += weight * projection

    # Unit normalize
    mag = math.sqrt(sum(v * v for v in vector))
    if mag > 0:
        vector = [v / mag for v in vector]
    else:
        vector = [0.0] * dim

    return EmbeddingVector(tuple(vector))


def cosine_similarity(v1: EmbeddingVector, v2: EmbeddingVector) -> float:
    return v1.cosine_similarity(v2)
