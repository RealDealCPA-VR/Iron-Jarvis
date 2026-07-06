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


class MemoryLinkRecord(SQLModel, table=True):
    """A user-curated edge in the memory GRAPH view.

    ``a``/``b`` are opaque node ids ("lesson:<id>", "wm:<layer>:<scope>:<key>",
    "ltm:<source>:<ref>") stored in canonical order (``a < b``) so a pair has
    exactly one row. ``kind``:

    * ``manual``  — the user drew this connection; always shown.
    * ``blocked`` — the user disconnected an automatic similarity edge; the
      pair is suppressed so it never reappears.
    """

    id: str = Field(default_factory=lambda: new_id("mlink"), primary_key=True)
    a: str = Field(index=True)
    b: str = Field(index=True)
    kind: str = "manual"  # manual | blocked
    created_at: datetime = Field(default_factory=utcnow)
