"""Leap 06-a: start a rehearsal of a rule or a schedule, and read its report.
Leap 06-b: and of a workflow (``WorkflowEngine`` with the rehearsal on its
run record) or a goal (``GoalEngine.run_iteration(…, rehearsal=)``).

One door for the page (``daemon/routes/rehearsals.py``) and for chat (the
``rehearse`` tool below): :func:`start` runs the subject through ITS OWN door
with the rehearsal on the run (``ReflexRouter.rehearse``,
``platform.rehearse_scheduled``); :func:`report` builds the card from the
ledger's rehearsed rows — the sentence, the counts and every item are counted
from those rows by code, never from the model's closing words; :func:`keep`
stamps ``rehearsed_at`` on the subject; :func:`stop` stops its runs.

Nothing here sends a notice: the person who pressed Rehearse reads the report
where they pressed it (the bell and every destination stay quiet for a
rehearsal's run, ``comm/notifier.is_rehearsal_run``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import Session, ToolInvocation
from . import rehearsal as R
from .base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from .rehearsal_store import RehearsalRecord, RehearsalStore

#: The most items a report lists (the sentence counts them all).
REPORT_ITEMS_MAX = 200
#: A rehearsal still "running" with no run after this long did not start
#: (the daemon stopped before its run was made).
NEVER_STARTED_AFTER = timedelta(minutes=3)

#: Background drivers of schedule rehearsals, by rehearsal id (a strong
#: reference, and what Stop cancels before the run exists).
_DRIVERS: dict[str, "asyncio.Task[Any]"] = {}

_ACTIVE = ("active", "queued")
_KIND_ALIASES = {"rule": "rule", "reflex": "rule", "rules": "rule", "schedule": "schedule", "schedules": "schedule",
                 # Next leaps 19-b: a schedule Iron Jarvis offers (Improvements),
                 # rehearsed through the schedule's own door before it is made.
                 "offer": "offer",
                 # Leap 06-b: a saved workflow and a goal.
                 "workflow": "workflow", "workflows": "workflow", "goal": "goal", "goals": "goal"}
#: The refusal when the kind is not one of these.
UNKNOWN_KIND_WORDS = "Only a rule, a schedule, a workflow or a goal can be rehearsed."
#: The finish a workflow's real run announces (an outgoing webhook may hear it).
_WORKFLOW_FINISH_EVENTS = (
    "workflow.completed", "workflow.step_started", "workflow.step_completed", "workflow.waiting",
)


class RehearsalRefused(Exception):
    """A rehearsal that cannot start, in words for a person (``status`` is
    the HTTP code the route answers with)."""

    def __init__(self, words: str, status: int = 400) -> None:
        super().__init__(words)
        self.words = words
        self.status = status


def _store(platform: Any) -> RehearsalStore:
    return RehearsalStore(platform.engine)


# --------------------------------------------------------------------------- #
# Finding the subject
# --------------------------------------------------------------------------- #


def _find_rule(platform: Any, ident: str) -> Any:
    store = getattr(platform, "reflex", None)
    if store is None:
        return None
    rule = store.get(ident)
    if rule is not None:
        return rule
    want = " ".join(str(ident or "").split()).lower()
    for r in store.list():
        if " ".join(str(r.name or "").split()).lower() == want:
            return r
    return None


def _find_schedule(platform: Any, ident: str) -> Any:
    sched = getattr(platform, "scheduler", None)
    if sched is None:
        return None
    task = sched.get(ident)
    if task is not None:
        return task
    want = " ".join(str(ident or "").split()).lower()
    for t in sched.list():
        if t.id == ident or " ".join(str(t.name or "").split()).lower() == want:
            return t
    return None


def _find_workflow(platform: Any, ident: str) -> Any:
    """A saved workflow by its name (exactly, then ignoring case and spaces)
    or its id. BLOCKING."""
    from ..workflows.store import WorkflowStore

    store = WorkflowStore(platform.engine)
    found = store.get(ident)
    if found is not None:
        return found
    want = " ".join(str(ident or "").split()).lower()
    for w in store.list():
        if w.id == ident or " ".join(str(w.name or "").split()).lower() == want:
            return w
    return None


def _find_goal(platform: Any, ident: str) -> Any:
    """A goal by its id or its name (ignoring case and spaces). BLOCKING."""
    goals = getattr(platform, "goal_engine", None)
    if goals is None:
        return None
    found = goals.store.get(ident)
    if found is not None:
        return found
    want = " ".join(str(ident or "").split()).lower()
    for g in goals.store.list():
        if " ".join(str(g.name or "").split()).lower() == want:
            return g
    return None


def _names(platform: Any, kind: str) -> list[str]:
    try:
        if kind == "rule":
            return [r.name for r in platform.reflex.list() if r.name][:20]
        if kind == "workflow":
            from ..workflows.store import WorkflowStore

            return [w.name for w in WorkflowStore(platform.engine).list()][:20]
        if kind == "goal":
            return [g.name for g in platform.goal_engine.store.list() if g.name][:20]
        return [t.name for t in platform.scheduler.list()][:20]
    except Exception:  # noqa: BLE001
        return []


_PLURAL_KIND = {"rule": "rules", "schedule": "schedules", "workflow": "workflows", "goal": "goals"}


def _destinations_words(platform: Any, payload: dict) -> str:
    """Where a schedule's result goes (its delivery line), in words, or ""
    when it notifies nobody."""
    if payload.get("notify") is False:
        return ""
    where = destination_names(platform, payload.get("notify_channels"))
    return f"send the result to {where}" if where else ""


def destination_names(platform: Any, names: Any = None) -> str:
    """Where a message goes, in words ("this PC and Slack"): ``names``, or
    every destination the notifier sends to; "" when there is none. Never
    raises. (Leap 06-b: a workflow's notify step in a rehearsal says it.)"""
    if not isinstance(names, list) or not names:
        try:
            names = list(platform.notifier.channels())
        except Exception:  # noqa: BLE001
            names = []
    said = []
    for n in names[:4]:
        n = str(n or "").strip()
        if not n:
            continue
        said.append("this PC" if n.lower() in ("desktop", "this_pc", "this-pc") else n[:1].upper() + n[1:])
    if not said:
        return ""
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]


