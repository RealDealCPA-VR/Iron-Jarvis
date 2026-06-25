"""Offline text embeddings (§22 retrieval).

``MockEmbedder`` is a deterministic, network-free embedder so retrieval works in
the fully offline demo. Tokens are hashed into a fixed number of buckets and the
resulting vector is L2-normalized, so: identical text always yields one vector,
and texts that share tokens land closer together under cosine similarity.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, runtime_checkable

import numpy as np

EMBED_DIM = 64
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into a fixed-length vector (§22)."""

    def embed(self, text: str) -> list[float]:
        ...


class MockEmbedder:
    """Deterministic bag-of-hashed-tokens embedder; offline, no network (§22)."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def embed(self, text: str) -> list[float]:
        """Hash tokens into buckets, then L2-normalize to a unit vector."""
        vec = np.zeros(self.dim, dtype=np.float64)
        for token in self._tokens(text):
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dim
            vec[bucket] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec.tolist()
