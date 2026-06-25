"""Memory persistence model (§21 layered memory, §22 retrieval).

A single ``MemoryRecord`` row backs every layer of the hierarchy. The embedding
is stored as a JSON-encoded list of floats so the default SQLite backend stays
dependency-light; swapping to Postgres+pgvector is a column-type change (§22).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class MemoryRecord(SQLModel, table=True):
    """One stored memory across the layered hierarchy (§21)."""

    id: str = Field(default_factory=lambda: new_id("mem"), primary_key=True)
    layer: str = Field(index=True)
    scope_id: str | None = Field(default=None, index=True)  # project/session id
    key: str = Field(index=True)
    text: str = ""
    embedding_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
