"""Leap 06-a: start a rehearsal of a rule or a schedule, and read its report.

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
_KIND_ALIASES = {"rule": "rule", "reflex": "rule", "rules": "rule", "schedule": "schedule", "schedules": "schedule"}


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


def _names(platform: Any, kind: str) -> list[str]:
    try:
        if kind == "rule":
            return [r.name for r in platform.reflex.list() if r.name][:20]
        return [t.name for t in platform.scheduler.list()][:20]
    except Exception:  # noqa: BLE001
        return []


def _destinations_words(platform: Any, payload: dict) -> str:
    """Where a schedule's result goes (its delivery line), in words, or ""
    when it notifies nobody."""
    if payload.get("notify") is False:
        return ""
    names = payload.get("notify_channels")
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
        said.append("this PC" if n.lower() in ("desktop", "this_pc") else n[:1].upper() + n[1:])
    if not said:
        return ""
    where = said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]
    return f"send the result to {where}"


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
        raise RehearsalRefused("Only a rule or a schedule can be rehearsed for now.")
    ident = " ".join(str(ident or "").split())
    if not ident:
        raise RehearsalRefused(f"Say which {k} to rehearse.")
    finder = _find_rule if k == "rule" else _find_schedule
    subject = await asyncio.to_thread(finder, platform, ident)
    if subject is None:
        names = await asyncio.to_thread(_names, platform, k)
        listed = ", ".join(f"“{n}”" for n in names)
        what = "rules" if k == "rule" else "schedules"
        raise RehearsalRefused(
            f"There is no {k} called “{ident}”." + (f" Your {what}: {listed}." if listed else ""),
            status=404,
        )
    store = _store(platform)
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


def _record_webhooks(platform: Any, rid: str) -> None:
    """A SIDE DOOR (``rehearsal.SIDE_DOORS["outbound_webhook"]``): every
    outgoing webhook that would hear the task finish is one rehearsed row,
    written BEFORE the run so the report never grows after it is ready.
    The delivery itself skips every event of a rehearsal's run
    (``webhooks/outbound.on_event``). BLOCKING. Never raises."""
    hooks = getattr(platform, "outbound_webhooks", None)
    if hooks is None:
        return
    try:
        slugs = list(hooks.subscribed_to(_FINISH_EVENTS))[:_WEBHOOKS_MAX]
    except Exception:  # noqa: BLE001
        return
    for slug in slugs:
        R.record_side_door(
            platform.engine, session_id=rid, door="outbound_webhook",
            words=f"tell another app through the webhook “{slug}” that the task finished",
            target=slug,
        )


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
    try:
        res = await platform.rehearse_scheduled(task, rid)
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
    view = await asyncio.to_thread(report, platform, rec.id)
    if view["status"] != "ready":
        raise RehearsalRefused("Only a finished rehearsal can be kept.", status=409)
    when = utcnow()
    stamped = await asyncio.to_thread(store.stamp_subject, rec, when)
    if not stamped:
        what = "rule" if rec.subject_kind == "rule" else "schedule"
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
        raise RehearsalRefused("Only a rule or a schedule can be rehearsed for now.")
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
    with session_scope(platform.engine) as db:
        rows = list(db.exec(
            select(ToolInvocation)
            .where(ToolInvocation.session_id.in_([*sessions, rec.id]))  # type: ignore[union-attr]
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
    ran = bool(sessions)
    for row in rows:
        if not ran and row.session_id == rec.id and row.tool == "outbound_webhook":
            continue
        if not row.rehearsed:
            if row.ok and not row.undo_of and _is_read(platform, row.tool):
                reads += 1
            continue
        try:
            args = json.loads(row.args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        effect = R.effect_of(row.tool, args, platform.registry.get(row.tool) if row.tool else None)
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
        # 06-b says when steps after a held change were worked out without it.
        "guessed_after": False,
        "error": _mask(str(summary.get("error") or "")),
        # Why no run started, in the schedule row's own words ("nothing new
        # to review"): only for a finished rehearsal that started none.
        "detail": _mask(_sentence_case(str(summary.get("detail") or ""))) if status == "ready" and not ran else "",
        "sample": "" if rec.subject_kind != "rule" else (
            "a made-up signal" if made_up else "the last signal it matched"
        ),
        "kept_at": _iso(rec.kept_at),
        "created_at": _iso(rec.created_at),
        "finished_at": _iso(rec.finished_at),
    }


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
        "Rehearse one of the user's rules or schedules: run it once for real "
        "reading, but change nothing — every file it would write, move or "
        "change, every message it would send and every command it would run "
        "is listed instead of done. Use when the user asks to rehearse, try "
        "out, dry-run or test a rule or a schedule without it doing anything. "
        "`kind` is rule or schedule; `name` is its name as the user said it. "
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
            "kind": {"type": "string", "enum": ["rule", "schedule"]},
            "name": {"type": "string", "description": "The rule's or schedule's name."},
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
        what = "rule" if view["subject"]["kind"] == "rule" else "schedule"
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


def _report_words(view: dict[str, Any], what: str, subject: str, gave_up: bool = False) -> str:
    """The report as a reply's words (the phone has no card)."""
    page = "Rules" if what == "rule" else "Schedules"
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
    return "\n".join(lines)


def rehearsal_tools(platform: Any) -> list[Tool]:
    return [RehearseTool(platform)]
