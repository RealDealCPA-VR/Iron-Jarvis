"""An agent run's conversation, kept so a restart can resume it (v1.337.0).

Part 3 of the pi-durable borrow (ideas only, MIT). Before this, Continue on
a job a restart cut off started a NEW run whose only memory of the old one
was a one-paragraph recap (``Orchestrator.continue_session``): every file it
had read, every result it had seen and every decision it had made was gone,
because model messages were never stored.

* :func:`save` — the flat perceive→act loop calls it when the model asks for
  tools and again when their results are in, so the stored list always ends
  either on complete tool rounds or on ONE assistant turn whose calls were
  running. Off the loop, best-effort (a failed save never touches the run).
* :func:`load_for_resume` — the messages a resumed run starts from. A tool
  call with no result (the process died while it ran) gets one: the note the
  boot reconcile wrote on its ledger row when there is one, else the same
  words from ``tools.base.replay_safe`` — "safe to run again" for a read,
  "may have landed, check first" for anything else. The model sees its own
  call and what became of it, never an orphaned ``tool_use`` (strict
  providers reject a whole conversation with one).

The transcript is a RESUME aid, not the ledger: ``ToolInvocation`` and the
run rows stay the record of what happened.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import RunTranscript

log = logging.getLogger("iron_jarvis.agents.transcript")

#: A stored transcript larger than this is not written (a resume would not
#: fit any model's window anyway; Continue then falls back to the recap).
MAX_TRANSCRIPT_CHARS = 4_000_000

#: The note a resumed run's model reads after its conversation.
RESUME_NOTE = (
    "[Iron Jarvis stopped while you were working on this (a restart or a crash)"
    " and has resumed. Your conversation so far is above, exactly as it was. A"
    " tool call that was cut off says so in its result: a call that only reads"
    " is safe to run again; for anything else, check whether it took effect"
    " before doing it again. Continue the task from where you stopped.]"
)


def _encode(messages: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        row: dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if getattr(m, "tool_call_id", None):
            row["tool_call_id"] = m.tool_call_id
        if getattr(m, "name", None):
            row["name"] = m.name
        calls = getattr(m, "tool_calls", None) or []
        if calls:
            row["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments or {}} for c in calls
            ]
        if getattr(m, "images", None):
            row["content"] = (row["content"] + "\n[an image was attached here]").strip()
        out.append(row)
    return out


def _decode(rows: list[Any]) -> list[Any]:
    from ..providers.adapters.base import LLMMessage, ToolCall

    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("role") not in ("user", "assistant", "tool"):
            continue
        out.append(
            LLMMessage(
                role=r["role"],
                content=str(r.get("content") or ""),
                tool_call_id=r.get("tool_call_id"),
                name=r.get("name"),
                tool_calls=[
                    ToolCall(id=str(c.get("id")), name=str(c.get("name")),
                             arguments=c.get("arguments") or {})
                    for c in (r.get("tool_calls") or [])
                    if isinstance(c, dict) and c.get("id") and c.get("name")
                ],
            )
        )
    return out


def _write(engine: Any, run_id: str, session_id: str, text: str) -> None:
    try:
        with session_scope(engine) as db:
            row = db.get(RunTranscript, run_id) or RunTranscript(run_id=run_id)
            row.session_id = session_id
            row.messages_json = text
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — a transcript never fails a run
        log.warning("could not save the conversation of run %s", run_id, exc_info=True)


async def save(engine: Any, run_id: str, session_id: str, messages: list[Any]) -> None:
    """Keep ``messages`` as run ``run_id``'s conversation (off the loop)."""
    import asyncio

    try:
        text = json.dumps(_encode(messages))
    except Exception:  # noqa: BLE001
        return
    if len(text) > MAX_TRANSCRIPT_CHARS:
        return
    await asyncio.to_thread(_write, engine, run_id, session_id, text)


def has_transcript(engine: Any, run_id: str) -> bool:
    with session_scope(engine) as db:
        row = db.get(RunTranscript, run_id)
        return row is not None and (row.messages_json or "[]") != "[]"


