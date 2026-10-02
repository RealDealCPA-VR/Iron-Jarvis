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
#: "always" (v1.299.0) joined: it is "once" plus a standing grant — a yes.
_APPROVED_DECISIONS = frozenset({"once", "conversation", "always"})

_ZERO_ASK = {"asked": 0, "approved": 0, "denied": 0, "timed_out": 0}


def _ask_rows(engine, goal_id: str) -> list[tuple[str, dict]]:
    """The parsed ``approval.requested`` / ``approval.resolved`` payloads of
    every session whose ``origin`` is this goal's stamp, newest first,
    capped at :data:`_ASK_EVENT_CAP` (both columns indexed). ONE query for
    both aggregations below; a corrupt row is skipped, a broken query
    answers ``[]`` (loads never raise — the GoalStore contract)."""
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
        return []
    out: list[tuple[str, dict]] = []
    for etype, payload_json in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError):
            continue  # a corrupt row is not this listing's problem
        if isinstance(payload, dict):
            out.append((str(etype), payload))
    return out


def _stats_from_rows(rows: list[tuple[str, dict]]) -> dict[str, dict[str, int]]:
    from ...core.events import EventType

    stats: dict[str, dict[str, int]] = {}
    for etype, payload in rows:
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


def ask_stats_for(engine, goal_id: str) -> dict[str, dict[str, int]]:
    """Per-TOOL approval receipts across THIS goal's sessions.

    ``{tool: {asked, approved, denied, timed_out}}`` — computed from the
    ``approval.requested`` / ``approval.resolved`` EventRecords of every
    session whose ``origin`` is this goal's stamp (see :func:`_ask_rows`).
    Mirrors the ``GET /chat/approvals/pending`` hygiene: the args the
    payloads carry are NEVER copied out — this function returns counts and
    nothing else. Loads never raise: a broken query answers ``{}``, which
    honestly offers nothing.
    """
    return _stats_from_rows(_ask_rows(engine, goal_id))


def _hash_stats_from_rows(rows: list[tuple[str, dict]]) -> dict[str, dict[str, dict]]:
    from ...core.events import EventType
    from ...core.grants import grant_label

    def _bucket(tool: str, h: str) -> dict:
        return out.setdefault(tool, {}).setdefault(h, {**_ZERO_ASK, "label": ""})

    out: dict[str, dict[str, dict]] = {}
    hash_of: dict[str, str] = {}  # approval_id -> args_hash (the join key)
    # Requests first: they carry the hash (v1.299.0) and the REDACTED args
    # the card showed — the label an exact offer is shown with.
    for etype, payload in rows:
        if etype != EventType.APPROVAL_REQUESTED:
            continue
        tool = payload.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        h = str(payload.get("args_hash") or "")
        aid = str(payload.get("approval_id") or "")
        if aid:
            hash_of[aid] = h
        bucket = _bucket(tool, h)
        bucket["asked"] += 1
        if h and not bucket["label"]:
            bucket["label"] = grant_label(tool, payload.get("args"))
    for etype, payload in rows:
        if etype != EventType.APPROVAL_RESOLVED:
            continue
        tool = payload.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        # A resolution finds its request by approval id; one that cannot (a
        # pre-v1.299 row, a corrupt request) lands under "" — unknown hash —
        # which can never support an EXACT offer.
        h = hash_of.get(str(payload.get("approval_id") or ""), "")
        bucket = _bucket(tool, h)
        decision = str(payload.get("decision") or "")
        if decision in _APPROVED_DECISIONS:
            bucket["approved"] += 1
        elif decision == "deny":
            bucket["denied"] += 1
        elif decision == "timeout":
            bucket["timed_out"] += 1
    return out


def ask_hash_stats_for(engine, goal_id: str) -> dict[str, dict[str, dict]]:
    """Per (TOOL, ARGS_HASH) receipts (v1.299.0): ``{tool: {args_hash:
    {asked, approved, denied, timed_out, label}}}``. The hash rides the
    ``approval.requested`` payload (the runtime writes it; a hash of the real
    arguments is not a secret); a resolution joins its request by
    ``approval_id``. ``label`` is the REDACTED display the card showed —
    the one string an exact offer is rendered with; the args themselves are
    never copied out. ``""`` is the unknown-hash bucket (older rows)."""
    return _hash_stats_from_rows(_ask_rows(engine, goal_id))


def _live_store_grants(grants, goal_id: str) -> tuple[set[tuple[str, str]], set[str]]:
    """``(exact pairs, any-args tools)`` live in the store for this goal."""
    exact: set[tuple[str, str]] = set()
    any_args: set[str] = set()
    if grants is None:
        return exact, any_args
    try:
        rows = grants.list("goal", goal_id)
    except Exception:  # noqa: BLE001 — a store fault offers as if nothing were granted
        return exact, any_args
    for rec in rows:
        if rec.args_hash:
            exact.add((rec.tool, rec.args_hash))
        else:
            any_args.add(rec.tool)
    return exact, any_args


