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
import logging
import weakref
from typing import Callable
from datetime import timedelta

from sqlalchemy import Engine, delete as sqla_delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, select

from ..core.db import dumps, session_scope
from ..core.ids import utcnow
from .engine import WorkflowDef, load_workflow
from .models import VERSIONS_MAX, WorkflowRecord, WorkflowVersionRecord

#: v1.505.0 (north-star 5.2): what must go WITH a removed workflow, per
#: database (the platform registers its grant store here: a saved
#: workflow's standing OKs are revoked when it is removed). Keyed weakly by
#: the engine, since a ``WorkflowStore`` is made wherever one is needed.
_ON_REMOVED: "weakref.WeakKeyDictionary[Engine, Callable[[str], None]]" = weakref.WeakKeyDictionary()


def on_removed(engine: Engine, callback: "Callable[[str], None]") -> None:
    """Register what runs after a saved workflow is removed from ``engine``."""
    _ON_REMOVED[engine] = callback


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
        folder: str | None = None,
    ) -> WorkflowRecord:
        """Upsert the workflow named ``name`` with ``steps`` (JSON) + ``description``.

        Release 11 (the intelligence plan): ``folder`` is the folder its tool
        steps work in (None = KEEP it, "" = none), and a save that changes
        the steps, the description or the folder of an existing workflow
        first keeps what it had as an earlier VERSION
        (:class:`WorkflowVersionRecord`), so :meth:`restore` can put it back.

        Inserts a new row, or updates the existing one in place and bumps
        ``updated_at``. Returns the persisted (refreshed) record.

        ``project_id`` carries THREE intents (v1.200.0, the v1.164.0
        remote-agent-token lesson — absent must mean KEEP, never delete):

        * a non-empty string SETS the pin;
        * ``None`` (the default — i.e. the caller said nothing) KEEPS any
          existing pin. The old code deleted the pin row here, so every
          pin-unaware caller (``workflow_create``, the workflow generator)
          silently unpinned a def just by re-saving it;
        * ``""`` (an EXPLICIT empty string) CLEARS the pin. Unlike the token,
          a pin is not a secret no UI can prefill — ``""`` has been the
          public unpin contract since the route shipped (``POST /workflows``
          documents "``\"\"`` explicitly unpins" and the dashboard clears
          exactly this way), so the store now honours the same three-way
          contract instead of needing the route's pre-fetch workaround.
        """
        steps_json = dumps(list(steps))
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorkflowRecord).where(WorkflowRecord.name == name)
            ).first()
            if row is None:
                row = WorkflowRecord(
                    name=name, description=description, steps_json=steps_json,
                    folder=(folder or "").strip(),
                )
            else:
                new_folder = (row.folder or "") if folder is None else folder.strip()
                if (row.steps_json, row.description, row.folder or "") != (steps_json, description, new_folder):
                    keep_version(db, row)
                row.description = description
                row.steps_json = steps_json
                row.folder = new_folder
                row.updated_at = utcnow()
            db.add(row)
            if project_id is not None:  # None = caller said nothing = KEEP
                pin = project_id.strip()
                pin_row = db.get(WorkflowPinRecord, name)
                if pin:
                    if pin_row is None:
                        pin_row = WorkflowPinRecord(name=name, project_id=pin)
                    else:
                        pin_row.project_id = pin
                    db.add(pin_row)
                elif pin_row is not None:
                    db.delete(pin_row)  # explicit "" clears the pin
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

    def versions(self, name: str) -> "list[WorkflowVersionRecord] | None":
        """The earlier versions of the workflow named ``name``, newest
        first (release 11); None when there is no such workflow."""
        with session_scope(self.engine) as db:
            row = db.exec(select(WorkflowRecord).where(WorkflowRecord.name == name)).first()
            if row is None:
                return None
            got = list(db.exec(
                select(WorkflowVersionRecord)
                .where(WorkflowVersionRecord.workflow_id == row.id)
                .order_by(WorkflowVersionRecord.replaced_at.desc())  # type: ignore[attr-defined]
            ))
            for v in got:
                db.expunge(v)
            return got

    def version(self, name: str, version_id: str) -> "WorkflowVersionRecord | None":
        """One earlier version of the workflow named ``name`` (never another
        workflow's), or None."""
        for v in self.versions(name) or []:
            if v.id == version_id:
                return v
        return None

    def restore(self, name: str, version_id: str) -> "WorkflowRecord | None":
        """Put an earlier version back (release 11): its steps, description
        and folder are saved through :meth:`save`, so what it replaces is kept
        as a version in turn (a restore can itself be put back). None when
        the workflow or the version is not there. The caller checks the
        steps first (``workflows/checks.step_problem``, the one save check)."""
        v = self.version(name, version_id)
        if v is None:
            return None
        rec = self.get(name)
        if rec is None:
            return None
        return self.save(name, json.loads(v.steps_json or "[]"), v.description, folder=v.folder or "")

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
                "folder": rec.folder or "",
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
            # Release 11: its earlier versions go with it.
            db.exec(sqla_delete(WorkflowVersionRecord).where(WorkflowVersionRecord.workflow_id == row.id))  # type: ignore[call-overload]
            db.delete(row)
            db.commit()
        hook = _ON_REMOVED.get(self.engine)
        if hook is not None:
            try:
                hook(name)
            except Exception:  # noqa: BLE001 — best-effort: the removal stands
                logging.getLogger(__name__).warning(
                    "workflows: the removal hook failed for %r", name, exc_info=True)
        return True


def keep_version(db, row: WorkflowRecord) -> None:
    """Keep what ``row`` holds now as an earlier version (release 11), inside
    the caller's transaction, and drop the oldest past
    :data:`~iron_jarvis.workflows.models.VERSIONS_MAX`."""
    db.add(WorkflowVersionRecord(
        workflow_id=row.id, steps_json=row.steps_json or "[]", description=row.description or "",
        folder=row.folder or "",
    ))
    db.flush()
    ids = list(db.exec(
        select(WorkflowVersionRecord.id)
        .where(WorkflowVersionRecord.workflow_id == row.id)
        .order_by(WorkflowVersionRecord.replaced_at.desc(), WorkflowVersionRecord.id.desc())  # type: ignore[attr-defined]
    ))
    old = [str(i) for i in ids[VERSIONS_MAX:]]
    if old:
        db.exec(sqla_delete(WorkflowVersionRecord).where(WorkflowVersionRecord.id.in_(old)))  # type: ignore[attr-defined,call-overload]


#: Run statuses with nothing left to execute. ``waiting`` (parked at a human
#: gate) and the live trio (running/cancelling/resuming) are never pruned no
#: matter how old: deleting a parked run would answer the user's question with
#: a 404.
FINISHED_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    "completed_with_failures",  # v1.446.0 (review item 22)
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
    "completed_with_failures",  # v1.446.0 (review item 22)
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
    from .models import WorkflowRunRecord, real_runs

    keep = max(0, int(keep))
    with session_scope(engine) as db:
        # Leap 06-b review: rehearsals' runs never take a real run's place in
        # the window: the two are kept apart, ``keep`` of each.
        stale_ids = []
        for kind in (real_runs(), ~real_runs()):
            stale_ids += list(
                db.exec(
                    select(WorkflowRunRecord.id)  # type: ignore[call-overload]
                    .where(WorkflowRunRecord.status.in_(PRUNABLE_RUN_STATUSES), kind)  # type: ignore[attr-defined]
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