# --------------------------------------------------------------------------- #
# Start, stop, keep
# --------------------------------------------------------------------------- #


async def start(platform: Any, kind: str, ident: str) -> dict[str, Any]:
    """Start a rehearsal of the rule or schedule ``ident`` (its id or its
    name) and return its report (status ``running``, or already settled for
    a rule that hands work to another computer, or ``failed`` in words).
    Raises :class:`RehearsalRefused` for an unknown kind or subject."""
    k = _KIND_ALIASES.get(str(kind or "").strip().lower(), "")
    if not k:
        raise RehearsalRefused(UNKNOWN_KIND_WORDS)
    ident = " ".join(str(ident or "").split())
    if k == "offer":
        return await start_offer(platform, ident)
    if not ident:
        raise RehearsalRefused(f"Say which {k} to rehearse.")
    finder = {"rule": _find_rule, "workflow": _find_workflow, "goal": _find_goal}.get(k, _find_schedule)
    subject = await asyncio.to_thread(finder, platform, ident)
    if subject is None:
        names = await asyncio.to_thread(_names, platform, k)
        listed = ", ".join(f"“{n}”" for n in names)
        what = _PLURAL_KIND.get(k, k + "s")
        raise RehearsalRefused(
            f"There is no {k} called “{ident}”." + (f" Your {what}: {listed}." if listed else ""),
            status=404,
        )
    store = _store(platform)
    if k == "workflow":
        return await _start_workflow(platform, store, subject)
    if k == "goal":
        return await _start_goal(platform, store, subject)
    if k == "rule":
        router = getattr(platform, "reflex_router", None)
        if router is None:
            raise RehearsalRefused("Rules are not running in this copy of Iron Jarvis.", status=409)
        sample = router.sample_for(subject)
        rec = await asyncio.to_thread(
            lambda: store.create(kind="rule", subject_id=subject.id, name=subject.name or subject.id, sample=sample)
        )
        if str(getattr(subject, "action", "") or "") == "session":
            # A side door: an outgoing webhook would hear the task finish.
            # Written BEFORE the run starts, so a report never grows after
            # it says ready (a rule that starts no run of its own, a remote
            # agent's, finishes no task here: no webhook hears it).
            await asyncio.to_thread(_record_webhooks, platform, rec.id)
        res = await router.rehearse(subject, sample, rehearsal=rec.id)
        await asyncio.to_thread(_settle_start, store, rec.id, res)
    else:
        rec = await asyncio.to_thread(
            lambda: store.create(kind="schedule", subject_id=subject.id, name=subject.name, sample={})
        )
        rehearse = getattr(platform, "rehearse_scheduled", None)
        if rehearse is None:
            await asyncio.to_thread(
                lambda: store.update(rec.id, status="failed", finished_at=utcnow(),
                                     summary={"error": "Schedules are not running in this copy of Iron Jarvis."})
            )
        else:
            try:
                payload = json.loads(subject.payload_json or "{}")
            except (TypeError, ValueError):
                payload = {}
            where = _destinations_words(platform, payload if isinstance(payload, dict) else {})
            if where and subject.kind == "task":
                # A side door: the schedule's delivery line sends nothing.
                await asyncio.to_thread(
                    lambda: R.record_side_door(
                        platform.engine, session_id=rec.id, door="schedule_delivery", words=where,
                    )
                )
            if subject.kind == "task":
                # A side door: an outgoing webhook would hear the task finish.
                await asyncio.to_thread(_record_webhooks, platform, rec.id)
            _DRIVERS[rec.id] = asyncio.ensure_future(_drive_schedule(platform, store, rec.id, subject))
    return await asyncio.to_thread(report, platform, rec.id)


#: The events a rule's or a schedule's run sends an outgoing webhook when its
#: task finishes (the Webhooks page's "A task finishes").
_FINISH_EVENTS = ("session.completed", "agent.completed")
#: The most webhooks one report lists.
_WEBHOOKS_MAX = 10