def _streak(s: dict) -> bool:
    """The ladder's threshold: all N asks approved, N >= 3, no deny, no
    timeout."""
    return (
        int(s.get("asked", 0)) >= _OFFER_MIN_ASKS
        and int(s.get("approved", 0)) == int(s.get("asked", 0))
        and int(s.get("denied", 0)) == 0
        and int(s.get("timed_out", 0)) == 0
    )


def exact_offers(
    record: GoalContractRecord,
    hash_stats: dict[str, dict[str, dict]],
    grants=None,
) -> list[dict]:
    """The EXACT offers (v1.299.0): ``[{tool, args_hash, label, count}]`` —
    one per tool whose asks ALL carry the SAME non-empty hash and pass the
    streak rule. "The same command three times" earns "always allow THIS
    command", not "always allow shell": a floor tool qualifies here (an exact
    grant is not an any-args bypass — the store refuses those on the floor).
    Mixed hashes, or any ask with no hash, fall through to the per-tool rule
    (:func:`grant_offers`). Suppressed when a live goal-scoped store grant
    already covers it (exact for that hash, or any-args for that tool)."""
    live_exact, live_any = _live_store_grants(grants, record.id)
    offers: list[dict] = []
    for tool in sorted(hash_stats):
        per = hash_stats[tool]
        hashes = [h for h in per if h]
        if len(hashes) != 1 or int(per.get("", {}).get("asked", 0)) > 0:
            continue  # several commands, or asks whose hash is unknown
        if int(per.get("", {}).get("approved", 0)) + int(per.get("", {}).get("denied", 0)) + int(
            per.get("", {}).get("timed_out", 0)
        ) > 0:
            continue  # a resolution that could not find its request
        h = hashes[0]
        s = per[h]
        if not _streak(s):
            continue
        if (tool, h) in live_exact or tool in live_any:
            continue
        offers.append(
            {
                "tool": tool,
                "args_hash": h,
                "label": str(s.get("label") or tool),
                "count": int(s.get("asked", 0)),
            }
        )
    return offers


