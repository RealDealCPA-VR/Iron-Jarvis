"""Durable store for saved workflow *definitions* (SPEC §24).

:class:`WorkflowStore` persists agent-authored workflows as
:class:`~iron_jarvis.workflows.models.WorkflowRecord` rows so they survive a
daemon restart and surface in the dashboard. ``save`` upserts by ``name`` (the
steps are JSON-encoded and ``updated_at`` is bumped on every overwrite). The
refresh-before-detach pattern mirrors ``SecretsManager.set`` and
``Scheduler.add_task`` so the returned record stays usable after the session
closes.
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import dumps, session_scope
from ..core.ids import utcnow
from .models import WorkflowRecord


class WorkflowStore:
    """Persist / list / fetch / remove saved workflow definitions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(
        self, name: str, steps: list[dict], description: str = ""
    ) -> WorkflowRecord:
        """Upsert the workflow named ``name`` with ``steps`` (JSON) + ``description``.

        Inserts a new row, or updates the existing one in place and bumps
        ``updated_at``. Returns the persisted (refreshed) record.
        """
        steps_json = dumps(list(steps))
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

    def remove(self, name: str) -> bool:
        """Delete a saved workflow by name; returns False if it was absent."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorkflowRecord).where(WorkflowRecord.name == name)
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