def _cut_off_note(engine: Any, run_id: str, call: Any, registry: Any) -> str:
    """What became of a tool call the stop cut off: the reconcile's note on
    its ledger row (matched by run, tool and arguments), else the words
    ``replay_safe`` chooses."""
    from sqlmodel import select

    from ..core.models import ToolInvocation
    from ..tools.base import replay_safe
    from ..tools.registry import INTERRUPTED_MAY_HAVE_LANDED, INTERRUPTED_SAFE, interrupted_note

    try:
        with session_scope(engine) as db:
            rows = list(
                db.exec(
                    select(ToolInvocation).where(
                        ToolInvocation.agent_run_id == run_id, ToolInvocation.tool == call.name
                    )
                ).all()
            )
        for r in reversed(rows):
            if r.output in (INTERRUPTED_SAFE, INTERRUPTED_MAY_HAVE_LANDED):
                return r.output
    except Exception:  # noqa: BLE001
        pass
    tool = registry.get(call.name) if registry is not None else None
    return interrupted_note(replay_safe(tool))


# --- a decomposed run's plan (v1.339.0) -----------------------------------

#: The note the step a stop cut off gets in front of its prompt on resume.
STEP_RESUME_NOTE = (
    "[Iron Jarvis stopped while this step was running (a restart or a crash)"
    " and has resumed. Some of this step may already be done: check what is"
    " already there before doing anything again.]"
)


def _write_plan(engine: Any, run_id: str, session_id: str, plan: str | None, results: str) -> None:
    from ..core.models import RunPlan

    try:
        with session_scope(engine) as db:
            row = db.get(RunPlan, run_id) or RunPlan(run_id=run_id)
            row.session_id = session_id
            if plan is not None:
                row.plan_json = plan
            row.results_json = results
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — a plan record never fails a run
        log.warning("could not save the plan of run %s", run_id, exc_info=True)


async def save_plan(
    engine: Any, run_id: str, session_id: str, plan: list[Any] | None, results: list[Any]
) -> None:
    """Keep run ``run_id``'s plan (when given) and its finished steps (off the
    loop, best-effort). ``plan``/``results`` are ``PlanStep``/``StepResult``s."""
    import asyncio
    from dataclasses import asdict

    try:
        plan_text = None if plan is None else json.dumps([asdict(s) for s in plan])
        results_text = json.dumps([asdict(r) for r in results])
    except Exception:  # noqa: BLE001
        return
    await asyncio.to_thread(_write_plan, engine, run_id, session_id, plan_text, results_text)


def has_plan(engine: Any, run_id: str) -> bool:
    from ..core.models import RunPlan

    with session_scope(engine) as db:
        row = db.get(RunPlan, run_id)
        return row is not None and (row.plan_json or "[]") != "[]"


def load_plan(engine: Any, run_id: str) -> tuple[list[Any], list[Any]] | None:
    """``(plan, finished results)`` run ``run_id`` kept, or None."""
    from ..core.models import RunPlan
    from .decompose import PlanStep, StepResult

    with session_scope(engine) as db:
        row = db.get(RunPlan, run_id)
        if row is None:
            return None
        plan_text, results_text = row.plan_json or "[]", row.results_json or "[]"
    try:
        plan = [PlanStep(**s) for s in json.loads(plan_text)]
        results = [StepResult(**r) for r in json.loads(results_text)]
    except (ValueError, TypeError):
        return None
    if not plan or len(results) > len(plan):
        return None
    return plan, results


# --- the plan as a live checklist (v1.350.0) --------------------------------

#: A plan step's state on the checklist (``GET /sessions/{id}/plan``).
STEP_STATES = ("waiting", "running", "done", "failed", "skipped")

#: Session statuses that mean "still going" (queued counts: it will run).
_LIVE_SESSION = ("active", "queued")
#: Run states that mean "over", whatever the session row says yet.
_ENDED_RUN = ("completed", "failed", "cancelled")


class PlanUnreadable(ValueError):
    """The newest plan a session kept could not be read (a corrupt record)."""


def _value(x: Any) -> str:
    return str(getattr(x, "value", x) or "").lower()


def _naive(dt: Any) -> Any:
    """Order stored timestamps without mixing aware and naive values."""
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) is not None else dt