def grant_offers(
    record: GoalContractRecord,
    stats: dict[str, dict[str, int]],
    hash_stats: "dict[str, dict[str, dict]] | None" = None,
    grants=None,
) -> list[str]:
    """The trust ladder's PER-TOOL offer, computed server-side so every
    surface agrees.

    The rationale, spelled out: "you approved all N asks (N >= 3) for this
    tool, zero denies, zero timeouts — so the app OFFERS the standing grant;
    it never defaults to it." Deterministic and conservative on purpose:

    * one deny or one timed-out/unanswered ask (``approved != asked``) means
      the receipts do not support the offer;
    * a tool already in ``allowed_grants`` — or (v1.299.0) covered by a live
      any-args store grant — has nothing left to offer;
    * a DENY-FLOOR tool is NEVER offered at any count — a perfect approval
      streak on ``shell`` is still not consent to a standing headless bypass
      (the floor is refused at write AND spawn time, so offering it would be
      offering a guaranteed 400);
    * (v1.299.0) a tool whose receipts support an EXACT offer
      (:func:`exact_offers`) is offered THAT, not this — the same three
      commands earn "always this command", never "always this tool".
    """
    from ...tools.permissions import DENY_FLOOR_TOOLS

    granted = set(record.decoded_grants())
    exact_tools = {o["tool"] for o in exact_offers(record, hash_stats or {}, grants)}
    _, live_any = _live_store_grants(grants, record.id)
    offers: list[str] = []
    for tool in sorted(stats):
        s = stats[tool]
        if tool in granted or tool in DENY_FLOOR_TOOLS:
            continue
        if tool in exact_tools or tool in live_any:
            continue
        if _streak(s):
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
        rows = _ask_rows(d.platform.engine, record.id)
        stats = _stats_from_rows(rows)
        hash_stats = _hash_stats_from_rows(rows)
        store = getattr(d.platform, "grants", None)
        view = goal_view(record)
        view["grant_offers"] = grant_offers(record, stats, hash_stats, store)
        # v1.299.0: exact offers ("always allow THIS command") and the live
        # standing grants this goal holds, so the card can show and revoke
        # them. Labels are the REDACTED display; args never ride here.
        view["grant_offers_exact"] = exact_offers(record, hash_stats, store)
        view["standing_grants"] = (
            [store.as_dict(g) for g in store.list("goal", record.id)]
            if store is not None
            else []
        )
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
        """Accept a trust-ladder offer (or grant manually).

        ``add`` (per-tool): EXTEND ``allowed_grants`` through the store's own
        write-time rule — ``grants_violation`` is the one function
        ``GoalStore.create`` and the engine's spawn-time re-check already
        call, so a deny-floor tool 400s here with the exact same sentence —
        AND (v1.299.0) mint a goal-scoped any-args standing grant beside it
        (the compat JSON keeps riding ``create_session(allow_tools=)``; the
        store row is what the Grants list shows and revokes).

        ``add_exact`` (v1.299.0): ``[{tool, args_hash, label?}]`` — EXACT
        standing grants, store-only (``allowed_grants`` cannot express one).
        A floor tool is allowed here: an exact command is not an any-args
        bypass. ``expires_days`` (default 30; ``null`` = never, which only the
        goal scope may say) applies to every row this call mints."""
        record = _record_or_404(goal_id)
        add = [str(t).strip() for t in (body.add or []) if str(t).strip()]
        exact = [e for e in (body.add_exact or []) if isinstance(e, dict)]
        if not add and not exact:
            raise HTTPException(
                status_code=400,
                detail=(
                    "nothing to grant — pass add: [\"tool\", ...] and/or "
                    "add_exact: [{tool, args_hash}, ...]"
                ),
            )
        expires_days = body.expires_days
        if expires_days is not None and int(expires_days) <= 0:
            raise HTTPException(
                status_code=400,
                detail="expires_days must be positive, or null for never",
            )
        store = getattr(d.platform, "grants", None)
        if exact and store is None:
            raise HTTPException(
                status_code=503,
                detail="standing grants are not available on this daemon — restart it",
            )
        from ...core.db import session_scope
        from ...core.grants import grant_label
        from ...core.ids import utcnow

        minted: list = []
        if add:
            merged = list(record.decoded_grants())
            for tool in add:
                if tool not in merged:
                    merged.append(tool)  # idempotent: re-granting is not an error
            problem = grants_violation(merged)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
            with session_scope(d.platform.engine) as db:
                row = db.get(GoalContractRecord, goal_id)
                if row is None:  # deleted between the read and the write
                    raise HTTPException(status_code=404, detail="goal not found")
                row.allowed_grants_json = json.dumps(merged)
                row.updated_at = utcnow()
                db.add(row)
                db.commit()
            if store is not None:
                for tool in add:
                    try:
                        minted.append(
                            store.create(
                                "goal", goal_id, tool, "",
                                f"{tool} (any arguments)", expires_days,
                            )
                        )
                    except ValueError as exc:
                        # The floor was checked above; anything else here is
                        # a malformed request, said verbatim.
                        raise HTTPException(status_code=400, detail=str(exc))
        for entry in exact:
            tool = str(entry.get("tool") or "").strip()
            h = str(entry.get("args_hash") or "").strip().lower()
            label = str(entry.get("label") or "").strip()
            if not tool or not h:
                raise HTTPException(
                    status_code=400, detail="an exact grant needs tool and args_hash"
                )
            if d.platform.registry.get(tool) is None:
                # A grant names a REGISTERED tool (the ladder's offers come from
                # the runtime's own call names); a typo would sit in the list
                # forever and lift nothing (review fix).
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown tool {tool!r} — an exact grant names a registered tool",
                )
            try:
                minted.append(
                    store.create(
                        "goal", goal_id, tool, h,
                        label or grant_label(tool, {"args_hash": h[:12]}),
                        expires_days,
                    )
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        with session_scope(d.platform.engine) as db:
            row = db.get(GoalContractRecord, goal_id)
            if row is None:
                raise HTTPException(status_code=404, detail="goal not found")
            db.refresh(row)
            db.expunge(row)
        return {
            "goal": _payload(row),
            "granted": [store.as_dict(g) for g in minted] if store is not None else [],
        }

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
        # v1.299.0 (review fix): a goal's standing grants die with the goal —
        # a goal-scoped row has no other reader, and a "never expires" grant
        # (goal scope only) would otherwise outlive the thing it was for.
        revoked: list[str] = []
        store = getattr(d.platform, "grants", None)
        if store is not None:
            try:
                for rec in store.list("goal", goal_id):
                    store.revoke(rec.id)
                    revoked.append(rec.id)
            except Exception:  # noqa: BLE001 — the goal is gone; say what was not revoked
                log.exception("standing grants of %s not revoked", goal_id)
        # Additive ONLY when something was revoked: the pre-v1.299 shape
        # `{"deleted": id}` stays byte-identical for every other goal.
        return {"deleted": goal_id, **({"revoked_grants": revoked} if revoked else {})}
