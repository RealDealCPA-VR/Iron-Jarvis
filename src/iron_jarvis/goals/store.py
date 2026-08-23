"""Durable CRUD + lifecycle guards for :class:`GoalContractRecord` rows.

Pure persistence — no execution, no orchestrator (the ReflexStore shape): the
:class:`~iron_jarvis.goals.engine.GoalEngine` owns RUNNING a goal; this store
owns finding, validating and transitioning them, so it is cheap to build (just
an engine) and safe to expose on the platform.

Contracts:

* **Loads never raise.** ``get``/``list`` degrade to ``None``/``[]`` — a goal
  listing must not take a route down with it.
* **Writes validate.** :meth:`create` refuses deny-floor grants, an unbounded
  budget without ``unlimited: true``, and a ``checks`` verifier that checks
  nothing (see ``models.py`` for why each rule exists). ``ValueError`` with
  the reason — the route maps it to a 400, the engine relays it honestly.
* **Transitions are guarded.** :meth:`transition` follows
  :data:`~iron_jarvis.goals.models.GOAL_TRANSITIONS`; a satisfied/stopped/
  tripped goal comes back ONLY through the explicit :meth:`reopen`, which also
  clears the breaker.
* **Spend accumulation is atomic-enough.** :meth:`add_spend` is one
  read-modify-write inside one transaction, and the GoalEngine is the single
  writer of ``spent_json`` by design — two concurrent iterations of the same
  goal do not exist (an iteration is awaited end-to-end). The write also
  carries the ACCOUNTED session id in the same transaction
  (``spent_json.last_session_id``), which is what makes rehydration
  double-bill-proof: "was this session billed?" and "how much is billed?"
  cannot disagree, because they are one row written together.
* **The store owns the SCHEDULE ROW's lifecycle** (D2). A goal whose
  ``schedule`` is set is a promise ("daily at 09:00"), and every state door
  runs through this store — ``create``/``transition``/``reopen``/
  ``record_failure``/``remove`` — so this is the one place that promise can be
  kept without drifting: the scheduler row (``kind="goal"``, payload
  ``{goal_id}``, name ``goal:<id>``) is created at :meth:`create`, ENABLED
  IFF the goal is ``active``, and deleted with the goal. There is NO schedule
  EDIT path in G1 (there is no edit route): changing a goal's cadence means
  delete + recreate, stated here so a future edit route knows it must also
  move the scheduler row.
"""

from __future__ import annotations

import json
import weakref
from datetime import timedelta
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .models import (
    BREAKER_MAX_FAILURES,
    BREAKER_WINDOW_S,
    GOAL_STATES,
    GOAL_TRANSITIONS,
    GoalContractRecord,
    budget_violation,
    grants_violation,
)

log = get_logger("goals.store")

#: Engines whose table has been ensured (idempotent DDL — this only skips the
#: re-run). A WeakSet of the ENGINES, not ``id(engine)``: ids are recycled after
#: a GC, which is routine in a test run building a platform per test (the
#: capability-store lesson, copied verbatim).
_ENSURED: "weakref.WeakSet[Any]" = weakref.WeakSet()