async def start_offer(platform: Any, offer_id: str) -> dict[str, Any]:
    """Next leaps 19-b: rehearse a schedule Iron Jarvis OFFERS (Improvements)
    before it exists: the schedule Accept would save (``scheduling/offers.
    task_of``, not saved) runs through the schedule's own door
    (``platform.rehearse_scheduled``) exactly as a kept schedule's rehearsal
    does, with the same side doors, after the same add-time rules Accept
    applies (an account gone or off; Local only work, read again from its
    chats, whose model leaves this network: refused in words, nothing run).
    One rehearsal of an offer runs at a time: a press while one runs, or
    while another press is starting one, gets that one (a conditional write
    claims the offer first). The offer keeps the newest
    rehearsal's id; its report is ``GET /rehearsals/{id}``."""
    from ..scheduling import offers as O

    oid = " ".join(str(offer_id or "").split())
    if oid.startswith("schedule_offer:"):
        oid = oid.split(":", 1)[1]  # the id suggestion_list shows
    if not oid:
        raise RehearsalRefused("Say which offer to rehearse.")
    row = await asyncio.to_thread(O.get, platform.engine, oid)
    if row is None:
        raise RehearsalRefused("There is no such offer.", status=404)
    if row.status != O.WAITING:
        raise RehearsalRefused("This offer was already answered, so it is not rehearsed.", status=409)
    if O.claim_is_live(row.rehearsal_id):
        raise RehearsalRefused("A rehearsal of it is starting. Its report will show here.", status=409)
    if row.rehearsal_id and not row.rehearsal_id.startswith("claim:"):
        before = await asyncio.to_thread(_store(platform).get, row.rehearsal_id)
        if before is not None and before.status == "running":
            return await asyncio.to_thread(report, platform, before.id)
    rehearse = getattr(platform, "rehearse_scheduled", None)
    if rehearse is None:
        raise RehearsalRefused("Schedules are not running in this copy of Iron Jarvis.", status=409)
    task = await asyncio.to_thread(O.task_of, platform, row)
    try:
        payload = json.loads(task.payload_json or "{}")
        await asyncio.to_thread(O.check, platform, row, task.name, payload)
    except O.OfferRefused as exc:
        raise RehearsalRefused(exc.words, status=exc.status) from None
    # One press at a time (review): the offer's rehearsal is claimed with a
    # conditional write; a press that loses answers the winner's report.
    claim = await asyncio.to_thread(O.claim_rehearsal, platform.engine, row.id, row.rehearsal_id)
    if not claim:
        now = await asyncio.to_thread(O.get, platform.engine, row.id)
        rid = getattr(now, "rehearsal_id", "") or ""
        if rid and not rid.startswith("claim:"):
            return await asyncio.to_thread(report, platform, rid)
        # (a stale claim was taken by the other press, which won)
        raise RehearsalRefused("A rehearsal of it is starting. Its report will show here.", status=409)
    store = _store(platform)
    try:
        rec = await asyncio.to_thread(
            lambda: store.create(kind="offer", subject_id=row.id, name=task.name, sample={})
        )
    except BaseException:
        await asyncio.to_thread(O.set_rehearsal, platform.engine, row.id, row.rehearsal_id)
        raise
    await asyncio.to_thread(O.set_rehearsal, platform.engine, row.id, rec.id)
    where = _destinations_words(platform, payload)
    if where:
        await asyncio.to_thread(
            lambda: R.record_side_door(platform.engine, session_id=rec.id, door="schedule_delivery", words=where)
        )
    await asyncio.to_thread(_record_webhooks, platform, rec.id)
    _DRIVERS[rec.id] = asyncio.ensure_future(_drive_schedule(platform, store, rec.id, task))
    return await asyncio.to_thread(report, platform, rec.id)


def _record_webhooks(
    platform: Any, rid: str, events: "tuple[str, ...]" = _FINISH_EVENTS, what: str = "task"
) -> None:
    """A SIDE DOOR (``rehearsal.SIDE_DOORS["outbound_webhook"]``): every
    outgoing webhook that would hear the task finish (or, ``what`` workflow,
    any of the workflow's own events) is one rehearsed row, written BEFORE the run so the report never
    grows after it is ready. The delivery itself skips every event of a
    rehearsal's run (``webhooks/outbound.on_event``), and a workflow's
    rehearsal publishes no workflow event at all. BLOCKING. Never raises."""
    hooks = getattr(platform, "outbound_webhooks", None)
    if hooks is None:
        return
    try:
        slugs = list(hooks.subscribed_to(events))[:_WEBHOOKS_MAX]
    except Exception:  # noqa: BLE001
        return
    for slug in slugs:
        said = (
            f"tell another app through the webhook “{slug}” how the workflow's run went"
            if what == "workflow" else
            f"tell another app through the webhook “{slug}” that the {what} finished"
        )
        R.record_side_door(
            platform.engine, session_id=rid, door="outbound_webhook",
            words=said,
            target=slug,
        )


# --------------------------------------------------------------------------- #
# Leap 06-b: a workflow and a goal
# --------------------------------------------------------------------------- #

#: Run statuses of a workflow run that has not ended.
_WORKFLOW_LIVE = ("running", "resuming", "cancelling", "waiting")


async def _start_workflow(platform: Any, store: RehearsalStore, subject: Any) -> dict[str, Any]:
    """Rehearse the saved workflow ``subject`` through the workflow engine's
    own door: its run record is made with the rehearsal on it
    (``WorkflowEngine.create_record(…, rehearsal_id=)``), the same checks a
    real run meets refuse it first, and its steps run in the background
    exactly as a real run's would, blocks (If, For each) included. A workflow
    of fixed steps (tools, notify, ask) is rehearsed with no model call."""
    from ..workflows.engine import STARTED_BY_PERSON, WorkflowEngine

    rec = await asyncio.to_thread(
        lambda: store.create(kind="workflow", subject_id=subject.id, name=subject.name, sample={})
    )

    def _failed(words: str) -> None:
        store.update(rec.id, status="failed", finished_at=utcnow(), summary={"error": words})

    try:
        from ..workflows.store import WorkflowStore

        wf = await asyncio.to_thread(WorkflowStore(platform.engine).load_def, subject.name)
    except ValueError as exc:  # a step of a kind this version does not know
        await asyncio.to_thread(_failed, str(exc) or "This workflow cannot run.")
        return await asyncio.to_thread(report, platform, rec.id)
    if wf is None:
        await asyncio.to_thread(_failed, "The workflow it rehearses is no longer there.")
        return await asyncio.to_thread(report, platform, rec.id)
    engine = WorkflowEngine(platform, getattr(platform, "orchestrator", None))
    try:
        run = await asyncio.to_thread(
            lambda: engine.create_record(wf, started_by=STARTED_BY_PERSON, rehearsal_id=rec.id)
        )
    except ValueError as exc:  # no steps, a block that cannot run: the real run's own refusal
        await asyncio.to_thread(_failed, " ".join(str(exc).split()) or "This workflow cannot run.")
        return await asyncio.to_thread(report, platform, rec.id)
    await asyncio.to_thread(lambda: store.update(rec.id, run_id=run.id))
    events = _WORKFLOW_FINISH_EVENTS + (_FINISH_EVENTS if any(_has_agent_step(s) for s in wf.steps) else ())
    await asyncio.to_thread(_record_webhooks, platform, rec.id, events, "workflow")
    _DRIVERS[rec.id] = asyncio.ensure_future(_drive_workflow(platform, store, rec.id, engine, run, wf))
    return await asyncio.to_thread(report, platform, rec.id)


