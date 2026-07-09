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

from sqlalchemy import Engine
from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow


class WorkflowRunRecord(SQLModel, table=True):
    """One execution of a workflow (SPEC §24).

    Runs are ASYNC: the record is created ``running`` up front, updated in place
    as each step lands, then finalized. ``status`` is a plain string spanning
    running/completed/failed/cancelled/cancelling/interrupted (no enum migration).
    """

    id: str = Field(default_factory=lambda: new_id("wfrun"), primary_key=True)
    workflow_name: str = Field(default="", index=True)
    status: str = "active"
    #: Context spine: the project this run happened in (the active project at
    #: run time). Nullable; old DBs gain the column via _reconcile_additive_columns.
    project_id: str | None = Field(default=None, index=True)
    #: The ordered step plan captured at create time: [{name, agent}]. Lets the
    #: dashboard render the run's shape while it's still executing. Additive
    #: column; old DBs self-heal it as nullable.
    steps_json: str = "[]"
    session_ids_json: str = "[]"
    outputs_json: str = "{}"
    #: The step session currently executing, so the cancel route can stop it
    #: mid-run. Nullable; cleared once the run settles.
    current_session_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


def reconcile_interrupted_runs(engine: Engine) -> int:
    """On boot, mark runs left ``running``/``cancelling`` by a crash/restart as
    ``interrupted`` (none are actually executing on a fresh process) so they
    don't linger as forever-spinning rows. Returns how many were flipped."""
    marked = 0
    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.status.in_(("running", "cancelling"))
                )
            )
        )
        for r in rows:
            r.status = "interrupted"
            r.current_session_id = None
            if r.finished_at is None:
                r.finished_at = utcnow()
            db.add(r)
            marked += 1
        if marked:
            db.commit()
    return marked


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
