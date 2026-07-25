"""Code-artifact persistence model (v1.95.0).

One row per script an agent wrote and ran. The SOURCE is stored inline (scripts
are small — the tool exists for "a few dozen lines when no tool fits"), so a
re-run needs nothing from the long-deleted session workspace.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class CodeArtifactRecord(SQLModel, table=True):
    """A saved, re-runnable script (v1.95.0)."""

    id: str = Field(default_factory=lambda: new_id("code"), primary_key=True)
    name: str = Field(default="", index=True)
    language: str = "python"
    #: The script itself. Inline rather than a path: the workspace it was born
    #: in is deleted with its session, so a path would dangle within minutes.
    source: str = ""
    description: str = ""
    #: "run_code" (an agent wrote it) or "manual" (saved through the API).
    origin: str = "run_code"
    #: Provenance + context spine. Both survive the session/project going away;
    #: they are labels for grouping, never dereferenced for execution.
    session_id: str | None = Field(default=None, index=True)
    project_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    #: Outcome of the most recent run (the original one, until re-run).
    run_count: int = 0
    last_run_at: datetime | None = None
    last_exit_code: int | None = None
    #: Capped in the store — a runaway script must not bloat the DB.
    last_output: str = ""