def _has_agent_step(step: Any) -> bool:
    if getattr(step, "kind", "") == "agent":
        return True
    return any(_has_agent_step(s) for s in [*(getattr(step, "steps", None) or []),
                                             *(getattr(step, "else_steps", None) or [])])


def _end_workflow_run(platform: Any, run_id: str) -> None:
    """A stopped rehearsal's workflow run ends ``cancelled`` (never left
    "running"). BLOCKING. Never raises."""
    try:
        from ..workflows.models import WorkflowRunRecord

        with session_scope(platform.engine) as db:
            row = db.get(WorkflowRunRecord, run_id)
            if row is not None and row.status in _WORKFLOW_LIVE:
                row.status = "cancelled"
                row.current_session_id = None
                row.finished_at = row.finished_at or utcnow()
                db.add(row)
                db.commit()
    except Exception:  # noqa: BLE001
        pass


async def _drive_workflow(
    platform: Any, store: RehearsalStore, rid: str, engine: Any, run: Any, wf: Any
) -> None:
    """The workflow's rehearsal, run in the background. Its run record
    settles the report (:func:`_settle_workflow`). A Stop cancels this task;
    the run then ends Stopped."""
    try:
        await engine.run_record(run, wf)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(_end_workflow_run, platform, run.id))
        await asyncio.shield(asyncio.to_thread(
            lambda: store.update(rid, status="stopped", finished_at=utcnow())
        ))
        raise
    except Exception:  # noqa: BLE001 — a step that raises is a failed step
        await asyncio.to_thread(
            lambda: store.update(rid, status="failed", finished_at=utcnow(),
                                 summary={"error": "The rehearsal stopped with an error."})
        )
    finally:
        _DRIVERS.pop(rid, None)


def _settle_workflow(platform: Any, store: RehearsalStore, rec: RehearsalRecord) -> RehearsalRecord:
    """A workflow's rehearsal settles from its RUN RECORD: finished →
    ``ready`` (a step that failed and was gone past is said in ``detail``),
    a step that stopped it → ``failed`` with that step's own words, cancelled
    → ``stopped``. A run left unfinished with no driver (Iron Jarvis
    restarted) → ``failed``. BLOCKING."""
    from ..workflows.models import WorkflowRunRecord
    from ..workflows.step_outcome import step_failures

    if rec.id in _DRIVERS:
        return rec
    run = None
    if rec.run_id:
        with session_scope(platform.engine) as db:
            run = db.get(WorkflowRunRecord, rec.run_id)
            if run is not None:
                db.expunge(run)
    if run is None:
        if rec.created_at and utcnow().replace(tzinfo=None) - rec.created_at.replace(tzinfo=None) > NEVER_STARTED_AFTER:
            return store.update(rec.id, status="failed", finished_at=utcnow(),
                                summary={"error": "The rehearsal did not start."}) or rec
        return rec
    status = str(run.status or "")
    if status == "cancelled":
        return store.update(rec.id, status="stopped", finished_at=utcnow()) or rec
    if status in _WORKFLOW_LIVE or status == "interrupted":
        return store.update(rec.id, status="failed", finished_at=utcnow(), summary={
            "error": "The rehearsal stopped before it finished, when Iron Jarvis restarted."
        }) or rec
    try:
        steps = [str(s.get("name") or "") for s in json.loads(run.steps_json or "[]") if isinstance(s, dict)]
        outputs = json.loads(run.outputs_json or "{}")
    except (TypeError, ValueError):
        steps, outputs = [], {}
    halted, skipped = step_failures(steps, outputs if isinstance(outputs, dict) else {})
    if status == "failed":
        if halted is not None:
            words = f"The step “{halted[0]}” failed: {halted[1].get('summary') or 'it failed'}"
        else:
            words = "Its run could not finish."
        return store.update(rec.id, status="failed", finished_at=utcnow(), summary={"error": words}) or rec
    detail = ""
    if skipped:
        names = ", ".join(f"“{n}”" for n, _ in skipped[:5])
        detail = f"These steps failed and the run went on past them: {names}"
    return store.update(rec.id, status="ready", finished_at=utcnow(), summary={"detail": detail}) or rec


async def _start_goal(platform: Any, store: RehearsalStore, goal: Any) -> dict[str, Any]:
    """Rehearse one try of the goal ``goal`` (``GoalEngine.run_iteration(…,
    rehearsal=)``) in the background: its books are not touched."""
    engine = getattr(platform, "goal_engine", None)
    rec = await asyncio.to_thread(
        lambda: store.create(kind="goal", subject_id=goal.id, name=goal.name or goal.id, sample={})
    )
    if engine is None:
        await asyncio.to_thread(
            lambda: store.update(rec.id, status="failed", finished_at=utcnow(),
                                 summary={"error": "Goals are not running in this copy of Iron Jarvis."})
        )
        return await asyncio.to_thread(report, platform, rec.id)
    # A side door: an outgoing webhook would hear the try's run finish.
    await asyncio.to_thread(_record_webhooks, platform, rec.id)

    async def _one_try() -> dict[str, Any]:
        res = await engine.run_iteration(goal.id, rehearsal=rec.id)
        sid = str(res.get("session_id") or "")
        if res.get("refused"):
            reason = " ".join(str(res.get("reason") or "").split())
            return {"ok": False, "session_id": "", "error": f"A real try would not run: {reason}".rstrip(": ")}
        if not sid:
            return {"ok": False, "session_id": "", "error": str(res.get("reason") or "It could not run.")}
        return {"ok": True, "session_id": sid, "detail": ""}

    _DRIVERS[rec.id] = asyncio.ensure_future(_drive(store, rec.id, _one_try()))
    return await asyncio.to_thread(report, platform, rec.id)


