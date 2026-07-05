"""Workflow persistence model (SPEC §24).

``WorkflowRunRecord`` records one execution of a named workflow: which sessions
it spawned and what each produced. ``WorkflowRecord`` is the durable *definition*
of a saved workflow (its ordered steps) so an agent can author a workflow that
persists and the user sees it in the dashboard. Both are plain SQLModel tables;
they auto-create via ``init_db`` when this module is imported before ``init_db``
runs.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class WorkflowRunRecord(SQLModel, table=True):
    """One execution of a workflow (SPEC §24)."""

    id: str = Field(default_factory=lambda: new_id("wfrun"), primary_key=True)
    workflow_name: str = Field(default="", index=True)
    status: str = "active"
    #: Context spine: the project this run happened in (the active project at
    #: run time). Nullable; old DBs gain the column via _reconcile_additive_columns.
    project_id: str | None = Field(default=None, index=True)
    session_ids_json: str = "[]"
    outputs_json: str = "{}"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class WorkflowRecord(SQLModel, table=True):
    """A saved workflow *definition* — an agent-authored, persisted process.

    Stores the ordered steps as JSON so the engine can re-load and run it, and
    so the dashboard can list every workflow the user (or an agent) created.
    Upserted by ``name`` via :class:`~iron_jarvis.workflows.store.WorkflowStore`.
    """

    id: str = Field(default_factory=lambda: new_id("wf"), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    steps_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
