"""Goal DIGEST (G2, v1.209.0) — the trust engine's report: what each goal DID,
SPENT, and HELD over a window, composed by code from recorded truth only.

The honest-mock rule applies with full force here: this text is trust
infrastructure — the user reads it to decide whether standing autonomy keeps
its keys — so NO LINE of it is model-written. Every figure traces to a durable
record a model cannot edit:

* **iterations ran** — counted from ``goal.iteration_completed``
  :class:`~iron_jarvis.core.models.EventRecord` rows in the window. This
  source is chosen over a ``spent.iterations`` delta deliberately:
  ``spent_json`` is a single CUMULATIVE counter with no history, so a windowed
  delta would need a snapshot that does not exist (and rehydration adjustments
  would blur it) — whereas the engine publishes ``iteration_completed`` at
  EVERY ending (completed / failed / cancelled / crashed — the D6 handler
  guarantees even an escaping crash publishes) and the bus's persistence
  handler writes it durably. A row either exists in the window or it does not;
  nothing reconstructs it after the fact. Stated gap: an iteration whose
  daemon died mid-run gets no completion event (rehydrate reconciles state,
  not history) — the breaker/state-change lines carry that story instead.
* **spend** — summed from the goal's session ROWS in the window (``origin ==
  "goal:<id>"``): recorded token counts × ``eval.pricing.cost_for`` — the
  exact same recorded truth ``GoalEngine._settle_spend`` bills from, never the
  cumulative ``spent_json`` (cumulative ≠ windowed) and never a transcript.
* **results** — per window session, the ledger's created/changed files
  (``agents/outcome.session_result``: ToolInvocation + UndoJournal) and the
  session row's recorded summary — the same sources the engine's deterministic
  checkpoint is composed from.
* **asks held** — ``approval.requested`` events tagged to the goal's sessions
  whose ``approval.resolved`` decision in the window was ``timeout`` or
  ``deny``: the run paused on an ask-tier tool and the answer, while the user
  was away, was NO. A still-open ask is not listed (it is live, not history);
  an approved ask is not "held".
* **state changes** — ``goal.satisfied`` / ``goal.tripped`` events in the
  window, with the tripped reason verbatim.

A goal with NOTHING in the window is ABSENT — a digest that pads itself with
"no activity" rows buries the line that matters. Refused iterations
(``goal.iteration_refused``) are deliberately not a digest line: a refusal
spawned nothing and spent nothing, and the notifier alerts it live
(``comm/notifier.py``, this same release) — the digest reports what RAN.

Deterministic: given the same database and the same ``now``, two calls return
identical dicts (pure function of the records; ordering is pinned by
``created_at, id``). Pure SYNC and bounded (every query carries a LIMIT) —
callers on the event loop hop (the route registers a sync ``def`` handler, so
FastAPI threadpools it; v1.153.1 rule).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import EventRecord, Session
from .store import GoalStore

log = get_logger("goals.digest")

# Window bounds: at least one hour, at most ~30 days — a digest is a report,
# not an archive dump.
_MIN_HOURS = 1
_MAX_HOURS = 24 * 30

# Query bounds (every select below carries one — the off-loop rule's sibling:
# a digest must stay cheap no matter how big the event log has grown).
_MAX_EVENTS = 5000
_MAX_SESSIONS = 1000
_MAX_RESULTS_PER_GOAL = 20
_MAX_ITEMS_PER_GOAL = 50
_SUMMARY_CLIP = 400

# Goal event names — plain strings mirroring goals/engine.py's constants
# (imported lazily nowhere: the digest must not drag the engine/orchestrator
# import chain in). tests/test_goal_digest_v1209.py pins these against the
# engine's constants so the two files cannot drift.
_ITERATION_COMPLETED = "goal.iteration_completed"
_SATISFIED = "goal.satisfied"
_TRIPPED = "goal.tripped"
_GOAL_EVENT_TYPES = (_ITERATION_COMPLETED, _SATISFIED, _TRIPPED)

_APPROVAL_REQUESTED = "approval.requested"
_APPROVAL_RESOLVED = "approval.resolved"
#: The decisions that mean "the ask was HELD while nobody was watching":
#: the runtime's answer-window timeout, or an explicit deny. "once"/
#: "conversation" grants are not held, and a still-open ask is live, not
#: history.
_HELD_DECISIONS = frozenset({"timeout", "deny"})

_GOAL_ORIGIN_PREFIX = "goal:"


def _decode(payload_json: str) -> dict[str, Any]:
    """Payload JSON → dict; garbage decodes to ``{}`` (a hand-edited or
    truncated row must not take the whole digest down)."""
    try:
        raw = json.loads(payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _iso(value: Any) -> str:
    try:
        return value.isoformat()
    except AttributeError:
        return str(value or "")


def compose_digest(
    platform, hours: int = 24, *, now: datetime | None = None
) -> dict[str, Any]:
    """The window digest: ``{since, hours, goals: [...]}`` — see the module
    docstring for what each figure is derived from and why.

    Each goal entry: ``{id, name, ran, spent: {tokens, dollars}, results:
    [{session_id, files, summary}], asks_held: [{approval_id, tool, decision,
    at}], state_changes: [{to, reason, at}]}``. Goals quiet in the window are
    absent; no goals with activity → ``{"since": ..., "hours": ..., "goals":
    []}`` — an empty report, honestly empty, never an invented row.

    ``now`` exists for tests (determinism is asserted by calling twice with
    the same instant); production callers omit it.
    """
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 24
    hours = max(_MIN_HOURS, min(_MAX_HOURS, hours))
    at = now or utcnow()
    since = at - timedelta(hours=hours)

    store = GoalStore(platform.engine)
    goals = store.list()  # loads never raise; [] on failure

    # ---- gather the window's records (bounded, ordered, one pass) ---------
    goal_events: list[tuple[str, str | None, dict[str, Any], Any]] = []
    approval_events: list[tuple[str, str | None, dict[str, Any], Any]] = []
    sessions: list[dict[str, Any]] = []
    try:
        with session_scope(platform.engine) as db:
            rows = db.exec(
                select(EventRecord)
                .where(
                    EventRecord.type.in_(_GOAL_EVENT_TYPES),  # type: ignore[attr-defined]
                    EventRecord.created_at >= since,  # type: ignore[operator]
                )
                .order_by(EventRecord.created_at.asc(), EventRecord.id.asc())  # type: ignore[attr-defined]
                .limit(_MAX_EVENTS)
            )
            for r in rows:
                goal_events.append(
                    (r.type, r.session_id, _decode(r.payload_json), r.created_at)
                )
            rows = db.exec(
                select(EventRecord)
                .where(
                    EventRecord.type.in_((_APPROVAL_REQUESTED, _APPROVAL_RESOLVED)),  # type: ignore[attr-defined]
                    EventRecord.created_at >= since,  # type: ignore[operator]
                )
                .order_by(EventRecord.created_at.asc(), EventRecord.id.asc())  # type: ignore[attr-defined]
                .limit(_MAX_EVENTS)
            )
            for r in rows:
                approval_events.append(
                    (r.type, r.session_id, _decode(r.payload_json), r.created_at)
                )
            rows = db.exec(
                select(Session)
                .where(
                    Session.origin.like(_GOAL_ORIGIN_PREFIX + "%"),  # type: ignore[union-attr]
                    Session.created_at >= since,  # type: ignore[operator]
                )
                .order_by(Session.created_at.asc(), Session.id.asc())  # type: ignore[attr-defined]
                .limit(_MAX_SESSIONS)
            )
            for s in rows:
                # Plain values, harvested INSIDE the scope — rows detach on exit.
                sessions.append(
                    {
                        "id": s.id,
                        "goal_id": (s.origin or "")[len(_GOAL_ORIGIN_PREFIX) :],
                        "input_tokens": int(s.input_tokens or 0),
                        "output_tokens": int(s.output_tokens or 0),
                        "provider": s.provider or "",
                        "model": s.model or "",
                        "summary": (s.summary or "")[:_SUMMARY_CLIP],
                    }
                )
    except Exception:  # noqa: BLE001 — a digest read must degrade, never raise
        log.exception("digest window queries failed")
        return {"since": since.isoformat(), "hours": hours, "goals": []}

    # ---- group by goal ------------------------------------------------------
    events_by_goal: dict[str, list[tuple[str, dict[str, Any], Any]]] = {}
    for etype, _sid, payload, created in goal_events:
        gid = str(payload.get("goal_id") or "")
        if gid:
            events_by_goal.setdefault(gid, []).append((etype, payload, created))

    sessions_by_goal: dict[str, list[dict[str, Any]]] = {}
    session_goal: dict[str, str] = {}
    for s in sessions:
        if s["goal_id"]:
            sessions_by_goal.setdefault(s["goal_id"], []).append(s)
            session_goal[s["id"]] = s["goal_id"]
    # An iteration event names its session too — covers a session that started
    # BEFORE the window (its asks still belong to the goal).
    for _etype, sid, payload, _created in goal_events:
        gid = str(payload.get("goal_id") or "")
        if sid and gid:
            session_goal.setdefault(sid, gid)

    # Approval join: requested rows keyed by approval_id, resolution wins by
    # the LAST resolved decision in the window (the runtime publishes exactly
    # one, but a replayed log must not double-list).
    requested: dict[str, tuple[str | None, dict[str, Any], Any]] = {}
    resolutions: dict[str, str] = {}
    for etype, sid, payload, created in approval_events:
        apr = str(payload.get("approval_id") or "")
        if not apr:
            continue
        if etype == _APPROVAL_REQUESTED:
            requested[apr] = (sid, payload, created)
        else:
            resolutions[apr] = str(payload.get("decision") or "")

    asks_by_goal: dict[str, list[dict[str, Any]]] = {}
    for apr, (sid, payload, created) in requested.items():
        gid = session_goal.get(sid or "")
        if not gid:
            continue  # not a goal session's ask
        decision = resolutions.get(apr, "")
        if decision not in _HELD_DECISIONS:
            continue  # granted, or still open — not held
        asks_by_goal.setdefault(gid, []).append(
            {
                "approval_id": apr,
                "tool": str(payload.get("tool") or ""),
                "decision": decision,
                "at": _iso(created),
            }
        )

    # ---- compose (per goal, deterministic order: the store's created_at
    # desc listing, then id — stable for two calls over the same records) ----
    from ..eval import pricing  # lazy — keeps table registration light

    out_goals: list[dict[str, Any]] = []
    for goal in goals:
        gid = goal.id
        evs = events_by_goal.get(gid, [])
        sess = sessions_by_goal.get(gid, [])
        asks = asks_by_goal.get(gid, [])[:_MAX_ITEMS_PER_GOAL]

        ran = sum(1 for etype, _p, _c in evs if etype == _ITERATION_COMPLETED)
        tokens = sum(s["input_tokens"] + s["output_tokens"] for s in sess)
        dollars = round(
            sum(
                pricing.cost_for(
                    s["provider"], s["model"], s["input_tokens"], s["output_tokens"]
                )
                for s in sess
            ),
            6,
        )

        results: list[dict[str, Any]] = []
        for s in sess[:_MAX_RESULTS_PER_GOAL]:
            files: list[str] = []
            try:
                from ..agents import outcome as _outcome

                res = _outcome.session_result(platform.engine, s["id"])
                files = [
                    str(f)
                    for f in (
                        list(res.get("files_created") or [])
                        + list(res.get("files_changed") or [])
                    )
                ]
            except Exception:  # noqa: BLE001 — a ledger hiccup shrinks the
                # report, never fails it (the outcome module's own contract)
                log.exception("digest file harvest failed for %s", s["id"])
            results.append(
                {"session_id": s["id"], "files": files, "summary": s["summary"]}
            )

        state_changes: list[dict[str, Any]] = []
        for etype, payload, created in evs:
            if etype not in (_SATISFIED, _TRIPPED):
                continue
            state_changes.append(
                {
                    "to": "satisfied" if etype == _SATISFIED else "tripped",
                    "reason": str(payload.get("reason") or ""),
                    "at": _iso(created),
                }
            )
        state_changes = state_changes[:_MAX_ITEMS_PER_GOAL]

        if not (ran or sess or asks or state_changes):
            continue  # quiet goal — absent, not padded
        out_goals.append(
            {
                "id": gid,
                "name": goal.name,
                "ran": ran,
                "spent": {"tokens": tokens, "dollars": dollars},
                "results": results,
                "asks_held": asks,
                "state_changes": state_changes,
            }
        )

    return {"since": since.isoformat(), "hours": hours, "goals": out_goals}