def renew_in(engine: Any, options: dict[str, Any]) -> str:
    """Run again, or a follow-up, of a REHEARSAL's run (``options`` are the
    new run's, copied from the old one's): the new run is a NEW rehearsal of
    the same subject, with its own record and so its own report. It never
    joins the old one: a report the user read or kept never changes under
    them. The new run stays a rehearsal (the safe direction): even when no
    record can be written it gets a fresh id. Returns the new id ("" when
    the old run was no rehearsal). BLOCKING."""
    old = str(options.get(R.REHEARSAL_OPTION) or "").strip() if isinstance(options, dict) else ""
    if not old:
        return ""
    try:
        fresh = RehearsalStore(engine).renew(old).id
    except Exception:  # noqa: BLE001 — still a rehearsal, never the old report
        from ..core.ids import new_id

        fresh = new_id("reh")
    options[R.REHEARSAL_OPTION] = fresh
    return fresh


def _settle_start(store: RehearsalStore, rid: str, res: dict[str, Any]) -> None:
    if not res.get("ok"):
        store.update(rid, status="failed", finished_at=utcnow(),
                     summary={"error": str(res.get("error") or "It could not start.")})
        return
    sid = str(res.get("session_id") or "")
    if sid:
        store.update(rid, session_ids=[sid])
        return
    # Nothing to run (a hand-off to another computer is one rehearsed row).
    store.update(rid, status="ready", finished_at=utcnow(), summary={"detail": ""})


async def _drive_schedule(platform: Any, store: RehearsalStore, rid: str, task: Any) -> None:
    """The schedule's rehearsal, run in the background (its fire awaits the
    run); its result settles the record. A Stop cancels this task."""
    await _drive(store, rid, platform.rehearse_scheduled(task, rid))


async def _drive(store: RehearsalStore, rid: str, work: Any) -> None:
    """Await ``work`` (``{"ok", "session_id", "detail"}`` or ``{"ok": False,
    "error"}``) in the background; its result settles the record (a run it
    started settles the report when it ends). A Stop cancels this task."""
    try:
        res = await work
        sid = str(res.get("session_id") or "")
        if sid:
            await asyncio.to_thread(lambda: store.update(rid, session_ids=[sid]))
        if not res.get("ok"):
            await asyncio.to_thread(
                lambda: store.update(rid, status="failed", finished_at=utcnow(),
                                     summary={"error": str(res.get("error") or "It could not run.")})
            )
        elif not sid:
            await asyncio.to_thread(
                lambda: store.update(rid, status="ready", finished_at=utcnow(),
                                     summary={"detail": str(res.get("detail") or "")})
            )
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(
            lambda: store.update(rid, status="stopped", finished_at=utcnow())
        ))
        raise
    except Exception:  # noqa: BLE001 — a step that raises is a failed step
        await asyncio.to_thread(
            lambda: store.update(rid, status="failed", finished_at=utcnow(),
                                 summary={"error": "The rehearsal stopped with an error."})
        )
    finally:
        _DRIVERS.pop(rid, None)


async def stop(platform: Any, rehearsal_id: str) -> dict[str, Any]:
    """Stop a running rehearsal: its runs are cancelled (they changed
    nothing), and the record says Stopped."""
    store = _store(platform)
    rec = await asyncio.to_thread(store.get, rehearsal_id)
    if rec is None:
        raise RehearsalRefused("There is no such rehearsal.", status=404)
    if rec.status == "running":
        orch = getattr(platform, "orchestrator", None)
        for sid, status in await asyncio.to_thread(_session_states, platform, rec):
            if status in _ACTIVE and orch is not None:
                try:
                    await asyncio.to_thread(orch.cancel_session, sid)
                except (KeyError, ValueError):
                    pass
        driver = _DRIVERS.get(rec.id)
        if driver is not None and not driver.done():
            driver.cancel()
        await asyncio.to_thread(lambda: store.update(rec.id, status="stopped", finished_at=utcnow()))
    return await asyncio.to_thread(report, platform, rec.id)


async def keep(platform: Any, rehearsal_id: str) -> dict[str, Any]:
    """Keep it: the user read the report and keeps the rule or schedule as
    it is — ``rehearsed_at`` is stamped on it. Only a finished report can be
    kept."""
    store = _store(platform)
    rec = await asyncio.to_thread(store.get, rehearsal_id)
    if rec is None:
        raise RehearsalRefused("There is no such rehearsal.", status=404)
    if rec.subject_kind == "offer":
        # 19-b: an offered schedule does not exist yet; Accept makes it.
        raise RehearsalRefused("This schedule is only offered so far. Accept it on Improvements to make it.",
                               status=409)
    view = await asyncio.to_thread(report, platform, rec.id)
    if view["status"] != "ready":
        raise RehearsalRefused("Only a finished rehearsal can be kept.", status=409)
    when = utcnow()
    stamped = await asyncio.to_thread(store.stamp_subject, rec, when)
    if not stamped:
        what = rec.subject_kind if rec.subject_kind in ("rule", "workflow", "goal") else "schedule"
        raise RehearsalRefused(f"The {what} it rehearsed is no longer there.", status=404)
    await asyncio.to_thread(lambda: store.update(rec.id, kept_at=when))
    return await asyncio.to_thread(report, platform, rec.id)


#: A finished report not kept yet is offered again on its page for this
#: long (a running one always is), so a reload or a second Rehearse never
#: loses it.
REOPEN_FOR = timedelta(hours=1)
#: The most reports a page is offered again.
REOPEN_MAX = 5


