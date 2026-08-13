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
from datetime import timedelta

from sqlalchemy import Engine, delete as sqla_delete
from sqlalchemy.exc import IntegrityError
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

    def patch(
        self,
        name: str,
        new_name: str | None = None,
        description: str | None = None,
    ) -> WorkflowRecord | None:
        """Rename and/or re-describe the def named ``name`` — steps untouched
        (v1.170.0). ``None`` leaves a field alone. Returns ``None`` when the
        def is absent; raises ``ValueError`` when ``new_name`` is already
        taken by ANOTHER def (renaming onto yourself is a no-op, not a clash).

        A rename MOVES the project-pin sidecar row (its primary key IS the
        name): delete-then-insert inside the same transaction, so the pin can
        neither be dropped nor orphaned under the old name. Reflex rules that
        bind to this workflow BY NAME (``ReflexRule.target``, resolved via
        ``load_def`` at fire time) are retargeted in that same transaction —
        a rename that left them behind would orphan every webhook/comm trigger
        SILENTLY (the router logs ``no saved workflow '<old>'`` and nothing
        runs), the exact orphan class the v1.164.0 name-immutability lesson
        exists for.
        """
        target = (new_name or "").strip()
        if target:
            # Self-heal like __init__'s pin-table create: the reflex table may
            # not exist on a DB predating the Reflex Loop. DDL runs BEFORE the
            # session opens so it can never contend with our own transaction.
            try:
                from ..reflex.models import ReflexRule

                ReflexRule.__table__.create(self.engine, checkfirst=True)
            except Exception:  # noqa: BLE001 — already exists / created concurrently
                pass
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorkflowRecord).where(WorkflowRecord.name == name)
            ).first()
            if row is None:
                return None
            changed = False
            if target and target != name:
                clash = db.exec(
                    select(WorkflowRecord).where(WorkflowRecord.name == target)
                ).first()
                if clash is not None:
                    raise ValueError(f"a workflow named '{target}' already exists")
                row.name = target
                pin_row = db.get(WorkflowPinRecord, name)
                if pin_row is not None:
                    pinned_project = pin_row.project_id
                    db.delete(pin_row)
                    db.flush()  # the PK is the name — clear the old row first
                    db.add(WorkflowPinRecord(name=target, project_id=pinned_project))
                # Retarget name-bound reflex rules (action == "workflow" only —
                # a session/remote_agent rule whose target happens to share the
                # name is a DIFFERENT binding and must not move).
                from ..reflex.models import ReflexRule

                for rule in db.exec(
                    select(ReflexRule).where(
                        ReflexRule.action == "workflow",
                        ReflexRule.target == name,
                    )
                ):
                    rule.target = target
                    db.add(rule)
                changed = True
            if description is not None and description != row.description:
                row.description = description
                changed = True
            if changed:
                row.updated_at = utcnow()
                db.add(row)
                try:
                    db.commit()
                except IntegrityError:
                    # Check-then-write race: a concurrent rename/save claimed
                    # ``target`` between our clash SELECT and this commit, so
                    # the UNIQUE constraint fired. Surface the SAME ValueError
                    # the pre-check raises — the route's 409 mapping then
                    # covers the race instead of leaking a raw 500.
                    db.rollback()
                    raise ValueError(
                        f"a workflow named '{target or name}' already exists"
                    ) from None
            db.refresh(row)  # un-expire attrs so the detached record stays usable
            return row

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


#: Run statuses with nothing left to execute. ``waiting`` (parked at a human
#: gate) and the live trio (running/cancelling/resuming) are never pruned no
#: matter how old: deleting a parked run would answer the user's question with
#: a 404.
FINISHED_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)

#: The subset the keep-window prune may touch. ``interrupted`` is finished in
#: the "nothing is executing" sense but it is RESUMABLE (contract 4 renders a
#: Resume button for it) — pruning one by count turns that button into a 404
#: and the partial progress becomes unrecoverable. Interrupted rows are pruned
#: ONLY by age (:data:`INTERRUPTED_PRUNE_AFTER_DAYS`), when they are
#: realistically abandoned rather than merely past a busy backlog's window.
PRUNABLE_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    "failed",
    "cancelled",
)

#: How long an ``interrupted`` run stays resumable before pruning may take it.
INTERRUPTED_PRUNE_AFTER_DAYS: int = 14


def prune_runs(
    engine: Engine, keep: int = 500, interrupted_after_days: int = INTERRUPTED_PRUNE_AFTER_DAYS
) -> int:
    """Delete the OLDEST finished workflow runs beyond the newest ``keep``
    (v1.170.0). Returns how many rows were deleted.

    Only :data:`PRUNABLE_RUN_STATUSES` rows are keep-window candidates — a run
    that is running, parked (``waiting``), or mid-cancel/mid-resume survives
    regardless of age, and an ``interrupted`` (resumable) run survives the
    window too, falling only to the ``interrupted_after_days`` age threshold.
    ``keep`` counts prunable finished runs (newest-first by ``started_at``).

    SELECTS IDS ONLY, DELETES IN BULK: a stale row's ``steps_json``/
    ``outputs_json`` blobs can be hundreds of KB each, and this runs against
    the biggest backlogs by definition — loading full rows put every blob in
    memory at once. NOTE for the lifespan call site: this function is
    SYNCHRONOUS; wire it through ``asyncio.to_thread``, never directly on the
    event loop (the v1.153.1 "Daemon offline" failure mode).
    """
    from .models import WorkflowRunRecord

    keep = max(0, int(keep))
    with session_scope(engine) as db:
        stale_ids = list(
            db.exec(
                select(WorkflowRunRecord.id)  # type: ignore[call-overload]
                .where(WorkflowRunRecord.status.in_(PRUNABLE_RUN_STATUSES))  # type: ignore[attr-defined]
                .order_by(WorkflowRunRecord.started_at.desc())  # type: ignore[attr-defined]
                .offset(keep)
            )
        )
        cutoff = utcnow() - timedelta(days=max(0, int(interrupted_after_days)))
        stale_ids += list(
            db.exec(
                select(WorkflowRunRecord.id).where(  # type: ignore[call-overload]
                    WorkflowRunRecord.status == "interrupted",
                    WorkflowRunRecord.started_at < cutoff,  # type: ignore[arg-type]
                )
            )
        )
        if not stale_ids:
            return 0
        db.exec(
            sqla_delete(WorkflowRunRecord).where(
                WorkflowRunRecord.id.in_(stale_ids)  # type: ignore[attr-defined]
            )
        )
        db.commit()
        return len(stale_ids)
