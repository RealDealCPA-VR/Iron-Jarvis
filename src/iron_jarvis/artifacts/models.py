"""Artifact persistence model (SPEC §26).

``ArtifactRecord`` indexes each on-disk version of a named artifact. It is a
plain SQLModel table; auto-creates via ``init_db`` when this module is imported
before ``init_db`` runs (the orchestrator handles import order).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class ArtifactRecord(SQLModel, table=True):
    """One stored version of a named artifact (SPEC §26)."""

    id: str = Field(default_factory=lambda: new_id("art"), primary_key=True)
    name: str = Field(index=True)
    version: int = 1
    kind: str = "file"
    path: str = ""
    session_id: str | None = Field(default=None, index=True)
    size: int = 0
    created_at: datetime = Field(default_factory=utcnow)