def open_reports(platform: Any, kind: str) -> list[dict[str, Any]]:
    """The reports of ``kind`` (rule or schedule) still worth showing on its
    page: for each subject its NEWEST rehearsal, when it is still running, or
    finished within :data:`REOPEN_FOR` and not kept. Newest first, at most
    :data:`REOPEN_MAX`. BLOCKING. Raises :class:`RehearsalRefused` for an
    unknown kind."""
    k = _KIND_ALIASES.get(str(kind or "").strip().lower(), "")
    if not k:
        raise RehearsalRefused(UNKNOWN_KIND_WORDS)
    since = utcnow().replace(tzinfo=None) - REOPEN_FOR
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for rec in _store(platform).newest(k):
        if rec.subject_id in seen:
            continue
        seen.add(rec.subject_id)
        ended = rec.finished_at.replace(tzinfo=None) if rec.finished_at else None
        if rec.status != "running" and (
            rec.status != "ready" or rec.kept_at or (ended is not None and ended < since)
        ):
            # Review 6: a finished report that is kept, old or not ready is
            # never offered, so its card (a read of the run table and the
            # ledger) is not built. Only a running one can still settle.
            continue
        view = report(platform, rec.id)
        recent = view["status"] == "ready" and not view["kept_at"] and (ended is None or ended >= since)
        if view["status"] == "running" or recent:
            out.append(view)
        if len(out) >= REOPEN_MAX:
            break
    return out


# --------------------------------------------------------------------------- #
# The report, from the ledger
# --------------------------------------------------------------------------- #


def sessions_of(platform: Any, rec: RehearsalRecord) -> list[str]:
    """Every run of this rehearsal: the ones it started and every helper's
    run that inherited it (found by its own options). BLOCKING."""
    out = list(RehearsalStore.sessions(rec))
    needle = f'"{R.REHEARSAL_OPTION}": "{rec.id}"'
    try:
        with session_scope(platform.engine) as db:
            rows = db.exec(
                select(Session.id).where(Session.options_json.contains(needle, autoescape=True))  # type: ignore[union-attr]
            ).all()
        for sid in rows:
            sid = sid if isinstance(sid, str) else sid[0]
            if sid not in out:
                out.append(sid)
    except Exception:  # noqa: BLE001 — the started runs still answer
        pass
    return out


def _session_states(platform: Any, rec: RehearsalRecord) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with session_scope(platform.engine) as db:
        for sid in sessions_of(platform, rec):
            row = db.get(Session, sid)
            if row is not None:
                out.append((sid, str(getattr(row.status, "value", row.status) or "")))
    return out


def _settle(platform: Any, store: RehearsalStore, rec: RehearsalRecord) -> RehearsalRecord:
    """A running rehearsal whose runs have all ended settles: ``ready``,
    ``failed`` (one failed: its own words) or ``stopped``. BLOCKING."""
    if rec.status != "running":
        return rec
    if rec.subject_kind == "workflow":
        return _settle_workflow(platform, store, rec)
    states = _session_states(platform, rec)
    if not states:
        if rec.id in _DRIVERS:
            return rec
        if rec.created_at and utcnow().replace(tzinfo=None) - rec.created_at.replace(tzinfo=None) > NEVER_STARTED_AFTER:
            return store.update(rec.id, status="failed", finished_at=utcnow(),
                                summary={"error": "The rehearsal did not start."}) or rec
        return rec
    if any(s in _ACTIVE for _, s in states):
        return rec
    if rec.id in _DRIVERS:
        return rec  # the schedule's fire is still writing its result
    failed = [sid for sid, s in states if s == "failed"]
    if failed:
        words = ""
        with session_scope(platform.engine) as db:
            row = db.get(Session, failed[0])
            words = " ".join(str(getattr(row, "summary", "") or "").split())[:300] if row else ""
        return store.update(rec.id, status="failed", finished_at=utcnow(),
                            summary={"error": words or "Its run could not finish."}) or rec
    if all(s == "cancelled" for _, s in states):
        return store.update(rec.id, status="stopped", finished_at=utcnow()) or rec
    return store.update(rec.id, status="ready", finished_at=utcnow()) or rec


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _mask(text: str) -> str:
    """``text`` with every secret-looking value masked (``detections.redact``,
    the one mask the record readers use). Never raises."""
    try:
        from ..detections.redact import mask

        return str(mask(text) if text else text)
    except Exception:  # noqa: BLE001 — cannot mask: say nothing of it
        return "" if text else text


#: Why a step would be refused, in the Handbook's words ("a tool the agent
#: does not have, a tool set to Never run"), for the two refusals the gate
#: writes in its own words (``registry.invoke``'s roster line, the permission
#: engine's deny). Leap 06-a, the 390 px look: the card showed the ledger's
#: "`write_file` is not one of this agent's tools", backticks and all.
NOT_ITS_TOOL = "the agent that runs it does not have this tool"
SET_TO_NEVER = "it is set to Never run"


def _refused_why(output: Any) -> str:
    """The reason a refused step shows on the card: the two gate refusals in
    plain words, any other its own sentence masked (a lane's refusal is
    already written for the person), never with a code mark."""
    text = " ".join(str(output or "").split())
    if "is not one of this agent's tools" in text:
        return NOT_ITS_TOOL
    if text == "denied by policy":
        return SET_TO_NEVER
    return _mask(text.replace("`", "")[:200])


def _sentence_case(text: str) -> str:
    """``nothing new to review`` → ``Nothing new to review.``"""
    text = " ".join(text.split())
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _is_read(platform: Any, name: str | None) -> bool:
    """Did a call to ``name`` that RAN in a rehearsal only read? A tool the
    rehearsal would not hold because it only reads — never a helper's
    hand-off, the run's own list or a side door ("It read N things")."""
    tool_name = str(name or "")
    if not tool_name or tool_name in R.REHEARSAL_HELPERS or tool_name in R.REHEARSAL_OWN_STATE:
        return False
    if tool_name in R.SIDE_DOORS:
        return False
    try:
        tool = platform.registry.get(tool_name)
    except Exception:  # noqa: BLE001
        tool = None
    return tool is not None and not R.holds(tool)


