"""The work record's one table (v1.491.0, vision plan leap 3, Phase 1).

One row per PIECE OF WORK that has a folder: a chat, a coding session in
Chat › Code, a coding session in a real tool (Build › Code), an agent run.
Every column is copied from what Iron Jarvis already stores (the thread, the
session row, the undo journal); nothing here is model-written and no file on
disk is read to fill it. The row is a DERIVED index: deleting it loses
nothing, and :mod:`iron_jarvis.workmap.index` rebuilds it from its sources.

Registered in ``core.db._LATE_MODEL_MODULES`` (a lazily imported table is
otherwise created on fresh test databases and on no real install).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import utcnow

#: The kinds of work a row can be.
KIND_CHAT = "chat"
KIND_CODE = "code"            # Chat › Code (a Build thread)
KIND_TOOL_CODE = "tool_code"  # Build › Code (a real coding tool)
KIND_RUN = "run"              # an agent run
KINDS = (KIND_CHAT, KIND_CODE, KIND_TOOL_CODE, KIND_RUN)


class WorkEntry(SQLModel, table=True):
    """One piece of work, where it happened and what it left."""

    __tablename__ = "workentry"

    #: ``<kind>:<ref>``.
    id: str = Field(primary_key=True)
    kind: str = Field(default="", index=True)
    #: The source row's id (thread id, hosted session id, session id).
    ref: str = ""
    title: str = ""
    #: The folder as the user would read it.
    folder: str = ""
    #: The folder normalised for matching (``keys.folder_key``).
    folder_key: str = Field(default="", index=True)
    project_id: str | None = Field(default=None, index=True)
    #: Build › Code: the tool's name ("Claude Code"). "" otherwise.
    tool: str = ""
    #: An agent run's origin ("schedule:nightly"). "" otherwise.
    origin: str = ""
    #: completed | failed | cancelled | … ("" = not known / still open).
    outcome: str = ""
    #: The work was switched to Local only: its words stay off any model
    #: that leaves this network.
    local_only: bool = False
    #: The work may have run on a model on this network with no Local only
    #: switch to say so (Build › Code on Pi / OpenCode): kept home the same
    #: way (the v1.414.0 rule).
    keep_home: bool = False
    #: The user archived it: still on the Memory page, never told to a chat.
    archived: bool = False
    #: ``[{"path": <absolute path>, "how": "made" | "changed" | "wrote"}]``,
    #: newest first, at most ``collect.FILES_MAX``.
    files_json: str = "[]"
    files_total: int = 0
    #: A run's own summary line, cut. "" for the other kinds.
    note: str = ""
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    #: What the source row looked like when this row was built; a refresh
    #: rebuilds the row only when it differs.
    stamp: str = ""


__all__ = ["KINDS", "KIND_CHAT", "KIND_CODE", "KIND_RUN", "KIND_TOOL_CODE", "WorkEntry"]