def _ensure_table(engine) -> None:
    """Create ``goalcontract`` if missing. Never raises.

    Belt-and-braces beside ``core.db._LATE_MODEL_MODULES`` (which the
    coordinator registers ``goals.models`` into): boot's ``create_all`` builds
    the table AND the additive-column reconciler can see it (the v1.151.2
    lesson). This covers a store constructed against an engine that never went
    through ``init_db`` at all — a bare unit-test platform.
    """
    try:
        if engine in _ENSURED:
            return
    except TypeError:  # pragma: no cover — a stub engine that is not hashable
        pass
    try:
        GoalContractRecord.__table__.create(engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — a DDL hiccup must never break a request
        log.exception("could not ensure the goal table")
    try:
        _ENSURED.add(engine)
    except TypeError:  # pragma: no cover — not weak-referenceable; re-run is safe
        pass


def verifier_violation(verifier: Any) -> str:
    """Why this verifier may never be stored, or ``""``.

    ``kind:"checks"`` must carry at least one check that COERCES to a non-empty
    workflows ``expect:`` shape — the coercion is IMPORTED from
    ``workflows.engine._coerce_expect`` (lazily, to keep table registration
    light), so the goal vocabulary and the workflow vocabulary cannot drift.
    Without this rule, ``{"kind": "checks", "checks": []}`` would auto-satisfy
    vacuously on the first completed session — a verifier that verifies
    nothing is worse than ``manual``, because it LOOKS like one that does.
    """
    if verifier is None:
        return ""  # defaults to manual
    if not isinstance(verifier, dict):
        return "verifier must be an object like {kind: 'checks'|'manual', checks?: [...]}"
    kind = str(verifier.get("kind") or "manual").strip().lower()
    if kind not in ("checks", "manual"):
        return f"unknown verifier kind {kind!r}; expected 'checks' or 'manual'"
    if kind == "manual":
        return ""
    checks = verifier.get("checks")
    if not isinstance(checks, list) or not checks:
        return (
            "a 'checks' verifier needs at least one check — an empty checklist "
            "would declare the goal satisfied without verifying anything; use "
            "kind 'manual' if only you can judge it"
        )
    from ..workflows.engine import _coerce_expect  # lazy — see the docstring

    for i, raw in enumerate(checks):
        if not _coerce_expect(raw):
            return (
                f"check {i + 1} has no usable expectation — each check is a "
                "workflows expect: shape, e.g. {files: ['report.md']} or "
                "{summary_contains: ['filed']}"
            )
    return ""


class GoalStore:
    """See the module docstring. ``scheduler`` is the platform's
    ``scheduling.Scheduler`` (or ``None`` for a bare read-only store): the
    store needs it because EVERY state door lives here, and the schedule row
    must track the state no matter which door was used (the resume route goes
    straight to ``transition``/``reopen``, not through the engine).

    ``scheduler`` may also be a ZERO-ARG CALLABLE returning the scheduler —
    resolved at USE time, never at construction, because ``build_platform``
    constructs the GoalEngine (and therefore this store) two lines BEFORE it
    attaches ``platform.scheduler``; an ``__init__``-time capture would bind
    ``None`` forever and quietly kill the whole schedule lifecycle on every
    real install while tests (which build their own engine later) stay green.
    """

    def __init__(self, engine, scheduler=None) -> None:
        self.engine = engine
        self.scheduler = scheduler
        _ensure_table(engine)

    def _resolve_scheduler(self):
        """The live scheduler, or ``None`` (see the class docstring)."""
        sched = self.scheduler
        if sched is not None and callable(sched) and not hasattr(sched, "add_task"):
            try:
                return sched()
            except Exception:  # noqa: BLE001 — no scheduler beats a crashed door
                log.exception("goal scheduler resolver failed")
                return None
        return sched

    @staticmethod
    def schedule_name(goal_id: str) -> str:
        """The scheduler row's unique name for one goal (``goal:<id>``)."""
        return f"goal:{goal_id}"

    # -- CRUD ----------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        contract_text: str,
        agent_type: str = "builder",
        project_id: str | None = None,
        schedule: str = "",
        allowed_grants: list[str] | None = None,
        budget: dict[str, Any] | None = None,
        verifier: dict[str, Any] | None = None,
        origin: str = "",
    ) -> GoalContractRecord:
        """Validate + persist one goal (raises ``ValueError`` with the reason)."""
        contract = str(contract_text or "").strip()
        if not contract:
            raise ValueError("a goal needs contract_text — the goal, stated checkably")
        problem = grants_violation(allowed_grants)
        if problem:
            raise ValueError(problem)
        problem = budget_violation(budget)
        if problem:
            raise ValueError(problem)
        problem = verifier_violation(verifier)
        if problem:
            raise ValueError(problem)
        record = GoalContractRecord(
            name=str(name or "").strip() or contract[:60],
            contract_text=contract,
            agent_type=str(agent_type or "builder").strip() or "builder",
            # Context spine: empty/whitespace normalizes to None (ungrounded)
            # so "" never masquerades as a project id downstream (reflex rule).
            project_id=(project_id or "").strip() or None,
            schedule=str(schedule or "").strip(),
            allowed_grants_json=json.dumps(
                [str(g).strip() for g in (allowed_grants or []) if str(g).strip()]
            ),
            budget_json=json.dumps(budget if isinstance(budget, dict) else {}),
            verifier_json=json.dumps(
                verifier if isinstance(verifier, dict) else {"kind": "manual"}
            ),
            origin=str(origin or "").strip()[:64],
        )
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
        # D2 — the schedule is a PROMISE, so keep it or refuse it here, never
        # sell it. A non-empty cron creates the real scheduler row; a cron the
        # scheduler rejects (or a build with no scheduler at all) UNDOES the
        # goal row and raises — a card reading "daily at 09:00" over a goal
        # that can never fire is exactly the false promise this closes.
        if record.schedule:
            scheduler = self._resolve_scheduler()
            if scheduler is None:
                self._delete_row(record.id)
                raise ValueError(
                    "this store has no scheduler, so the schedule "
                    f"{record.schedule!r} would never fire — create the goal "
                    "without a schedule and run it manually"
                )
            try:
                scheduler.add_task(
                    self.schedule_name(record.id),
                    record.schedule,
                    kind="goal",
                    payload={"goal_id": record.id},
                )
            except Exception as exc:  # noqa: BLE001 — undo, then say why
                self._delete_row(record.id)
                raise ValueError(
                    f"schedule {record.schedule!r} was not accepted: {exc}"
                ) from exc
        return record

    def _delete_row(self, goal_id: str) -> None:
        """Best-effort row removal for :meth:`create`'s undo path."""
        try:
            with session_scope(self.engine) as db:
                row = db.get(GoalContractRecord, goal_id)
                if row is not None:
                    db.delete(row)
                    db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not undo goal row %s after a schedule refusal", goal_id)

    def get(self, goal_id: str) -> GoalContractRecord | None:
        try:
            with session_scope(self.engine) as db:
                return db.get(GoalContractRecord, goal_id)
        except Exception:  # noqa: BLE001 — a listing/read must never raise
            log.exception("goal get failed for %s", goal_id)
            return None

    def list(self, state: str | None = None) -> list[GoalContractRecord]:
        try:
            with session_scope(self.engine) as db:
                stmt = select(GoalContractRecord)
                if state is not None:
                    stmt = stmt.where(GoalContractRecord.state == state)
                stmt = stmt.order_by(GoalContractRecord.created_at.desc())  # type: ignore[attr-defined]
                return list(db.exec(stmt))
        except Exception:  # noqa: BLE001
            log.exception("goal list failed")
            return []

    def remove(self, goal_id: str) -> bool:
        with session_scope(self.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
        # The schedule row dies with the goal (D2) — unconditionally, so a
        # hand-edited record whose ``schedule`` was blanked still can't leave
        # an orphan row firing iterations for a deleted goal.
        scheduler = self._resolve_scheduler()
        if scheduler is not None:
            try:
                scheduler.remove(self.schedule_name(goal_id))
            except Exception:  # noqa: BLE001 — a scheduler hiccup must not undelete
                log.exception("could not remove the schedule row for goal %s", goal_id)
        return True

    def _sync_schedule(self, goal_id: str, schedule: str, state: str) -> None:
        """Keep the scheduler row's ``enabled`` in lock-step with the goal:
        ENABLED IFF ``active`` (D2). A paused/stopped/tripped/satisfied goal's
        cron must not keep firing refusals every morning; a resumed/reopened
        one must fire again without the user rebuilding anything. Re-creates a
        missing row for an active goal (an older build may have removed it on
        stop). Best-effort — a scheduler hiccup must never fail a state change
        that is already committed."""
        scheduler = self._resolve_scheduler()
        if not schedule or scheduler is None:
            return
        name = self.schedule_name(goal_id)
        try:
            if scheduler.get(name) is None:
                if state == "active":
                    scheduler.add_task(
                        name, schedule, kind="goal", payload={"goal_id": goal_id}
                    )
                return
            scheduler.enable(name, state == "active")
        except Exception:  # noqa: BLE001
            log.exception("could not sync the schedule row for goal %s", goal_id)

    # -- lifecycle -------------------------------------------------------------

    def transition(self, goal_id: str, to_state: str) -> GoalContractRecord:
        """Move a goal along a VALID edge (raises ``ValueError`` otherwise).

        ``satisfied``/``failed``/``stopped``/``tripped``→``active`` are all
        refused here on purpose — resurrection is :meth:`reopen`, explicitly.
        """
        to_state = str(to_state or "").strip().lower()
        if to_state not in GOAL_STATES:
            raise ValueError(f"unknown goal state {to_state!r}; expected one of {GOAL_STATES}")
        with session_scope(self.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                raise ValueError(f"no such goal: {goal_id}")
            if to_state == row.state:
                return row  # idempotent no-op, not an error
            allowed = GOAL_TRANSITIONS.get(row.state, frozenset())
            if to_state not in allowed:
                raise ValueError(
                    f"a {row.state} goal cannot become {to_state}"
                    + (
                        " — use reopen() to resurrect it explicitly"
                        if to_state == "active"
                        else ""
                    )
                )
            row.state = to_state
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
        self._sync_schedule(row.id, row.schedule, row.state)
        return row

    def reopen(self, goal_id: str) -> GoalContractRecord:
        """The EXPLICIT resurrection: any non-active goal → active.

        Clears the breaker (stale failures must not instantly re-trip a goal
        the user just deliberately revived) and stamps ``updated_at``. Raises
        ``ValueError`` for unknown / already-active.
        """
        with session_scope(self.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                raise ValueError(f"no such goal: {goal_id}")
            if row.state == "active":
                raise ValueError("goal is already active")
            row.state = "active"
            row.breaker_json = json.dumps({"failures": []})
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
        self._sync_schedule(row.id, row.schedule, row.state)
        return row

    # -- accounting -------------------------------------------------------------

    def add_spend(
        self,
        goal_id: str,
        *,
        tokens: int = 0,
        dollars: float = 0.0,
        wallclock_s: float = 0.0,
        iterations: int = 1,
        session_id: str = "",
    ) -> GoalContractRecord | None:
        """Accumulate one iteration's spend (one read-modify-write transaction).

        Single-writer by design (the GoalEngine awaits each iteration end to
        end), so this is atomic-enough without a version column. Returns the
        updated row, or ``None`` for an unknown goal.

        ``session_id`` stamps ``spent_json.last_session_id`` IN THE SAME WRITE
        (D7): the billed-flag and the balance land in one transaction, so
        there is no crash window where the spend committed but the "already
        billed" marker did not — which is precisely the window that would let
        :meth:`GoalEngine.rehydrate` bill the same completed session twice.
        Empty ``session_id`` preserves the previous stamp (a seed/adjustment
        must not erase the idempotence key).
        """
        with session_scope(self.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                return None
            prior_sid = self.last_accounted_session(row)
            spent = row.decoded_spent()
            spent["tokens"] = int(spent["tokens"]) + max(0, int(tokens or 0))
            spent["dollars"] = round(float(spent["dollars"]) + max(0.0, float(dollars or 0.0)), 6)
            spent["wallclock_s"] = round(
                float(spent["wallclock_s"]) + max(0.0, float(wallclock_s or 0.0)), 3
            )
            spent["iterations"] = int(spent["iterations"]) + max(0, int(iterations or 0))
            accounted = str(session_id or "").strip() or prior_sid
            if accounted:
                spent["last_session_id"] = accounted
            row.spent_json = json.dumps(spent)
            row.last_run_at = utcnow()
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def record_failure(self, goal_id: str, reason: str) -> tuple[GoalContractRecord | None, bool]:
        """Append one failure to the breaker and trip if the window fills.

        Returns ``(record, tripped_now)``. Failure timestamps are PRUNED to the
        window on every write, so old failures age out instead of counting
        forever. The state write happens here (durable before anything else
        reads it); the EVENT is the engine's job — this store has no bus.
        """
        now = utcnow()
        cutoff = now - timedelta(seconds=BREAKER_WINDOW_S)
        with session_scope(self.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                return None, False
            breaker = row.decoded_breaker()
            failures = []
            for iso in breaker.get("failures", []):
                parsed = _parse_iso(iso)
                if parsed is not None and parsed >= cutoff:
                    failures.append(iso)
            failures.append(now.isoformat())
            breaker["failures"] = failures
            breaker["last_reason"] = str(reason or "")[:400]
            tripped_now = False
            if len(failures) >= BREAKER_MAX_FAILURES and row.state == "active":
                breaker["tripped_at"] = now.isoformat()
                row.state = "tripped"
                tripped_now = True
            row.breaker_json = json.dumps(breaker)
            row.updated_at = now
            db.add(row)
            db.commit()
            db.refresh(row)
        if tripped_now:
            # A tripped goal's cron must stop firing refusals (D2's iff-active
            # rule); reopen re-enables it along with clearing the breaker.
            self._sync_schedule(row.id, row.schedule, row.state)
        return row, tripped_now

    @staticmethod
    def last_accounted_session(record: GoalContractRecord) -> str:
        """The session id whose spend is already in ``spent_json`` (see
        :meth:`add_spend`), or ``""``. Read raw — ``decoded_spent`` serves the
        four counters only."""
        try:
            raw = json.loads(record.spent_json or "{}")
        except (TypeError, ValueError):
            return ""
        return str(raw.get("last_session_id") or "") if isinstance(raw, dict) else ""

    def set_checkpoint(self, goal_id: str, checkpoint: dict[str, Any]) -> GoalContractRecord | None:
        """Replace the carry-forward checkpoint (the engine composes it)."""
        with session_scope(self.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                return None
            row.checkpoint_json = json.dumps(checkpoint if isinstance(checkpoint, dict) else {})
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return row


def goal_view(record: GoalContractRecord) -> dict[str, Any]:
    """One goal contract, FLAT, for the route/dashboard — decoded, never raw JSON.

    ``trip_reason`` is the CANONICAL top-level field the surfaces read: the
    breaker's honest reason when ``state == "tripped"``, ``None`` otherwise
    (always present, so a typed client never branches on key existence). The
    same text is mirrored at ``breaker.reason`` for the fallback path the
    surfaces also carry — one truth, two addresses, both from the record.
    """
    breaker = record.decoded_breaker()
    reason = str(breaker.get("last_reason") or "").strip() or None
    tripped = record.state == "tripped"
    return {
        "id": record.id,
        "name": record.name,
        "contract_text": record.contract_text,
        "agent_type": record.agent_type,
        "project_id": record.project_id,
        "schedule": record.schedule,
        "allowed_grants": record.decoded_grants(),
        "budget": record.decoded_budget(),
        "spent": record.decoded_spent(),
        "verifier": record.decoded_verifier(),
        "state": record.state,
        "trip_reason": reason if tripped else None,
        "breaker": {
            "failures": list(breaker.get("failures", [])),
            "reason": reason,
            "tripped_at": breaker.get("tripped_at"),
        },
        "checkpoint": record.decoded_checkpoint(),
        "origin": record.origin,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "last_run_at": record.last_run_at.isoformat() if record.last_run_at else None,
    }


def _parse_iso(value: str):
    """Parse a breaker timestamp; ``None`` for garbage (a hand-edited row must
    not crash failure recording — it just stops counting the bad entry)."""
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