def report(platform: Any, rehearsal_id: str) -> dict[str, Any]:
    """The rehearsal's card: ``{id, subject, status, sentence, counts, would,
    refused, reads, guessed_after, error, kept_at, …}``. Every number and
    item is read from the ledger's rehearsed rows of its runs. BLOCKING.
    Raises :class:`RehearsalRefused` (404) for an unknown id."""
    store = _store(platform)
    rec = store.get(rehearsal_id)
    if rec is None:
        raise RehearsalRefused("There is no such rehearsal.", status=404)
    rec = _settle(platform, store, rec)
    sessions = sessions_of(platform, rec)
    # Leap 06-b: a workflow's tool steps, notify and ask are recorded under
    # its RUN's id (their ``ToolContext.session_id``).
    run_ids = [rec.run_id] if rec.subject_kind == "workflow" and rec.run_id else []
    with session_scope(platform.engine) as db:
        rows = list(db.exec(
            select(ToolInvocation)
            .where(ToolInvocation.session_id.in_([*sessions, *run_ids, rec.id]))  # type: ignore[union-attr]
            .order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
        ).all())
        for r in rows:
            db.expunge(r)
    would: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    counted: list[R.Effect] = []
    counts: dict[str, int] = {}
    reads = 0
    # No run of its own started (a schedule whose fire had nothing to run, a
    # rule that could not start): no task finished, so no webhook heard one.
    ran = bool(sessions) or bool(run_ids)
    # Leap 06-b: the order of reads and held changes, for ``guessed_after``.
    order: list[tuple[str, bool]] = []
    stopped_at: dict[str, Any] | None = None
    for row in rows:
        if not ran and row.session_id == rec.id and row.tool == "outbound_webhook":
            continue
        if not row.rehearsed:
            if not row.undo_of and _is_read(platform, row.tool):
                if row.ok:
                    reads += 1
                # A read that failed (a file a held write would have made)
                # was a read all the same.
                order.append(("read", False))
            continue
        try:
            args = json.loads(row.args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        effect = R.effect_of(row.tool, args, platform.registry.get(row.tool) if row.tool else None)
        if (row.would or "run") != "refuse":
            order.append((row.tool if row.tool in R.AFTER_THE_RUN else effect.kind, True))
        if row.tool == "workflow_ask" and stopped_at is None:
            after = args.get("after") if isinstance(args.get("after"), list) else []
            stopped_at = {
                "question": _mask(str(effect.words or "").removeprefix("ask you: ")),
                "not_rehearsed": [_mask(str(a)) for a in after if str(a).strip()][:20],
            }
        # An item quotes a command or a message: every secret-looking value
        # in it is masked (the shared mask), on the card and on the phone.
        item = {"kind": effect.kind, "words": _mask(effect.words), "target": _mask(effect.target),
                "would": row.would or "run"}
        if (row.would or "run") == "refuse":
            if len(refused) < REPORT_ITEMS_MAX:
                refused.append({**item, "why": _refused_why(row.output)})
            continue
        counted.append(effect)
        if effect.noun:
            counts[effect.kind] = counts.get(effect.kind, 0) + 1
        if len(would) < REPORT_ITEMS_MAX:
            would.append(item)
    summary = RehearsalStore.summary(rec)
    made_up = RehearsalStore.sample_is_made_up(rec)
    status = rec.status
    guessed = R.guessed_after(order)
    if status == "ready":
        said = R.sentence(counted)
    elif status == "stopped":
        said = "Stopped. Nothing was changed."
    elif status == "failed":
        said = "The rehearsal could not finish. Nothing was changed."
    else:
        said = "Rehearsing. Nothing is being changed."
    return {
        "id": rec.id,
        "subject": {"kind": rec.subject_kind, "id": rec.subject_id, "name": rec.subject_name},
        "status": status,
        "sentence": said,
        "counts": counts,
        "would": would,
        "would_total": len(counted),
        "refused": refused,
        "reads": reads,
        # Leap 06-b: a step that read came after a held change, so it read
        # what the change would have changed, unchanged.
        "guessed_after": guessed,
        "guessed_words": R.GUESSED_AFTER_WORDS if guessed else "",
        # Leap 06-b: a workflow's rehearsal that ended at its question, and
        # what came after it (not rehearsed).
        "stopped_at": stopped_at,
        "stopped_words": _stopped_words(stopped_at),
        "error": _mask(str(summary.get("error") or "")),
        # Why no run started, in the schedule row's own words ("nothing new
        # to review"): only for a finished rehearsal that started none. A
        # workflow's: the steps that failed and were gone past.
        "detail": _mask(_sentence_case(str(summary.get("detail") or ""))) if status == "ready" and (
            not ran or rec.subject_kind == "workflow"
        ) else "",
        "sample": "" if rec.subject_kind != "rule" else (
            "a made-up signal" if made_up else "the last signal it matched"
        ),
        "kept_at": _iso(rec.kept_at),
        "created_at": _iso(rec.created_at),
        "finished_at": _iso(rec.finished_at),
    }


def _stopped_words(stopped_at: "dict[str, Any] | None") -> str:
    """The line under a workflow's report that ended at its question."""
    if not stopped_at:
        return ""
    after = list(stopped_at.get("not_rehearsed") or [])
    if not after:
        return "It stopped where it would ask you. Nothing comes after the question."
    listed = after[0] if len(after) == 1 else ", ".join(after[:-1]) + " and " + after[-1]
    return f"It stopped where it would ask you, so what comes after was not rehearsed: {listed}."


# --------------------------------------------------------------------------- #
# Chat: "rehearse it"
# --------------------------------------------------------------------------- #


class RehearseTool(Tool):
    """``rehearse {kind, name}``: start a rehearsal of a rule or a schedule
    from chat. It changes nothing (allow), so it is READONLY; a rehearsal
    cannot start another one. The result carries the report card
    (``data.rehearsal``), drawn by both chat lanes (``config_cards``)."""

    name = "rehearse"
    description = (
        "Rehearse one of the user's rules, schedules, workflows or goals: run "
        "it once for real reading, but change nothing — every file it would "
        "write, move or change, every message it would send and every command "
        "it would run is listed instead of done (a goal's try spends nothing "
        "of its budget). Use when the user asks to rehearse, try out, dry-run "
        "or test one of them without it doing anything. "
        "`kind` is rule, schedule, workflow or goal; `name` is its name as the "
        "user said it. "
        "A schedule Iron Jarvis OFFERS to run by itself (an Improvements suggestion "
        "whose id starts with schedule_offer:) is rehearsed with `kind` offer and "
        "`name` that id. "
        "The report shows on a card when the rehearsal ends; where no card "
        "can show (the phone), the result waits for the report and says it."
    )
    permission_key = "rehearse"
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    #: Each call starts a run (it uses the model once): a call a restart cut
    #: off is never started again by itself.
    replay = False
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["rule", "schedule", "workflow", "goal", "offer"]},
            "name": {"type": "string", "description": "The rule's, schedule's, workflow's or goal's name."},
        },
        "required": ["kind", "name"],
    }

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if getattr(ctx, "rehearsal_id", ""):
            return ToolResult(ok=False, error="A rehearsal cannot start another rehearsal.")
        kind = str(args.get("kind") or "")
        name = str(args.get("name") or "")
        try:
            view = await start(self.platform, kind, name)
        except RehearsalRefused as exc:
            return ToolResult(ok=False, error=exc.words)
        subject = view["subject"]["name"] or name
        what = _subject_word(view["subject"]["kind"])
        gave_up = False
        if getattr(ctx, "cannot_ask", False) and view["status"] == "running":
            # The phone (the POST lane) draws no card and a rehearsal sends
            # no notice, so the reply WAITS for the report and says it. One
            # that outlasts the wait is stopped: nobody could read its report.
            view = await _wait_for_report(self.platform, view["id"])
            if view["status"] == "running":
                view = await stop(self.platform, view["id"])
                gave_up = True
        if view["status"] == "failed":
            return ToolResult(
                ok=False,
                error=f"The rehearsal of the {what} “{subject}” could not run: {view['error']}",
            )
        data = {"rehearsal": {"id": view["id"], "kind": view["subject"]["kind"], "name": subject}}
        if getattr(ctx, "cannot_ask", False):
            return ToolResult(ok=True, output=_report_words(view, what, subject, gave_up), data=data)
        lead = (
            f"Rehearsing the {what} “{subject}”. Nothing will be changed; the report "
            "will show on the card when it is done."
            if view["status"] == "running"
            else f"Rehearsed the {what} “{subject}”. {view['sentence']}"
        )
        return ToolResult(ok=True, output=lead, data=data)


