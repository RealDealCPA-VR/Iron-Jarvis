"""Durable store for saved workflow *definitions* (SPEC §24).

:class:`WorkflowStore` persists agent-authored workflows as
:class:`~iron_jarvis.workflows.models.WorkflowRecord` rows so they survive a
daemon restart and surface in the dashboard. ``save`` upserts by ``name`` (the
steps are JSON-encoded and ``updated_at`` is bumped on every overwrite). The
refresh-before-detach pattern mirrors ``SecretsManager.set`` and
``Scheduler.add_task`` so the returned record stays usable after the session
closes.

A def may carry an optional EXPLICIT project pin ("run this workflow inside
project X") persisted as a :class:`WorkflowPinRecord` sidecar row — a missing
row simply means unpinned, so old DBs and defs saved before pinning existed
keep loading unchanged, with no migration.
"""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlmodel import Field, SQLModel, select

from ..core.db import dumps, session_scope
from ..core.ids import utcnow
from .engine import WorkflowDef, load_workflow
from .models import WorkflowRecord


class WorkflowPinRecord(SQLModel, table=True):
    """The optional per-def project pin (context spine).

    Kept as its own tiny row keyed by workflow name — NOT a column on
    ``WorkflowRecord`` — so the def schema stays untouched: no row = unpinned.
    A pin never outlives its def (``remove`` deletes both).
    """

    name: str = Field(primary_key=True)
    project_id: str = ""


class WorkflowStore:
    """Persist / list / fetch / remove saved workflow definitions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # Self-heal: the pin table is defined HERE (not workflows/models.py), so
        # it may not have been on SQLModel.metadata when init_db's create_all
        # ran — create it on first use (checkfirst = idempotent), mirroring
        # RemoteAgentRegistry.
        try:
            WorkflowPinRecord.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — already exists / created concurrently
            pass

    def save(
        self,
        name: str,
        steps: list[dict],
        description: str = "",
        project_id: str | None = None,
    ) -> WorkflowRecord:
        """Upsert the workflow named ``name`` with ``steps`` (JSON) + ``description``.

        Inserts a new row, or updates the existing one in place and bumps
        ``updated_at``. ``project_id`` is the optional explicit project pin;
        each save rewrites the WHOLE def, so omitting it unpins (a stale pin
        silently grounding runs in the wrong project would be worse than
        re-stating it). Returns the persisted (refreshed) record.
        """
        steps_json = dumps(list(steps))
        pin = (project_id or "").strip()
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorkflowRecord).where(WorkflowRecord.name == name)
            ).first()
            if row is None:
                row = WorkflowRecord(
                    name=name, description=description, steps_json=steps_json
                )
            else:
                row.description = description
                row.steps_json = steps_json
                row.updated_at = utcnow()
            db.add(row)
            pin_row = db.get(WorkflowPinRecord, name)
            if pin:
                if pin_row is None:
                    pin_row = WorkflowPinRecord(name=name, project_id=pin)
                else:
                    pin_row.project_id = pin
                db.add(pin_row)
            elif pin_row is not None:
                db.delete(pin_row)  # unpinned save clears any prior pin
            db.commit()
            db.refresh(row)  # un-expire attrs so the detached record stays usable
            return row

    def list(self) -> list[WorkflowRecord]:
        """Return every saved workflow, oldest first."""
        with session_scope(self.engine) as db:
            return list(
                db.exec(select(WorkflowRecord).order_by(WorkflowRecord.created_at))
            )

    def get(self, name: str) -> WorkflowRecord | None:
        """Return the saved workflow named ``name`` (or None)."""
        with session_scope(self.engine) as db:
            return db.exec(
                select(WorkflowRecord).where(WorkflowRecord.name == name)
            ).first()

    def get_project_id(self, name: str) -> str | None:
        """Return the project a saved workflow is pinned to, or None (= unpinned
        — including every def saved before pinning existed)."""
        with session_scope(self.engine) as db:
            row = db.get(WorkflowPinRecord, name)
        if row is None:
            return None
        return row.project_id or None

    def pins(self) -> dict[str, str]:
        """Map workflow name -> pinned project id (unpinned defs absent), so a
        list view can annotate every def in one query."""
        with session_scope(self.engine) as db:
            rows = db.exec(select(WorkflowPinRecord)).all()
        return {r.name: r.project_id for r in rows if r.project_id}

    def load_def(self, name: str) -> WorkflowDef | None:
        """Return the saved workflow as a runnable :class:`WorkflowDef` — with
        its project pin applied — or None. The ONE place stored-record -> def
        composition lives, so every runner picks the pin up for free."""
        rec = self.get(name)
        if rec is None:
            return None
        return load_workflow(
            {
                "name": rec.name,
                "description": rec.description,
                "steps": json.loads(rec.steps_json or "[]"),
                "project_id": self.get_project_id(rec.name),
            }
        )

    def remove(self, name: str) -> bool:
        """Delete a saved workflow by name; returns False if it was absent."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorkflowRecord).where(WorkflowRecord.name == name)
            ).first()
            if row is None:
                return False
            pin_row = db.get(WorkflowPinRecord, name)
            if pin_row is not None:
                db.delete(pin_row)  # a pin never outlives its def
            db.delete(row)
            db.commit()
            return True