def latest_plan(engine: Any, session_id: str) -> dict[str, Any] | None:
    """The plan of ``session_id``'s NEWEST run that kept one, or None when no
    run of it did. BLOCKING (two indexed reads) — callers hop off the loop.

    ``{run_id, goals: [str], results: [dict], run_state, session_status}``;
    raises :class:`PlanUnreadable` when that plan's record cannot be read —
    never a half-read plan, and never an OLDER plan shown in its place."""
    from sqlmodel import select

    from ..core.models import AgentRun, RunPlan, Session

    with session_scope(engine) as db:
        rows = [
            (r.run_id, r.plan_json, r.results_json, r.updated_at)
            for r in db.exec(select(RunPlan).where(RunPlan.session_id == session_id)).all()
            if (r.plan_json or "[]") != "[]"  # ``has_plan``'s rule
        ]
        if not rows:
            return None
        runs = {
            r.id: (r.created_at, _value(r.state))
            for r in db.exec(
                select(AgentRun).where(AgentRun.id.in_([row[0] for row in rows]))  # type: ignore[union-attr]
            ).all()
        }
        sess = db.get(Session, session_id)
        session_status = _value(sess.status) if sess is not None else ""

    def _when(row: tuple[Any, ...]) -> tuple[Any, Any]:
        # The run's start, then the plan's last write (two runs can start in
        # one clock tick); a missing run row orders by the plan alone.
        created = runs.get(row[0], (None, ""))[0]
        written = _naive(row[3]) or datetime.min
        return (_naive(created) or written, written)

    run_id, plan_text, results_text, _updated = max(rows, key=_when)
    try:
        plan = json.loads(plan_text or "[]")
        results = json.loads(results_text or "[]")
    except (ValueError, TypeError, RecursionError) as exc:
        raise PlanUnreadable(run_id) from exc
    if (
        not isinstance(plan, list)
        or not plan
        or not all(isinstance(s, dict) and isinstance(s.get("goal"), str) for s in plan)
        or not isinstance(results, list)
        or not all(isinstance(r, dict) for r in results)
        or len(results) > len(plan)
    ):
        raise PlanUnreadable(run_id)
    return {
        "run_id": run_id,
        "goals": [s["goal"] for s in plan],
        "results": results,
        "run_state": runs.get(run_id, (None, ""))[1],
        "session_status": session_status,
    }


def plan_checklist(stored: dict[str, Any], *, awaiting_approval: bool = False) -> list[dict[str, str]]:
    """``[{goal, state}]`` for a :func:`latest_plan` answer. Pure.

    A step with a result is ``done`` (it passed), ``failed`` (it ran and did
    not), or ``skipped`` (never attempted: the budget ran out, or the user
    declined the plan). A step without one, while the run is LIVE, is
    ``running`` (the first such step) or ``waiting`` — every one of them
    ``waiting`` while the plan itself awaits the user's Approve. Once the run
    is over they are ``skipped``, except the step a FAILED run was cut off in,
    which is ``failed``."""
    goals = list(stored.get("goals") or [])
    results = list(stored.get("results") or [])
    run_state = str(stored.get("run_state") or "")
    status = str(stored.get("session_status") or "")
    live = status in _LIVE_SESSION and run_state not in _ENDED_RUN
    ended_failed = "failed" in (status, run_state)
    done = len(results)
    out: list[dict[str, str]] = []
    for i, goal in enumerate(goals):
        if i < done:
            r = results[i]
            if r.get("attempted", True) is False:
                state = "skipped"
            elif r.get("ok") is True:
                state = "done"
            else:
                state = "failed"
        elif live:
            state = "running" if i == done and not awaiting_approval else "waiting"
        else:
            state = "failed" if i == done and ended_failed else "skipped"
        out.append({"goal": str(goal), "state": state})
    return out


def load_for_resume(engine: Any, run_id: str, registry: Any = None) -> list[Any] | None:
    """The messages a resumed run starts from, or None when run ``run_id``
    kept none. Every tool call without a result gets one (see the module
    doc); the caller appends its own instruction after them."""
    from ..providers.adapters.base import LLMMessage

    with session_scope(engine) as db:
        row = db.get(RunTranscript, run_id)
        if row is None:
            return None
        text = row.messages_json or "[]"
    try:
        messages = _decode(json.loads(text))
    except ValueError:
        return None
    if not messages:
        return None
    out: list[Any] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        out.append(m)
        i += 1
        if m.role == "assistant" and m.tool_calls:
            answered = set()
            while i < len(messages) and messages[i].role == "tool":
                out.append(messages[i])
                answered.add(messages[i].tool_call_id)
                i += 1
            for call in m.tool_calls:
                if call.id not in answered:
                    out.append(
                        LLMMessage(
                            role="tool",
                            tool_call_id=call.id,
                            name=call.name,
                            content=_cut_off_note(engine, run_id, call, registry),
                        )
                    )
    return out