#: On the phone (``ctx.cannot_ask``: a lane that draws no card) the reply
#: waits for the report at most this long, and never past the call's own
#: deadline (``tool_call_timeout_s``, 600 s by default); then it says the
#: report in words.
PHONE_WAIT_S = 540.0
#: How often the waiting reply reads the report again.
PHONE_POLL_S = 1.0
#: How many items the phone's reply lists (the sentence counts them all).
PHONE_ITEMS = 10


async def _wait_for_report(platform: Any, rehearsal_id: str) -> dict[str, Any]:
    from .registry import tool_deadline_left

    loop = asyncio.get_running_loop()
    budget = PHONE_WAIT_S
    left = tool_deadline_left()
    if left is not None:
        budget = min(budget, max(0.0, left - 10.0))
    end = loop.time() + budget
    view = await asyncio.to_thread(report, platform, rehearsal_id)
    while view["status"] == "running" and loop.time() < end:
        await asyncio.sleep(PHONE_POLL_S)
        view = await asyncio.to_thread(report, platform, rehearsal_id)
    return view


def _subject_word(kind: str) -> str:
    """The word a reply uses for what was rehearsed."""
    return kind if kind in ("rule", "workflow", "goal") else "schedule"


#: The page each kind is rehearsed on (the phone's reply names it).
_PAGE_OF = {"rule": "Rules", "workflow": "Workflows", "goal": "Autonomy", "offer": "Improvements"}


def _report_words(view: dict[str, Any], what: str, subject: str, gave_up: bool = False) -> str:
    """The report as a reply's words (the phone has no card)."""
    page = _PAGE_OF.get(str(view.get("subject", {}).get("kind") or ""), "Schedules")
    status = view["status"]
    if gave_up:
        return (
            f"The rehearsal of the {what} “{subject}” took longer than I can wait here, "
            f"so I stopped it. Nothing was changed. Rehearse it on the {page} page to "
            "see it through to the end."
        )
    if status == "stopped":
        return f"The rehearsal of the {what} “{subject}” was stopped. Nothing was changed."
    lines = [f"Rehearsed the {what} “{subject}”. {view['sentence']}"]
    if view.get("detail"):
        lines.append(str(view["detail"]))
    items = list(view.get("would") or [])
    for item in items[:PHONE_ITEMS]:
        asks = " (it would ask you first)" if item.get("would") == "ask" else ""
        lines.append(f"- Would {item['words']}{asks}")
    more = int(view.get("would_total") or len(items)) - min(len(items), PHONE_ITEMS)
    if more > 0:
        lines.append(f"- and {more} more")
    refused = list(view.get("refused") or [])
    if refused:
        lines.append("Would be refused: " + "; ".join(r["words"] for r in refused[:PHONE_ITEMS]) + ".")
    if view.get("stopped_words"):
        lines.append(str(view["stopped_words"]))
    if view.get("guessed_words"):
        lines.append(str(view["guessed_words"]))
    return "\n".join(lines)


def rehearsal_tools(platform: Any) -> list[Tool]:
    return [RehearseTool(platform)]
