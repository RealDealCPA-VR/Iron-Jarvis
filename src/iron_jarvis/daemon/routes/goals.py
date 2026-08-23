"""Goal-contract routes (v1.208.0): ``/goals`` is THE public Goals surface.

The path is deliberate: ``/goals`` used to serve the Motivation Layer's
lightweight intent goals; those relocated VERBATIM to ``/autonomy/goals``
(``routes/autonomy.py``) so one public "Goals" concept remains — the
CONTRACTED kind (``goals/``: checkable contract text, hard budget,
deterministic verifier, circuit breaker, restart-surviving lifecycle).

Division of labor, stated once so it cannot drift:

* **Validation with a WHY lives in the store** (deny-floor grants, budget
  rules, verifier rules — ``GoalStore.create`` / ``goals/models.py``). This
  module maps its ``ValueError`` to a 400 with the text VERBATIM and never
  re-derives a rule: two copies of the deny floor is how one of them rots.
* **Transitions are guarded in the store** (``GOAL_TRANSITIONS``); a guard
  refusal maps to 409 with the store's sentence verbatim. Unknown ids are a
  404 checked here first, so "no such goal" never masquerades as a conflict.
* **An honest refusal from ``run_iteration`` is a RESULT, not an error**:
  paused/tripped/budget-exhausted/already-running come back 200 with
  ``{ok: false, refused: true, reason}`` — the caller asked "may an iteration
  run now?" and got a truthful no; a 4xx would teach clients that asking is
  dangerous.
* **Resume is state-aware**: a paused goal resumes via
  ``transition("active")``; a TRIPPED goal resumes via ``reopen`` — the
  breaker is CLEARED, because the dashboard promises "Resume clears it" and a
  resume that leaves stale failures in the window re-trips on the next
  hiccup. Every other resurrection (satisfied/failed/stopped → active) stays
  behind the explicit ``/reopen`` door, exactly as the store's transition
  guard words it.

THE TRUST LADDER'S RECEIPTS (G2, v1.209.0): trust upgrades are OFFERED ON
RECEIPTS, never defaulted. A goal's iterations run ordinary sessions
(``Session.origin == "goal:<id>"``); when one pauses on an ask-tier tool the
runtime publishes ``approval.requested`` / ``approval.resolved`` events
tagged with that session (v1.189/v1.200 machinery). :func:`ask_stats_for`
aggregates those receipts per TOOL — counts only, the args in the event
payload are NEVER copied out (the ``GET /chat/approvals/pending`` posture) —
and :func:`grant_offers` turns them into the deterministic server-side offer
list every surface renders, so no two surfaces can disagree about what the
receipts support. ``PATCH /goals/{id}/grants`` is the acceptance door: it
extends ``allowed_grants`` through the SAME ``grants_violation`` rule the
store enforces at create (a floor tool 400s verbatim), and the new grant
rides the NEXT iteration automatically — ``GoalEngine.run_iteration``
re-reads the row and ``_iterate`` passes ``goal.decoded_grants()`` as
``allow_tools`` into ``orchestrator.create_session``.

Off-loop notes (v1.153.1): store calls are single-row SQLite reads/writes,
and the receipts aggregation is one bounded indexed query — the sync
handlers run in FastAPI's threadpool, so none of it touches the event loop
(same effect as the ``asyncio.to_thread`` hop the async approvals-pending
handler needs). ``create_goal`` / ``stop_goal`` / ``run_iteration`` are
engine coroutines (they publish events and, for run, await the whole
session) and are awaited directly, per the engine's own contracts.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from ...core.logging import get_logger
from ...goals.models import GOAL_STATES, GoalContractRecord, grants_violation
from ...goals.store import goal_view
from ..schemas import GoalContractCreate, GoalGrantsPatch

log = get_logger("daemon.goals")

#: Newest-first cap on the receipts query. A bound, not a distortion: each ask
#: is two rows, so this covers ~2000 asks per goal — far past any honest goal's
#: lifetime — while keeping the query bounded by construction (the
#: approvals-pending hygiene).
_ASK_EVENT_CAP = 4000

#: The trust ladder's threshold: you approved ALL N asks (N >= this) — offer.
_OFFER_MIN_ASKS = 3

#: ``approval.resolved`` decisions that count as an approval ("once" grants the
#: call, "conversation" the rest of the run — both are the user saying yes).
_APPROVED_DECISIONS = frozenset({"once", "conversation"})

_ZERO_ASK = {"asked": 0, "approved": 0, "denied": 0, "timed_out": 0}


def ask_stats_for(engine, goal_id: str) -> dict[str, dict[str, int]]:
    """Per-TOOL approval receipts across THIS goal's sessions.

    ``{tool: {asked, approved, denied, timed_out}}`` — computed from the
    ``approval.requested`` / ``approval.resolved`` EventRecords of every
    session whose ``origin`` is this goal's stamp (both columns indexed; the
    scan is capped at :data:`_ASK_EVENT_CAP` newest-first). Mirrors the
    ``GET /chat/approvals/pending`` hygiene: payloads are parsed defensively
    (a corrupt row is skipped, never a 500) and the args they carry are NEVER
    copied out — this function returns counts and nothing else. Loads never
    raise (the GoalStore contract): a broken query answers ``{}``, which
    honestly offers nothing.
    """
    from sqlmodel import select

    from ...core.db import session_scope
    from ...core.events import EventType
    from ...core.models import EventRecord
    from ...core.models import Session as SessionRow
    from ...goals.engine import _goal_origin

    try:
        with session_scope(engine) as db:
            session_ids = select(SessionRow.id).where(
                SessionRow.origin == _goal_origin(goal_id)
            )
            rows = list(
                db.exec(
                    select(EventRecord.type, EventRecord.payload_json)
                    .where(EventRecord.session_id.in_(session_ids))  # type: ignore[union-attr]
                    .where(
                        EventRecord.type.in_(  # type: ignore[union-attr]
                            [EventType.APPROVAL_REQUESTED, EventType.APPROVAL_RESOLVED]
                        )
                    )
                    .order_by(EventRecord.created_at.desc())  # type: ignore[arg-type]
                    .limit(_ASK_EVENT_CAP)
                )
            )
    except Exception:  # noqa: BLE001 — receipts must never take a goal view down
        log.exception("ask-stats query failed for %s", goal_id)
        return {}

    stats: dict[str, dict[str, int]] = {}
    for etype, payload_json in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError):
            continue  # a corrupt row is not this listing's problem
        if not isinstance(payload, dict):
            continue
        tool = payload.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        bucket = stats.setdefault(tool, dict(_ZERO_ASK))
        if etype == EventType.APPROVAL_REQUESTED:
            bucket["asked"] += 1
            continue
        decision = str(payload.get("decision") or "")
        if decision in _APPROVED_DECISIONS:
            bucket["approved"] += 1
        elif decision == "deny":
            bucket["denied"] += 1
        elif decision == "timeout":
            bucket["timed_out"] += 1
        # An unknown decision counts as nothing: it cannot support an offer,
        # and inventing a bucket for it would claim a receipt nobody issued.
    return stats


def grant_offers(record: GoalContractRecord, stats: dict[str, dict[str, int]]) -> list[str]:
    """The trust ladder's OFFER, computed server-side so every surface agrees.

    The rationale, spelled out: "you approved all N asks (N >= 3) for this
    tool, zero denies, zero timeouts — so the app OFFERS the standing grant;
    it never defaults to it." Deterministic and conservative on purpose:

    * one deny or one timed-out/unanswered ask (``approved != asked``) means
      the receipts do not support the offer;
    * a tool already in ``allowed_grants`` has nothing left to offer;
    * a DENY-FLOOR tool is NEVER offered at any count — a perfect approval
      streak on ``shell`` is still not consent to a standing headless bypass
      (the floor is refused at write AND spawn time, so offering it would be
      offering a guaranteed 400).
    """
    from ...tools.permissions import DENY_FLOOR_TOOLS

    granted = set(record.decoded_grants())
    offers: list[str] = []
    for tool in sorted(stats):
        s = stats[tool]
        if tool in granted or tool in DENY_FLOOR_TOOLS:
            continue
        if (
            int(s.get("asked", 0)) >= _OFFER_MIN_ASKS
            and int(s.get("approved", 0)) == int(s.get("asked", 0))
            and int(s.get("denied", 0)) == 0
            and int(s.get("timed_out", 0)) == 0
        ):
            offers.append(tool)
    return offers


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _engine():
        eng = getattr(d.platform, "goal_engine", None)
        if eng is None:
            raise HTTPException(
                status_code=503, detail="the goal engine is not available on this build"
            )
        return eng

    def _record_or_404(goal_id: str):
        record = _engine().store.get(goal_id)
        if record is None:
            raise HTTPException(status_code=404, detail="goal not found")
        return record

    def _payload(record, *, include_stats: bool = False) -> dict[str, Any]:
        """``goal_view`` + ``grant_offers`` (EVERY serialized goal carries the
        offer list, so the list, the detail and every verb response agree);
        the raw ``ask_stats`` ride only the detail route — the counts back the
        offer, the offer is what the surfaces render."""
        stats = ask_stats_for(d.platform.engine, record.id)
        view = goal_view(record)
        view["grant_offers"] = grant_offers(record, stats)
        if include_stats:
            view["ask_stats"] = stats
        return view

    @app.get("/goals")
    def list_goal_contracts(state: str | None = None) -> dict[str, Any]:
        if state is not None and state not in GOAL_STATES:
            # An unknown state silently answering [] would read as "no goals";
            # name the vocabulary instead.
            raise HTTPException(
                status_code=400,
                detail=f"unknown goal state {state!r}; expected one of {GOAL_STATES}",
            )
        return {"goals": [_payload(g) for g in _engine().store.list(state)]}

    # ----------------------------------------------------------------------- #
    # DIGEST (G2, v1.209.0) — added by the digest agent this wave; every other
    # route in this module belongs to the goals-routes agent. It sits HERE,
    # not at the bottom, because it MUST register before ``GET
    # /goals/{goal_id}`` below: FastAPI matches in registration order, so a
    # later ``/goals/digest`` would be swallowed as ``goal_id="digest"`` and
    # answer 404 "goal not found" forever.
    # ----------------------------------------------------------------------- #

    @app.get("/goals/digest")
    def goal_digest(hours: int = 24) -> dict[str, Any]:
        """The deterministic trust report: what every goal did, spent, and
        held over the window (``goals/digest.py`` — composed from EventRecord
        rows, session-row spend, and the ledger, never model text). SYNC
        handler on purpose: ``compose_digest`` is pure sync + bounded, so
        FastAPI's threadpool is the off-loop hop (v1.153.1). ``hours`` is
        clamped inside compose (1..720) rather than 400ing — a sloppy window
        still deserves a truthful report."""
        from ...goals.digest import compose_digest

        _engine()  # 503 honestly when the goal engine is absent on this build
        return {"digest": compose_digest(d.platform, hours)}

    @app.get("/goals/{goal_id}")
    def get_goal_contract(goal_id: str) -> dict[str, Any]:
        """One goal WITH its trust-ladder receipts (``ask_stats``) — see the
        module docstring."""
        record = _record_or_404(goal_id)
        return {"goal": _payload(record, include_stats=True)}

    @app.patch("/goals/{goal_id}/grants")
    def patch_goal_grants(goal_id: str, body: GoalGrantsPatch) -> dict[str, Any]:
        """Accept a trust-ladder offer (or grant manually): EXTEND
        ``allowed_grants`` through the store's own write-time rule —
        ``grants_violation`` is the one function ``GoalStore.create`` and the
        engine's spawn-time re-check already call, so a deny-floor tool 400s
        here with the exact same sentence. The new grant takes effect on the
        NEXT iteration with no further wiring: ``run_iteration`` re-reads the
        row and ``_iterate`` passes ``decoded_grants()`` to
        ``orchestrator.create_session(allow_tools=...)``."""
        record = _record_or_404(goal_id)
        add = [str(t).strip() for t in (body.add or []) if str(t).strip()]
        if not add:
            raise HTTPException(
                status_code=400,
                detail="nothing to grant — pass add: [\"tool\", ...]",
            )
        merged = list(record.decoded_grants())
        for tool in add:
            if tool not in merged:
                merged.append(tool)  # idempotent: re-granting is not an error
        problem = grants_violation(merged)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        from ...core.db import session_scope
        from ...core.ids import utcnow

        with session_scope(d.platform.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:  # deleted between the read and the write
                raise HTTPException(status_code=404, detail="goal not found")
            row.allowed_grants_json = json.dumps(merged)
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
        return {"goal": _payload(row)}

    @app.post("/goals")
    async def create_goal_contract(body: GoalContractCreate) -> dict[str, Any]:
        try:
            record = await _engine().create_goal(**body.model_dump(), origin="api")
        except ValueError as exc:
            # The store's refusal sentence VERBATIM — it already says why and
            # what to do instead (deny floor, budget rules, verifier rules).
            raise HTTPException(status_code=400, detail=str(exc))
        return {"goal": _payload(record)}

    @app.post("/goals/{goal_id}/run")
    async def run_goal_contract(goal_id: str) -> dict[str, Any]:
        """One iteration NOW. The engine's result dict is relayed as-is:
        refusals are ``{ok: false, refused: true, reason}`` at 200 — an honest
        refusal is a result, not an error — and an unknown id answers the
        engine's own ``{ok: false, reason: "unknown goal …"}`` shape."""
        return await _engine().run_iteration(goal_id)

    @app.post("/goals/{goal_id}/pause")
    def pause_goal_contract(goal_id: str) -> dict[str, Any]:
        _record_or_404(goal_id)
        try:
            record = _engine().store.transition(goal_id, "paused")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": _payload(record)}

    @app.post("/goals/{goal_id}/resume")
    def resume_goal_contract(goal_id: str) -> dict[str, Any]:
        """Paused → active via the guarded transition; TRIPPED → active via
        ``reopen`` so the breaker is cleared (see the module docstring). Any
        other state gets the transition guard's refusal verbatim as a 409."""
        record = _record_or_404(goal_id)
        try:
            if record.state == "tripped":
                record = _engine().store.reopen(goal_id)
            else:
                record = _engine().store.transition(goal_id, "active")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": _payload(record)}

    @app.post("/goals/{goal_id}/stop")
    async def stop_goal_contract(goal_id: str) -> dict[str, Any]:
        _record_or_404(goal_id)
        try:
            # Through the ENGINE, not the bare store: stop publishes
            # ``goal.stopped`` so the dashboard surfaces refresh.
            record = await _engine().stop_goal(goal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": _payload(record)}

    @app.post("/goals/{goal_id}/reopen")
    def reopen_goal_contract(goal_id: str) -> dict[str, Any]:
        _record_or_404(goal_id)
        try:
            record = _engine().store.reopen(goal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": _payload(record)}

    @app.delete("/goals/{goal_id}")
    def delete_goal_contract(goal_id: str) -> dict[str, Any]:
        if not _engine().store.remove(goal_id):
            raise HTTPException(status_code=404, detail="goal not found")
        return {"deleted": goal_id}
