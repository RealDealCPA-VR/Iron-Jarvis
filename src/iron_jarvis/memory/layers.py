"""Layered memory manager (§21).

``MemoryLayers`` is the front door to the four-tier hierarchy
(session < project < user < org). It owns a ``SqliteVectorRetriever`` for
similarity search and provides upsert-by-key reads/writes on top of it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .embeddings import Embedder, MockEmbedder
from .models import MemoryRecord
from .retrieval import SqliteVectorRetriever

if TYPE_CHECKING:
    from ..core.config import Config


class MemoryLayers:
    """Read/write/search across the layered memory hierarchy (§21, §22)."""

    LAYERS = ("session", "project", "user", "org")

    def __init__(
        self,
        engine: Engine,
        embedder: Embedder | None = None,
        config: "Config | None" = None,
    ) -> None:
        self.engine = engine
        self.embedder = embedder or MockEmbedder()
        self.config = config
        self.retriever = SqliteVectorRetriever(engine, self.embedder)

    def _check_layer(self, layer: str) -> None:
        if layer not in self.LAYERS:
            raise ValueError(
                f"unknown memory layer '{layer}'; expected one of {self.LAYERS}"
            )

    def write(
        self, layer: str, key: str, text: str, scope_id: str | None = None
    ) -> MemoryRecord:
        """Upsert by (layer, key, scope_id): update text in place if present, else insert."""
        self._check_layer(layer)
        existing = self._find(layer, key, scope_id)
        if existing is not None:
            with session_scope(self.engine) as db:
                row = db.get(MemoryRecord, existing.id)
                row.text = text
                row.embedding_json = json.dumps(self.embedder.embed(text))
                row.created_at = utcnow()
                db.add(row)
                db.commit()
                db.refresh(row)  # re-load expired attrs before the session closes
                return row
        # new record -> let the retriever embed + persist it
        record = MemoryRecord(layer=layer, scope_id=scope_id, key=key, text=text)
        return self.retriever.add(record)

    def read(self, layer: str, key: str, scope_id: str | None = None) -> str | None:
        """Return the stored text for (layer, key, scope_id) or None if absent."""
        record = self._find(layer, key, scope_id)
        return record.text if record is not None else None

    def list(self, layer: str, scope_id: str | None = None) -> list[MemoryRecord]:
        """List records in a layer; scope_id filters by scope when given."""
        stmt = select(MemoryRecord).where(MemoryRecord.layer == layer)
        if scope_id is not None:
            stmt = stmt.where(MemoryRecord.scope_id == scope_id)
        with session_scope(self.engine) as db:
            return list(db.exec(stmt))

    def search(
        self,
        query: str,
        k: int = 5,
        layer: str | None = None,
        scope_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """Cosine-similarity search across (optionally) a single layer/scope (§22)."""
        return self.retriever.search(query, k=k, layer=layer, scope_id=scope_id)

    def _find(
        self, layer: str, key: str, scope_id: str | None
    ) -> MemoryRecord | None:
        conds = [MemoryRecord.layer == layer, MemoryRecord.key == key]
        if scope_id is None:
            conds.append(MemoryRecord.scope_id.is_(None))
        else:
            conds.append(MemoryRecord.scope_id == scope_id)
        with session_scope(self.engine) as db:
            return db.exec(select(MemoryRecord).where(*conds)).first()
