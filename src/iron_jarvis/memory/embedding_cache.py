"""Persistent embedding cache (§22 retrieval — Total Recall).

Embeddings are expensive to recompute (especially over a real local model), so
this module persists every computed vector keyed by ``(source, chunk_id, model)``
in a single :class:`EmbeddingRecord` table. A cached row is reused *only* when its
stored ``text_hash`` and ``model`` still match, so an embedding is recomputed
exactly when the underlying text or the embedding model changes — making repeated
indexing incremental and survive a daemon restart.

Importing this module registers ``EmbeddingRecord`` on ``SQLModel.metadata`` so
the table auto-creates via ``init_db`` (it is imported by ``memory.embeddings``,
which the platform loads before ``init_db`` runs). Dependency-free: it reuses the
shared ``core/db`` SQLite engine only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import Engine, UniqueConstraint, delete, func
from sqlmodel import Field, Session, SQLModel, select

from ..core.ids import new_uid, utcnow

#: Hard cap on cached rows. Content-addressed rows from edited chunks + ephemeral
#: query embeddings would otherwise grow without bound, so we keep the newest
#: MAX_ROWS by created_at and evict the rest (checked every _PRUNE_EVERY writes).
MAX_ROWS = 50_000
_PRUNE_EVERY = 1000


def text_hash(text: str) -> str:
    """Stable content hash used to detect when a chunk's text has changed."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingRecord(SQLModel, table=True):
    """One cached embedding vector for a chunk of text under a given model."""

    __table_args__ = (
        UniqueConstraint("source", "chunk_id", "model", name="uq_embedding_chunk"),
    )

    id: str = Field(default_factory=lambda: new_uid("emb"), primary_key=True)
    source: str = Field(default="", index=True)  # logical owner (e.g. a root/file)
    chunk_id: str = Field(default="", index=True)  # stable id within the source
    text_hash: str = Field(default="", index=True)  # sha256 of the embedded text
    model: str = Field(default="", index=True)  # embedder identity (dims differ!)
    vector_json: str = "[]"  # JSON list[float]
    created_at: datetime = Field(default_factory=utcnow)


class EmbeddingStore:
    """Read/write the persistent embedding cache against the shared engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._put_count = 0

    def _prune(self, db: Session) -> None:
        """Evict oldest rows beyond MAX_ROWS so the cache can't grow unbounded."""
        total = db.scalar(select(func.count()).select_from(EmbeddingRecord)) or 0
        if total <= MAX_ROWS:
            return
        excess = total - MAX_ROWS
        old_ids = list(
            db.exec(
                select(EmbeddingRecord.id)
                .order_by(EmbeddingRecord.created_at)
                .limit(excess)
            )
        )
        if old_ids:
            db.exec(delete(EmbeddingRecord).where(EmbeddingRecord.id.in_(old_ids)))
            db.commit()

    @staticmethod
    def _chunk_id(text: str, chunk_id: str | None) -> str:
        # When no explicit chunk id is given (the plain ``embed(text)`` path), key
        # the row by the text hash itself so identical text dedupes to one row.
        return chunk_id if chunk_id is not None else text_hash(text)

    def get(
        self, text: str, *, model: str, source: str = "", chunk_id: str | None = None
    ) -> list[float] | None:
        """Return the cached vector, or None when missing / text or model changed."""
        cid = self._chunk_id(text, chunk_id)
        h = text_hash(text)
        with Session(self.engine) as db:
            row = db.exec(
                select(EmbeddingRecord).where(
                    EmbeddingRecord.source == source,
                    EmbeddingRecord.chunk_id == cid,
                    EmbeddingRecord.model == model,
                )
            ).first()
            if row is not None and row.text_hash == h:
                try:
                    return [float(x) for x in json.loads(row.vector_json)]
                except (ValueError, TypeError):
                    return None
        return None

    def put(
        self,
        text: str,
        vector: list[float],
        *,
        model: str,
        source: str = "",
        chunk_id: str | None = None,
    ) -> None:
        """Upsert the cached vector for ``(source, chunk_id, model)``."""
        cid = self._chunk_id(text, chunk_id)
        h = text_hash(text)
        payload = json.dumps([float(x) for x in vector])
        def _find(db):
            return db.exec(
                select(EmbeddingRecord).where(
                    EmbeddingRecord.source == source,
                    EmbeddingRecord.chunk_id == cid,
                    EmbeddingRecord.model == model,
                )
            ).first()

        from sqlalchemy.exc import IntegrityError

        with Session(self.engine) as db:
            row = _find(db)
            if row is None:
                row = EmbeddingRecord(
                    source=source, chunk_id=cid, model=model, text_hash=h, vector_json=payload
                )
            else:
                row.text_hash = h
                row.vector_json = payload
                row.created_at = utcnow()
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                # A concurrent first-write committed the same unique key between our
                # SELECT and INSERT — converge by updating the now-existing row.
                db.rollback()
                existing = _find(db)
                if existing is not None:
                    existing.text_hash = h
                    existing.vector_json = payload
                    existing.created_at = utcnow()
                    db.add(existing)
                    db.commit()
            self._put_count += 1
            if self._put_count % _PRUNE_EVERY == 0:
                try:
                    self._prune(db)
                except Exception:  # GC is best-effort; never break embedding
                    pass

    def get_or_compute(
        self,
        embed_fn: Callable[[str], list[float]],
        text: str,
        *,
        model: str,
        source: str = "",
        chunk_id: str | None = None,
    ) -> list[float]:
        """Cache-through: return the cached vector or compute, store, and return it.

        A cache read/write failure must never break embedding — on any cache error
        we fall back to computing the vector directly.
        """
        try:
            cached = self.get(text, model=model, source=source, chunk_id=chunk_id)
        except Exception:
            cached = None
        if cached is not None:
            return cached
        vec = embed_fn(text)
        try:
            self.put(text, vec, model=model, source=source, chunk_id=chunk_id)
        except Exception:
            pass  # caching is best-effort; the live vector is what matters
        return vec
