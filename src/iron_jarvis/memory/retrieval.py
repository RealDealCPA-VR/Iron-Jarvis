"""Vector retrieval (§22 retrieval pipeline).

``Retriever`` is the storage-agnostic contract. ``SqliteVectorRetriever`` is the
default backend: it keeps each embedding inline on the ``MemoryRecord`` row and
ranks candidates by numpy cosine similarity against the query embedding. Moving
to pgvector swaps this class without touching the layer manager.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import numpy as np
from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from .embeddings import Embedder
from .models import MemoryRecord


#: Safety cap on the candidate rows a single recall scores — high enough that a
#: real (layer,scope) never truncates in practice (a scope reaching this takes far
#: longer than "weeks"), low enough to bound memory/parse on a pathological store.
#: Cosine is VECTORIZED (one numpy matmul), so scoring this many stays fast.
_MAX_RECALL_CANDIDATES = 10000


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class Retriever(ABC):
    """Storage-agnostic memory index (§22)."""

    @abstractmethod
    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Embed (if needed) and persist a record."""
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        k: int = 5,
        layer: str | None = None,
        scope_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """Return the top-k (record, score) pairs sorted by score desc."""
        ...


class SqliteVectorRetriever(Retriever):
    """Default retriever: inline embeddings + numpy cosine ranking (§22)."""

    def __init__(self, engine: Engine, embedder: Embedder) -> None:
        self.engine = engine
        self.embedder = embedder

    def add(self, record: MemoryRecord) -> MemoryRecord:
        if record.embedding_json in ("", "[]"):
            record.embedding_json = json.dumps(self.embedder.embed(record.text))
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)  # re-load expired attrs before the session closes
        return record

    def search(
        self,
        query: str,
        k: int = 5,
        layer: str | None = None,
        scope_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        q = np.asarray(self.embedder.embed(query), dtype=np.float64)
        stmt = select(MemoryRecord)
        if layer is not None:
            stmt = stmt.where(MemoryRecord.layer == layer)
        if scope_id is not None:
            stmt = stmt.where(MemoryRecord.scope_id == scope_id)
        stmt = stmt.order_by(MemoryRecord.created_at.desc()).limit(_MAX_RECALL_CANDIDATES)
        with session_scope(self.engine) as db:
            rows = list(db.exec(stmt))
        qn = float(np.linalg.norm(q))
        if not rows or qn == 0.0:
            return []
        # Vectorized cosine: stack the candidate embeddings and do ONE matmul rather
        # than a Python per-row loop (the measured hot cost) — keeps recall fast
        # while still scoring the whole candidate set for correctness.
        keep: list[MemoryRecord] = []
        vecs: list[np.ndarray] = []
        for row in rows:
            v = np.asarray(json.loads(row.embedding_json or "[]"), dtype=np.float64)
            if v.size == q.size:
                keep.append(row)
                vecs.append(v)
        if not vecs:
            return []
        matrix = np.vstack(vecs)
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0.0] = 1.0
        sims = (matrix @ q) / (norms * qn)
        order = np.argsort(sims)[::-1][: max(0, k)]
        return [(keep[i], float(sims[i])) for i in order]
