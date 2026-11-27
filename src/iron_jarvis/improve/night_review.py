"""THE NIGHT REVIEW (17-a, build-out plan R1.8, idea 17).

Each night, while the PC has nothing else to do and well inside the owner's
plan limits, Iron Jarvis looks at what went wrong during the day and runs the
right suggestion maker for each problem. The suggestions wait in
Improvements, each saying why it was made, and ONE bell row says how many
there are. Nothing changes until the owner accepts one.

WHEN A PASS RUNS (:func:`review_pass`). ALL of these hold, read at the start
of the pass and again before each suggestion maker:

* the setting is on (``config.night_review is True``). Not set (``None``,
  the default) means it is OFFERED once on Improvements (D-17.1: "up to 6
  requests a night on your plan") and nothing runs until the owner says yes;
* Emergency stop is off;
* the night window is open (``harness.night.window_open``, 23:00 to 07:00
  by default), read through :data:`CLOCK` (tests set it; never a sleep);
* Iron Jarvis is idle: ``routes/system.activity_snapshot`` says nothing is
  ``working`` (no live run, no running workflow, no chat or coding reply,
  no working terminal), no chat changed in the last :data:`IDLE_CHAT_S` (30
  minutes), no night task is running. Work parked on the owner with no
  clock does not hold it (a workflow run waiting for an answer, an attended
  run on its card, a terminal showing its question, a dev server left
  running: ``busy`` counts those, ``working`` does not);
* tonight's review has not finished (its ``nightreview`` row is not done or
  skipped). A pass cut off midway (the PC became busy, the window closed, a
  restart) leaves its row ``waiting`` with what it already ran and why it
  stopped, and the next pass carries on from there without running anything
  twice.

A NIGHT THE WINDOW CLOSED ON (:func:`_close_cut_off`): the first look after
a night's window closed ends a row of that night still ``waiting`` (or
``running`` with no pass in this process: a restart) with what it ran, plus
one ``cut off`` entry saying why it stopped, and ends ``done`` (never
``skipped``: the jobs it never reached might have asked a model). The rest
of that night's work is not run (17-a catches nothing up), and the night's
ONE bell row rings for
what it made, the way a finished night's does. A night first seen only after
the next window opened (the PC was off all day) is ended without a ring, so
two nights never ring in one. Its suggestions still wait on Improvements,
and the page's line counts them only while that night is still last night
(:func:`last_night`): until the next night's review has finished, or the
next night is over. After that the line speaks of the newer night (or is
gone when that night made nothing), and the earlier suggestions stay in
the list.

ONE PASS = gather, match, run (one suggestion maker at a time):

1. GATHER, from the records only (never a model): yesterday's agent runs,
   per roster name, with the coach's own signals (``coach/signals.
   run_signals``) and clusters (``coach/taxonomy.clusters``); the skill
   drafts still waiting to be written (``SkillCandidateRecord`` pending).
   "Yesterday" is the 24 hours before tonight's window opened.
2. MATCH, the fixed table :data:`PROPOSERS`: the tool-failure scan (no
   model; the daily scan in ``daemon/app._tool_failure_pass`` then leaves a
   day the review covered alone); a CUSTOM agent with a pattern in
   yesterday's runs gets the coach; a BUILT-IN agent with the same pattern
   gets nothing from the coach (its instructions are the app's) and is only
   named in the night's record; ONE waiting skill draft gets one distill.
   Ideas 19, 20 and 21 add rows here; none has its own night loop.
3. RUN, each through ``improve/budget.may_spend`` with the model that did
   the work (the run's own provider and model, on its own account): pinned,
   no failover, inside ``background_call.background("night_review")`` (a
   failed request rings nothing), inside ``egress.local_only_scope`` when the
   work was Local only (the router's own gate refuses a model off this
   network), counted against the night's cap right before it is sent, and
   bounded (:data:`CALL_TIMEOUT_S`; cancelled at once by Emergency stop).
4. RESULTS (18-a), once the pass has run every job: ``improve/results.
   refresh`` makes and measures the result of each change the owner
   accepted (no model, its own table only, never raises into the pass).

THE RECORD (``improve/models.NightReviewRecord``): what each suggestion maker
did and why, the suggestions made (``[{kind, ref, why}]``, by the
Improvements kind and the suggestion's own id), the requests spent. The
Improvements inbox reads it to say why each suggestion was made
(``inbox/collect``: a read, so the inbox still decides nothing), and
:func:`page_view` is ``GET /improvements/night``: the one-time offer, and
the page's top line, shown only when last night's review made something that
still waits, or was skipped for a reason only the owner can change.

THE NOTICE: ``improvement.night_review`` with ``{night, made, by_kind,
title, body}`` and never a suggestion's words, published ONCE per night when
the pass ends having made something (claim first, given back when the
publish fails, the ``routes/night._announce_once`` pattern). The bell and the
desktop app's tray say it; it is not a default alert, so no phone gets it
(D-17.3: the phone line is 17-b, and only once the owner turns it on).
``config.night_review_notice`` off: the night is marked told and nothing
rings. It is the night's ONLY notice: the suggestion makers it runs publish
none of their own (``coach.propose(announce=False)``, whose
``coach.proposal`` is a bell row that pings, and ``distill_candidates(
announce=False)``, whose ``skill.proposal_created`` is a default phone
alert).

Every database read here is BLOCKING (SQLite): the async functions hop with
``asyncio.to_thread``. Nothing here raises into the daemon's loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from . import budget as B
from .models import DONE, RUNNING, SKIPPED, WAITING, NightReviewRecord

log = logging.getLogger(__name__)

#: The LOCAL clock a pass reads (aware). None = ``harness.night.now_local``
#: (whose own ``CLOCK`` seam tests may set instead).
CLOCK: "Callable[[], datetime] | None" = None

#: The loop: the first look after start, then one look every few minutes.
#: With the setting off a look reads no table; outside the window it reads
#: the unfinished rows of nights already over (one indexed query, ended
#: there) and one row (a notice still owed is rung then).
FIRST_PASS_S = 120.0
PASS_EVERY_S = 300.0
#: No chat may have changed for this long before a pass runs.
IDLE_CHAT_S = 1800.0
#: Yesterday's agent runs read at most, and per agent.
RUNS_MAX = 200
PER_AGENT = 25
#: Skill drafts written in one night (the plan: "skill candidates get one
#: distill").
SKILL_DRAFTS = 1
#: Waiting skill drafts looked at to find the one the budget allows.
SKILL_LOOK = 20
#: 19-a: drafts from repeated chat tasks written in one night (one request
#: each, inside the night's cap).
REPEAT_DRAFTS = 2
#: One suggestion maker's time limit (its model request included).
CALL_TIMEOUT_S = 180.0
#: Night rows read for the reasons shown on Improvements.
REASON_NIGHTS = 14

# What a pass that did not run says (``review_pass``'s ``skipped``).
SKIP_STOPPED = "Emergency stop is on"
SKIP_WINDOW = "outside the night window"
SKIP_BUSY = "Iron Jarvis is working"
SKIP_RECENT_CHAT = "a chat was used in the last 30 minutes"
SKIP_NIGHT_TASK = "a night task is running"
SKIP_DONE = "tonight's review is done"
SKIP_RUNNING = "a review pass is already running"
SKIP_UNREADABLE = "Iron Jarvis could not tell whether the PC is idle"
STOP_OFF = "the night review was switched off"

# What one suggestion maker did (``ran_json[*].result``), and the one entry
# a night the window closed on ends with (``CUT_OFF``).
MADE, NOTHING, REFUSED, NAMED, CUT_OFF = "made", "nothing", "refused", "named", "cut off"

WHY_BUILT_IN = "built-in agents are not coached: their instructions are the app's"
WHY_REMOTE = "an agent on another computer is coached on that computer"
WHY_NO_COACH = "the coach is not available"
WHY_NO_SKILLS = "skill drafts are not available"
WHY_TIMEOUT = "the model did not answer in time"
WHY_FAILED = "the model did not answer"
WHY_KEPT = "the draft was kept without asking (Approve learned skills automatically is on)"
WHY_CUT_OFF = "the night window closed before the review finished"

#: The suggestion makers, in the order a pass runs them: ``(name, what it
#: does)``. Ideas 19, 20 and 21 add rows (and a runner in ``_RUNNERS``).
PROPOSERS: tuple[tuple[str, str], ...] = (
    ("tool_failures", "the tool-failure scan: a tool that kept failing this week (no model)"),
    ("coach", "the coach: a custom agent with a pattern in yesterday's runs"),
    ("skill", "one skill draft that was waiting to be written"),
    # Next leaps 19-a: the same kind of task asked for in chat on three
    # different days (``learning/repeats``, no model) becomes one draft,
    # written on the model of the newest of those chats.
    ("repeat", "a skill draft from the same kind of task asked for in chat on three different days"),
    # Next leaps 19-b: when those three times fell on a rhythm (the same
    # weekday each week), an offer to run the task by itself on it, beside
    # the draft. No model, no schedule, no rehearsal: the owner's Accept
    # makes the schedule and their Rehearse runs the rehearsal.
    ("schedule_offer", "an offer to run a repeated task by itself, when the times it was asked for fell on a rhythm"),
)

#: The coach's categories as the reason on a suggestion (``{n}`` is how
#: many of yesterday's runs showed it).
_CATEGORY_WORDS: dict[str, Callable[[int], str]] = {
    "scope-creep": lambda n: f"ran out of steps {_times(n)}",
    "avoidable-rework": lambda n: f"had the same tool fail again and again {_in_runs(n)}",
    "tool-misuse": lambda n: f"had calls refused or used a tool wrongly {_in_runs(n)}",
    "late-escalation": lambda n: f"asked for you only near the end of its steps {_in_runs(n)}",
    "instruction-miss": lambda n: f"left a question unanswered, or failed without using a tool, {_in_runs(n)}",
    "stale-context": lambda n: f"read the same file again and again {_in_runs(n)}",
    "human-correction": lambda n: f"had to be picked up again by you {_times(n)}",
    "verifier-miss": lambda n: f"said it was done when it was not, {_in_runs(n)}",
}

#: ``True`` while this process runs a pass (one at a time).
_IN_PASS = False


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _in_runs(n: int) -> str:
    return "in one run" if n == 1 else f"in {n} runs"


def reset() -> None:
    """Tests: forget this process's pass flag and the injected clock."""
    global _IN_PASS, CLOCK
    _IN_PASS = False
    CLOCK = None


# --------------------------------------------------------------------------- #
# the clock, the night and the day before it
# --------------------------------------------------------------------------- #
def now_local() -> datetime:
    clock = CLOCK
    if clock is not None:
        return clock()
    from ..harness import night as N

    return N.now_local()


def _utc(at: datetime) -> datetime:
    """An aware time as naive UTC (the database's own convention)."""
    if at.tzinfo is None:
        return at
    return at.astimezone(timezone.utc).replace(tzinfo=None)


def _naive(at: Any) -> "datetime | None":
    if not isinstance(at, datetime):
        return None
    return _utc(at) if at.tzinfo is not None else at


def night_of(config: Any, at: datetime) -> str:
    from ..harness import night as N
    from ..harness.night_morning import night_key

    start, end = N.window_of(config)
    return night_key(at, start, end)


def _opened(config: Any, at: datetime) -> datetime:
    """When the night window last opened, at or before ``at`` (local)."""
    from ..harness import night as N
    from ..harness.night_window import clean_time

    start, _end = N.window_of(config)
    h, m = (clean_time(start) or "23:00").split(":")
    opened = at.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    if opened > at:
        opened -= timedelta(days=1)
    return opened


def period_of(config: Any, at: datetime) -> tuple[datetime, datetime]:
    """"Yesterday" for tonight's review, as naive UTC: the 24 hours before
    the window opened. Each moment belongs to exactly one night."""
    opened = _opened(config, at)
    return _utc(opened - timedelta(days=1)), _utc(opened)


def enabled(config: Any) -> bool:
    return getattr(config, "night_review", None) is True


def _stopped(config: Any) -> bool:
    try:
        from ..agents.orchestrator import emergency_stop_on

        return bool(emergency_stop_on(config))
    except Exception:  # noqa: BLE001 — cannot read the switch: hold
        return True


# --------------------------------------------------------------------------- #
# is Iron Jarvis idle?
# --------------------------------------------------------------------------- #
def busy_reason(d: Any, at: datetime) -> str:
    """"" when nothing is running and nobody used a chat lately; else why the
    pass waits. BLOCKING; never raises (a reading that fails waits)."""
    try:
        from sqlalchemy import func, or_

        from ..core.models import ChatThreadRecord
        from ..daemon.routes.system import activity_snapshot
        from ..harness.night import RUNNING as NIGHT_RUNNING, NightTaskRecord

        # ``working``, never ``busy``: ``busy`` also counts work parked on
        # the owner with no clock (a workflow run waiting for an answer, an
        # attended run on its card, a terminal showing its question, a dev
        # server left running), so one card open overnight held every pass
        # and nothing said why. ``working`` is the part that runs by itself
        # (``routes/system._working``, the sleep blocker's own reading).
        if activity_snapshot(d).get("working"):
            return SKIP_BUSY
        engine = d.platform.engine
        with session_scope(engine) as db:
            # A thread from before owners were kept has NULL there and reads
            # as the user's (core/models.ChatThreadRecord.owner): in SQL
            # ``owner != 'daemon'`` is NULL for it, so it is named.
            newest = db.exec(
                select(func.max(ChatThreadRecord.updated_at)).where(
                    or_(ChatThreadRecord.owner.is_(None), ChatThreadRecord.owner != "daemon")  # type: ignore[union-attr]
                )
            ).one()
            night_task = db.exec(
                select(NightTaskRecord.id).where(NightTaskRecord.state == NIGHT_RUNNING)
            ).first()
        newest = newest[0] if isinstance(newest, tuple) else newest
        latest = _naive(newest)
        if latest is not None and latest > _utc(at) - timedelta(seconds=IDLE_CHAT_S):
            return SKIP_RECENT_CHAT
        if night_task is not None:
            return SKIP_NIGHT_TASK
        return ""
    except Exception:  # noqa: BLE001
        log.debug("night review: idle could not be read", exc_info=True)
        return SKIP_UNREADABLE


# --------------------------------------------------------------------------- #
# gather and match (BLOCKING; no model)
# --------------------------------------------------------------------------- #
def _agent_name(roster: str) -> str:
    return roster.split(":", 1)[1] if ":" in roster else roster


def coach_why(roster: str, category: str, n: int) -> str:
    """"Your agent research ran out of steps 4 times yesterday."""
    words = _CATEGORY_WORDS.get(category)
    phrase = words(n) if words is not None else f"had the same trouble {_in_runs(n)}"
    return f"Your agent {_agent_name(roster)} {phrase} yesterday."


def _coach_jobs(platform: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """One job per agent whose runs in [start, end) show a pattern."""
    from ..coach.signals import run_signals
    from ..coach.taxonomy import clusters
    from ..core.models import Session, SessionStatus
    from ..scheduling.knobs import session_options

    with session_scope(platform.engine) as db:
        rows = list(db.exec(
            select(Session)
            .where(Session.created_at >= start, Session.created_at < end, Session.agent_name != "")
            .where(Session.status.notin_([SessionStatus.ACTIVE, SessionStatus.QUEUED]))  # type: ignore[attr-defined]
            .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
            .limit(RUNS_MAX)
        ))
        for r in rows:
            db.expunge(r)
    # Leap 06-a: a REHEARSAL's run did nothing and was told to carry on as if
    # it had; no agent is coached from it (``coach/signals.recent_runs`` keeps
    # it out the same way).
    from ..tools.rehearsal import rehearsal_of

    rows = [r for r in rows if not rehearsal_of(r)]
    by_agent: dict[str, list[Any]] = {}
    for r in rows:
        by_agent.setdefault(str(r.agent_name), []).append(r)
    coach = getattr(platform, "coach", None)
    jobs: list[dict[str, Any]] = []
    for roster, runs in by_agent.items():
        runs = runs[:PER_AGENT]
        signals = []
        for s in runs:
            try:
                signals.append(run_signals(platform, s))
            except Exception:  # noqa: BLE001 — one odd run never hides the rest
                log.debug("night review: signals of %s could not be read", s.id, exc_info=True)
        found = clusters(signals)
        if not found:
            continue
        top = found[0]
        newest = runs[0]
        if roster.startswith("custom:") and coach is not None and coach._slug(roster) is not None:
            kind = "custom"
        elif roster.startswith("remote:"):
            kind = "remote"
        elif roster.startswith("custom:"):
            continue  # an agent that no longer exists
        else:
            kind = "builtin"
        try:
            account = str(session_options(newest).get("account") or "").strip()
        except Exception:  # noqa: BLE001
            account = ""
        jobs.append({
            "key": f"coach:{roster}", "proposer": "coach", "target": roster, "kind": kind,
            "provider": str(newest.provider or ""), "model": str(newest.model or ""), "account": account,
            "local_only": any(bool(sig.get("local_only", True)) for sig in signals),
            "weight": float(top.weight), "why": coach_why(roster, top.category, int(top.count)),
        })
    jobs.sort(key=lambda j: (-j["weight"], j["target"]))
    return jobs


def _one_line(text: Any, cap: int = 80) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= cap else words[: cap - 1].rstrip() + "…"


def _skill_jobs(platform: Any, night: str = "") -> list[dict[str, Any]]:
    """The :data:`SKILL_DRAFTS` oldest skill drafts still waiting to be
    written that the budget allows tonight (``budget.may_spend``), out of the
    :data:`SKILL_LOOK` oldest. A draft the budget refuses (its run's model
    bills per use, was picked by Auto, a Local only run's model off this
    network) never holds up the newer ones behind it. When the budget
    refuses every one, the oldest is still the job, so the night's record
    says why (the cap reached, the same reason for all)."""
    if getattr(platform, "skill_learning", None) is None:
        return []
    from ..core.models import Session
    from ..scheduling.knobs import session_options
    from ..skills.learning_models import SkillCandidateRecord as C

    from ..skills.learning import _not_chat

    out: list[dict[str, Any]] = []
    with session_scope(platform.engine) as db:
        # 19-a: a chat candidate is the ``repeat`` row's (its own model).
        cands = list(db.exec(
            select(C).where(C.status == "pending", _not_chat(C.source))
            .order_by(C.created_at.asc()).limit(SKILL_LOOK)  # type: ignore[attr-defined]
        ))
        for cand in cands:
            sess = db.get(Session, cand.session_id)
            try:
                from ..agents.orchestrator import session_local_only

                local = bool(session_local_only(sess)) if sess is not None else False
                account = str(session_options(sess).get("account") or "").strip() if sess is not None else ""
            except Exception:  # noqa: BLE001 — fail closed: the work stays home
                local, account = True, ""
            task = _one_line(cand.task or (sess.task if sess is not None else ""))
            out.append({
                "key": f"skill:{cand.id}", "proposer": "skill", "target": cand.id,
                "provider": str(getattr(sess, "provider", "") or ""), "model": str(getattr(sess, "model", "") or ""),
                "account": account, "local_only": local,
                "why": (f"A run did something worth keeping as a skill, and its draft was waiting: “{task}”."
                        if task else "A run did something worth keeping as a skill, and its draft was waiting."),
            })
    allowed = [
        job for job in out
        if not B.may_spend(platform, provider=job["provider"], model=job["model"], account=job["account"],
                           local_only=bool(job["local_only"]), night=night)
    ]
    return (allowed or out)[:SKILL_DRAFTS]


def repeat_why(n: int) -> str:
    """Why the review made a draft from repeated chat tasks (the item's own
    evidence says the days and the tools)."""
    return f"Written from the {int(n)} times you asked for the same kind of task in chat."


def _repeat_jobs(platform: Any, end: datetime, night: str = "") -> list[dict[str, Any]]:
    """19-a: first the repeat detector (``learning/repeats.detect``: the
    chats of the 35 days before ``end``, no model; it adds a chat candidate
    per new repeat and never raises), then one job per waiting chat
    candidate, at most :data:`REPEAT_DRAFTS`, each pinned to the model of
    its NEWEST chat task (provider, model AND account) and Local only
    when any of its chats was. The
    budget's refusals are handled as :func:`_skill_jobs` does."""
    if getattr(platform, "skill_learning", None) is None:
        return []
    from ..learning import repeats
    from ..skills.learning_models import SOURCE_CHAT, SkillCandidateRecord as C

    repeats.detect(platform, now=end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end)
    out: list[dict[str, Any]] = []
    with session_scope(platform.engine) as db:
        cands = list(db.exec(
            select(C).where(C.status == "pending", C.source == SOURCE_CHAT)
            .order_by(C.created_at.asc()).limit(SKILL_LOOK)  # type: ignore[attr-defined]
        ))
    for cand in cands:
        evidence = repeats.load_evidence(cand.evidence_json)
        units = [u for u in evidence.get("units") or [] if isinstance(u, dict)]
        newest = units[0] if units else {}
        out.append({
            "key": f"repeat:{cand.id}", "proposer": "repeat", "target": cand.id,
            "provider": str(newest.get("provider") or ""), "model": str(newest.get("model") or ""),
            # The account the newest chat ran on (never the vendor's Default
            # in its place): ``_arm_account`` pins it, and an account that is
            # gone or turned off refuses the night's draft; no other login.
            "account": str(newest.get("account") or ""),
            # Fail closed: a unit that does not say is Local only.
            "local_only": (not units) or any(bool(u.get("local_only", True)) for u in units),
            "why": repeat_why(len(units)),
        })
    allowed = [
        job for job in out
        if not B.may_spend(platform, provider=job["provider"], model=job["model"], account=job["account"],
                           local_only=bool(job["local_only"]), night=night)
    ]
    return (allowed or out)[:REPEAT_DRAFTS]


def match(platform: Any, start: datetime, end: datetime, night: str = "") -> list[dict[str, Any]]:
    """Tonight's jobs, in :data:`PROPOSERS` order. BLOCKING; RAISES when the
    records cannot be read (the pass then waits). The one write: the repeat
    detector (19-a) adds a chat candidate for each NEW repeat it finds (no
    model; the same repeat is never added twice)."""
    jobs: list[dict[str, Any]] = []
    if getattr(platform, "improvement", None) is not None:
        jobs.append({"key": "tool_failures", "proposer": "tool_failures", "target": ""})
    jobs += _coach_jobs(platform, start, end)
    jobs += _skill_jobs(platform, night)
    jobs += _repeat_jobs(platform, end, night)
    if getattr(platform, "skill_learning", None) is not None:
        # 19-b: after the repeat drafts, so tonight's draft gets its offer.
        jobs.append({"key": "schedule_offer", "proposer": "schedule_offer", "target": ""})
    return jobs


# --------------------------------------------------------------------------- #
# the record (BLOCKING)
# --------------------------------------------------------------------------- #
def _loads(text: Any) -> list[dict[str, Any]]:
    try:
        got = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return [g for g in got if isinstance(g, dict)] if isinstance(got, list) else []


def _state_of(engine: Any, night: str) -> str:
    with session_scope(engine) as db:
        row = db.get(NightReviewRecord, night)
        return str(row.state) if row is not None else ""


def _begin(engine: Any, night: str) -> "dict[str, Any] | None":
    """Mark ``night`` running; what an earlier, cut-off pass already did, or
    None when the night is already finished."""
    with session_scope(engine) as db:
        row = db.get(NightReviewRecord, night)
        if row is None:
            row = NightReviewRecord(night=night, state=RUNNING, started_at=utcnow())
        elif row.state in (DONE, SKIPPED):
            return None
        else:
            row.state = RUNNING
            row.started_at = row.started_at or utcnow()
        db.add(row)
        db.commit()
        return {"ran": _loads(row.ran_json), "made": _loads(row.made_json)}


def _save(engine: Any, night: str, *, state: str, ran: list[dict[str, Any]], made: list[dict[str, Any]],
          requests: int, skipped_why: str = "", needs_you: bool = False, ended: bool = False) -> None:
    with session_scope(engine) as db:
        row = db.get(NightReviewRecord, night) or NightReviewRecord(night=night, started_at=utcnow())
        row.state = state
        row.ran_json = json.dumps(ran)
        row.made_json = json.dumps(made)
        row.requests = int(requests)
        row.skipped_why = skipped_why
        row.needs_you = bool(needs_you)
        if ended:
            row.ended_at = utcnow()
        db.add(row)
        db.commit()


def _close_cut_off(engine: Any, config: Any, at: datetime) -> list[str]:
    """End every night whose window has closed at ``at`` while its review was
    still ``waiting`` (cut off: the PC stayed busy, the window closed, the
    setting went off) or ``running`` (a restart: the caller runs no pass).
    Each keeps what it ran and made, gains one ``cut off`` entry with why it
    last stopped, and ends ``done``: never ``skipped``, since a night cut off
    before its last job cannot say that EVERY request it would have sent was
    refused (a refusal it met stays in ``ran_json``). The rest of its work is
    not run. Returns the nights ended. BLOCKING; RAISES when the record
    cannot be read."""
    from ..harness import night as N

    current = night_of(config, at)
    with session_scope(engine) as db:
        stmt = select(NightReviewRecord).where(
            NightReviewRecord.state.in_([WAITING, RUNNING])  # type: ignore[attr-defined]
        )
        # While the window is open, tonight is still tonight's to finish.
        if N.window_open(config, at):
            stmt = stmt.where(NightReviewRecord.night < current)
        else:
            stmt = stmt.where(NightReviewRecord.night <= current)
        ended: list[str] = []
        for row in list(db.exec(stmt)):
            ran = _loads(row.ran_json)
            stopped = str(row.skipped_why or "")
            ran.append({"proposer": "night", "target": "", "result": CUT_OFF,
                        "reason": f"{WHY_CUT_OFF} ({stopped})" if stopped else WHY_CUT_OFF, "key": "cut_off"})
            row.state, row.skipped_why, row.needs_you = DONE, "", False
            row.ran_json = json.dumps(ran)
            row.ended_at = utcnow()
            db.add(row)
            ended.append(str(row.night))
        if ended:
            db.commit()
    for night in ended:
        log.info("night review %s: ended when its window closed", night)
    return ended


def reviewed_within(engine: Any, *, hours: float = 24.0, now: "datetime | None" = None) -> bool:
    """Did a night review that RAN the tool-failure scan finish in the last
    ``hours``? (The daily tool-failure scan leaves such a day alone. A night
    the window closed on before the scan ran did not cover the day.)
    BLOCKING; never raises (False when it cannot tell, so the daily scan
    runs)."""
    try:
        moment = _naive(now) if now is not None else _naive(utcnow())
        since = moment - timedelta(hours=float(hours))
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(NightReviewRecord.ran_json)
                .where(NightReviewRecord.state.in_([DONE, SKIPPED]))  # type: ignore[attr-defined]
                .where(NightReviewRecord.ended_at >= since)  # type: ignore[operator]
            ))
        return any(str(r.get("key") or "") == "tool_failures" for text in rows for r in _loads(text))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# run one suggestion maker
# --------------------------------------------------------------------------- #
def _entry(job: dict[str, Any], result: str, reason: str = "") -> dict[str, Any]:
    return {"proposer": job["proposer"], "target": job.get("target", ""), "result": result,
            "reason": reason, "key": job["key"]}


async def _bounded(platform: Any, work: Awaitable[Any]) -> Any:
    """``work`` with :data:`CALL_TIMEOUT_S`, cancelled at once when Emergency
    stop is turned on. RAISES ``TimeoutError`` / ``_Stopped``."""
    task = asyncio.ensure_future(work)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CALL_TIMEOUT_S
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=1.0)
            if done:
                return task.result()
            if _stopped(getattr(platform, "config", None)):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise _Stopped(SKIP_STOPPED)
            if loop.time() >= deadline:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise TimeoutError(WHY_TIMEOUT)
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        raise


class _Stopped(Exception):
    """Emergency stop during a request: the pass stops here."""


class _Refused(Exception):
    """The budget said no right before a request was sent."""


def _gate(platform: Any, job: dict[str, Any], night: str) -> Callable[..., Awaitable[str]]:
    """The check right before ONE request is sent: the budget again (the cap
    included), then the request is counted. "" = send it. ``local_only``:
    the suggestion maker's own reading of the material it sends (the coach's
    evidence reaches past yesterday), added to the job's, so an off-network
    model is refused BEFORE a request is counted."""
    home = getattr(getattr(platform, "config", None), "home", None)

    async def check(*, local_only: bool = False) -> str:
        why = await asyncio.to_thread(
            B.may_spend, platform, provider=job["provider"], model=job["model"],
            account=job.get("account", ""), local_only=bool(job.get("local_only")) or bool(local_only),
            night=night,
        )
        if why:
            return why
        await asyncio.to_thread(B.count, home, night)
        return ""

    return check


async def _run_tool_failures(platform: Any, job: dict[str, Any], night: str) -> tuple[dict[str, Any], list]:
    from ..improvement.engine import _tool_name
    from ..motivation.models import ProposalRecord

    improvement = getattr(platform, "improvement", None)
    if improvement is None:
        return _entry(job, NOTHING), []

    def scan() -> list[dict[str, Any]]:
        # Only a suggestion THIS scan made is the night's. The scan also hands
        # back the id of one made earlier this week for the same failures
        # (one per week, ``_mint_failure_proposal``): still waiting (the daily
        # scan made it) or already answered. Every id from before counts as
        # old, whatever its status.
        with session_scope(platform.engine) as db:
            before = set(db.exec(select(ProposalRecord.id)))
        found = list(improvement.scan_tool_failures() or [])
        return [f for f in found if f.get("proposal_id") and f["proposal_id"] not in before]

    new = await asyncio.to_thread(scan)
    made = []
    for f in new:
        name = _tool_name(str(f.get("tool") or "")) or "A tool"
        name = name[0].upper() + name[1:]
        made.append({"kind": "wants", "ref": str(f["proposal_id"]),
                     "why": f"{name} failed {int(f.get('failures') or 0)} times in the last week."})
    return _entry(job, MADE if made else NOTHING), made


async def _run_coach(platform: Any, job: dict[str, Any], night: str) -> tuple[dict[str, Any], list]:
    if job.get("kind") == "builtin":
        return _entry(job, NAMED, WHY_BUILT_IN), []
    if job.get("kind") == "remote":
        return _entry(job, NAMED, WHY_REMOTE), []
    coach = getattr(platform, "coach", None)
    if coach is None:
        return _entry(job, NOTHING, WHY_NO_COACH), []
    why = await asyncio.to_thread(
        B.may_spend, platform, provider=job["provider"], model=job["model"], account=job.get("account", ""),
        local_only=bool(job.get("local_only")), night=night,
    )
    if why:
        return _entry(job, REFUSED, why), []
    check = _gate(platform, job, night)
    refused = {"why": ""}

    async def before_call(*, local_only: bool = False) -> str:
        # ``local_only``: the coach's evidence reading (a Local only run from
        # before yesterday included), heard before the request is counted.
        refused["why"] = await check(local_only=local_only)
        return refused["why"]

    # announce=False: the coach's own ``coach.proposal`` is a bell row that
    # pings; the night's ONE row (``improvement.night_review``) says it.
    record = await coach.propose(
        job["target"], pin=(job["provider"], job["model"], job.get("account", "")),
        before_call=before_call, local_only=bool(job.get("local_only")), announce=False,
    )
    if record is not None:
        return _entry(job, MADE), [{"kind": "coach", "ref": str(record.id), "why": job["why"]}]
    if refused["why"]:
        return _entry(job, REFUSED, refused["why"]), []
    return _entry(job, NOTHING, coach.last_reason_for(job["target"])), []


async def _run_skill(platform: Any, job: dict[str, Any], night: str) -> tuple[dict[str, Any], list]:
    from ..providers import egress as _egress
    from ..providers.adapters.base import LLMMessage

    learning = getattr(platform, "skill_learning", None)
    router = getattr(platform, "router", None)
    if learning is None or router is None:
        return _entry(job, NOTHING, WHY_NO_SKILLS), []
    why = await asyncio.to_thread(
        B.may_spend, platform, provider=job["provider"], model=job["model"], account=job.get("account", ""),
        local_only=bool(job.get("local_only")), night=night,
    )
    if why:
        return _entry(job, REFUSED, why), []
    check = _gate(platform, job, night)

    async def complete(system: str, prompt: str) -> str:
        # The account first: one that cannot be used now refuses before the
        # budget counts a request that would never be sent.
        token = await _arm_account(platform, job)
        try:
            refused = await check()
            if refused:
                raise _Refused(refused)
            # Never switches Local only OFF: the sweep's own scope (the
            # candidate's run) stays on when it is on.
            local = bool(job.get("local_only")) or _egress.current_scope().on
            with _egress.local_only_scope(local):
                route = await router.complete(
                    provider=job["provider"], model=job["model"] or None, system=system,
                    messages=[LLMMessage(role="user", content=prompt)], tools=[], session_id=None, pin=True,
                )
        finally:
            _disarm(token)
        return str(getattr(getattr(route, "response", route), "text", "") or "")

    try:
        # announce=False: ``skill.proposal_created`` is a default phone alert
        # (D-17.3: nothing from the night review reaches a phone in 17-a);
        # the night's ONE bell row says it.
        out = await learning.distill_candidates(complete, limit=1, ids=[job["target"]], announce=False)
    except _Refused as exc:
        return _entry(job, REFUSED, str(exc)), []
    except Exception as exc:  # noqa: BLE001 — a failed draft is a reason, never a crash
        refusal = _egress.refusal_of(exc)
        if refusal is not None:
            return _entry(job, REFUSED, B.WHY_LOCAL.format(name=B._name(job["provider"], platform))), []
        log.info("night review: the skill draft failed", exc_info=True)
        return _entry(job, NOTHING, WHY_FAILED), []
    ids = [str(i) for i in (out or {}).get("proposals") or [] if i]
    # Only a draft that WAITS on Improvements is a suggestion: one the
    # owner's "Approve learned skills automatically" kept unasked is not.
    waiting = await asyncio.to_thread(_waiting_refs, platform.engine, "skill", ids) if ids else set()
    made = [{"kind": "skill", "ref": i, "why": job["why"]} for i in ids if i in waiting]
    if made:
        return _entry(job, MADE), made
    reason = WHY_KEPT if ids else str((out or {}).get("note") or "")
    return _entry(job, NOTHING, reason), []


#: 19-b: why the review made an offer to run a task by itself (the item's
#: own lines say the days, the tools and the rhythm).
OFFER_WHY = "Offered beside the skill draft written from the same chats."


async def _run_schedule_offer(platform: Any, job: dict[str, Any], night: str) -> tuple[dict[str, Any], list]:
    """19-b: an offer for each new chat skill draft whose tasks fell on a
    rhythm (``scheduling/offers.make_offers``). No model, nothing scheduled,
    nothing rehearsed."""
    from ..scheduling import offers as O

    made = await asyncio.to_thread(O.make_offers, platform)
    out = [{"kind": "schedule_offer", "ref": str(o.id), "why": OFFER_WHY} for o in made]
    return _entry(job, MADE if out else NOTHING), out


async def _arm_account(platform: Any, job: dict[str, Any]) -> Any:
    """The account pin for the work's own account (``iron_proxy.choice.
    pin_for``), or None with no account. RAISES ``_Refused`` when that
    account cannot be used now (never another login)."""
    account = str(job.get("account") or "").strip()
    if not account:
        return None
    from ..iron_proxy import choice as _choice
    from ..iron_proxy.pin import set_pin

    pin, why = await asyncio.to_thread(
        _choice.pin_for, platform, _choice.clean(account), job["provider"], job["model"], who="agent",
    )
    if pin is None:
        raise _Refused(why or "its account cannot be used now")
    return set_pin(pin)


def _disarm(token: Any) -> None:
    if token is None:
        return
    try:
        from ..iron_proxy.pin import reset_pin

        reset_pin(token)
    except Exception:  # noqa: BLE001
        pass


_RUNNERS: dict[str, Callable[[Any, dict[str, Any], str], Awaitable[tuple[dict[str, Any], list]]]] = {
    "tool_failures": _run_tool_failures,
    "coach": _run_coach,
    "skill": _run_skill,
    # 19-a: a chat candidate is drafted by the same runner (the skill
    # learner reads its chats; the job carries the newest chat's model).
    "repeat": _run_skill,
    # 19-b: no model.
    "schedule_offer": _run_schedule_offer,
}
assert set(_RUNNERS) == {name for name, _ in PROPOSERS}


async def _run(platform: Any, job: dict[str, Any], night: str) -> tuple[dict[str, Any], list]:
    """One suggestion maker, bounded, as background work. Never raises
    (except a cancel and an Emergency stop, which ends the pass)."""
    from ..providers.background_call import background

    runner = _RUNNERS.get(job["proposer"])
    if runner is None:
        return _entry(job, NOTHING), []
    try:
        with background("night_review"):
            return await _bounded(platform, runner(platform, job, night))
    except (asyncio.CancelledError, _Stopped):
        raise
    except TimeoutError:
        return _entry(job, NOTHING, WHY_TIMEOUT), []
    except Exception:  # noqa: BLE001 — one suggestion maker never breaks the night
        log.warning("night review: %s failed", job["key"], exc_info=True)
        return _entry(job, NOTHING, WHY_FAILED), []


# --------------------------------------------------------------------------- #
# the notice
# --------------------------------------------------------------------------- #
def notice_title(made: int) -> str:
    """The bell's row, the tray's notice: "3 suggestions from yesterday's
    review" (never a suggestion's own words)."""
    n = int(made)
    return f"{n} suggestion{'' if n == 1 else 's'} from yesterday's review"


NOTICE_BODY = "Accept or decline each one on Improvements."


def notice_payload(night: str, made: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    for m in made:
        kind = str(m.get("kind") or "")
        if kind:
            by_kind[kind] = by_kind.get(kind, 0) + 1
    return {"night": night, "made": len(made), "by_kind": by_kind,
            "title": notice_title(len(made)), "body": NOTICE_BODY}


def _to_tell(engine: Any, night: str) -> "list[dict[str, Any]] | None":
    """The night's made suggestions, when its notice is still owed."""
    with session_scope(engine) as db:
        row = db.get(NightReviewRecord, night)
        if row is None or row.state not in (DONE, SKIPPED) or row.notified_at is not None:
            return None
        made = _loads(row.made_json)
    return made or None


def _claim(engine: Any, night: str) -> bool:
    from sqlalchemy import update

    with session_scope(engine) as db:
        res = db.exec(
            update(NightReviewRecord)  # type: ignore[call-overload]
            .where(NightReviewRecord.night == night, NightReviewRecord.notified_at.is_(None))  # type: ignore[union-attr]
            .values(notified_at=utcnow())
        )
        db.commit()
        return bool(getattr(res, "rowcount", 0))


def _release(engine: Any, night: str) -> None:
    from sqlalchemy import update

    try:
        with session_scope(engine) as db:
            db.exec(
                update(NightReviewRecord)  # type: ignore[call-overload]
                .where(NightReviewRecord.night == night).values(notified_at=None)
            )
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("night review %s: its notice could not be given back", night, exc_info=True)


async def announce_once(platform: Any, night: str) -> bool:
    """Ring night ``night``'s ONE bell row, if it made something and was not
    told yet: the claim is written FIRST, and given back when the publish
    fails (the next pass tries again). With ``night_review_notice`` off the
    night is marked told and nothing rings. Never raises."""
    from ..core.events import EventType

    engine = platform.engine
    try:
        made = await asyncio.to_thread(_to_tell, engine, night)
        if not made:
            return False
        if not await asyncio.to_thread(_claim, engine, night):
            return False
        if getattr(platform.config, "night_review_notice", True) is False:
            return False
        bus = getattr(platform, "event_bus", None)
        if bus is None:
            await asyncio.to_thread(_release, engine, night)
            return False
        try:
            await bus.publish(EventType.IMPROVEMENT_NIGHT_REVIEW, notice_payload(night, made), session_id=None)
            return True
        except Exception:  # noqa: BLE001 — the night's record stands; the next pass rings
            log.warning("night review %s: the notice could not be sent yet", night, exc_info=True)
            await asyncio.to_thread(_release, engine, night)
            return False
    except Exception:  # noqa: BLE001
        log.warning("night review %s: the notice failed", night, exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# the pass and the loop
# --------------------------------------------------------------------------- #
async def _stop_reason(d: Any, at: datetime) -> str:
    """Why the pass must stop before its next suggestion maker, read at
    ``at`` (the clock read again, never the pass's start): the setting,
    Emergency stop, the night window, idle. "" = carry on."""
    from ..harness import night as N

    config = d.platform.config
    if not enabled(config):
        return STOP_OFF
    if _stopped(config):
        return SKIP_STOPPED
    if not N.window_open(config, at):
        return SKIP_WINDOW
    return await asyncio.to_thread(busy_reason, d, at)


#: The suggestion makers that send a model request, and the answers of
#: theirs that mean no request was ever going to be sent (the maker is not
#: there at all).
_ASKS_A_MODEL = frozenset({"coach", "skill", "repeat"})
_UNAVAILABLE = frozenset({WHY_NO_COACH, WHY_NO_SKILLS})


def _would_ask(entry: dict[str, Any]) -> bool:
    """Was this entry a job that would have sent a model request? (A built-in
    or remote agent only named, the tool-failure scan and a maker that is not
    there send none.)"""
    if str(entry.get("proposer") or "") not in _ASKS_A_MODEL:
        return False
    if entry.get("result") not in (MADE, NOTHING, REFUSED):
        return False
    return str(entry.get("reason") or "") not in _UNAVAILABLE


def _finish(ran: list[dict[str, Any]], made: list[dict[str, Any]]) -> tuple[str, str, bool]:
    """``(state, skipped_why, needs_you)`` for a pass that ran every job (a
    night cut off before its last job ends ``done``, :func:`_close_cut_off`).
    SKIPPED only by the record's contract (``improve/models``): it made
    nothing because EVERY request it would have sent was refused. A night
    where one agent was refused and another was reviewed (and the model found
    nothing to suggest) RAN: it is ``done``, and the refusal stays in
    ``ran_json`` with its reason."""
    if made:
        return DONE, "", False
    asked = [r for r in ran if _would_ask(r)]
    if not asked or any(r.get("result") != REFUSED for r in asked):
        return DONE, "", False
    refused = [str(r.get("reason") or "") for r in asked if r.get("reason")]
    if not refused:
        return DONE, "", False
    mine = [r for r in refused if B.needs_you(r)]
    return SKIPPED, (mine or refused)[0], bool(mine)


async def _refresh_results(engine: Any, at: datetime) -> None:
    """18-a (``improve/results.refresh``): make and measure the results of
    the changes the owner accepted, off the loop. Reads the records and
    writes its own tables only; never raises into the pass. What it could
    not read is kept as the last count (``results.last_check``): Improvements
    and chat name it, and the daily pass counts again and reports it on its
    health row (it covers the day only when every record was read)."""
    try:
        from . import results as RS

        got = await asyncio.to_thread(RS.refresh, engine, now=_utc(at))
        if got.get("unreadable"):
            log.warning("night review: results of %s could not be read", ", ".join(got["unreadable"]))
    except Exception:  # noqa: BLE001 — a result is never worth a failed night
        log.warning("night review: the results of accepted changes were not measured", exc_info=True)


async def review_pass(d: Any, *, now: "datetime | None" = None) -> "dict[str, Any] | None":
    """ONE look. None = the review is off or not answered yet (the loop then
    clears its health row). Otherwise what happened:
    ``{"skipped": why}`` (nothing ran), ``{"stopped": why, ...}`` (it began
    and stopped before it finished: the next pass carries on), or ``{"state",
    "night", "made", "requests"}``. A suggestion maker's own failure never
    raises; a record that cannot be READ does (the loop's health row)."""
    global _IN_PASS
    platform = d.platform
    config = platform.config
    if not enabled(config):
        return None
    if _stopped(config):
        return {"skipped": SKIP_STOPPED}
    # The clock is read again before each suggestion maker (the window may
    # close mid-pass); a test's fixed ``now`` stays fixed.
    clock: Callable[[], datetime] = (lambda: now) if now is not None else now_local
    at = clock()
    from ..harness import night as N

    engine = platform.engine
    night = night_of(config, at)
    if not _IN_PASS:
        # A night whose window closed before its review finished ends here,
        # with what it ran (the next lines ring what it made).
        await asyncio.to_thread(_close_cut_off, engine, config, at)
    if not N.window_open(config, at):
        # A notice still owed (the publish failed, or Iron Jarvis stopped
        # between the night's end and its notice) is rung the next morning:
        # one row read.
        if not _IN_PASS:
            await announce_once(platform, night)
        return {"skipped": SKIP_WINDOW}
    if _IN_PASS:
        return {"skipped": SKIP_RUNNING}
    _IN_PASS = True
    home = getattr(config, "home", None)
    try:
        state = await asyncio.to_thread(_state_of, engine, night)
        if state in (DONE, SKIPPED):
            await announce_once(platform, night)
            return {"skipped": SKIP_DONE, "night": night}
        busy = await asyncio.to_thread(busy_reason, d, at)
        if busy:
            return {"skipped": busy, "night": night}
        prior = await asyncio.to_thread(_begin, engine, night)
        if prior is None:
            return {"skipped": SKIP_DONE, "night": night}
        ran: list[dict[str, Any]] = prior["ran"]
        made: list[dict[str, Any]] = prior["made"]
        start, end = period_of(config, at)
        try:
            jobs = await asyncio.to_thread(match, platform, start, end, night)
        except Exception:
            spent = await asyncio.to_thread(B.requests_tonight, home, night)
            await asyncio.to_thread(_save, engine, night, state=WAITING, ran=ran, made=made, requests=spent)
            raise
        done_keys = {str(r.get("key") or "") for r in ran}
        for job in jobs:
            if job["key"] in done_keys:
                continue
            stop = await _stop_reason(d, clock())
            if not stop:
                try:
                    entry, new = await _run(platform, job, night)
                except _Stopped:
                    stop = SKIP_STOPPED
            if stop:
                spent = await asyncio.to_thread(B.requests_tonight, home, night)
                # Why it stopped is kept: a night the window then closes on
                # says it in its ``cut off`` entry (:func:`_close_cut_off`).
                await asyncio.to_thread(_save, engine, night, state=WAITING, ran=ran, made=made, requests=spent,
                                        skipped_why=stop)
                return {"stopped": stop, "night": night, "made": len(made), "requests": spent}
            ran.append(entry)
            made += new
            done_keys.add(job["key"])
            spent = await asyncio.to_thread(B.requests_tonight, home, night)
            await asyncio.to_thread(_save, engine, night, state=RUNNING, ran=ran, made=made, requests=spent)
        final, why, needs = _finish(ran, made)
        spent = await asyncio.to_thread(B.requests_tonight, home, night)
        await asyncio.to_thread(_save, engine, night, state=final, ran=ran, made=made, requests=spent,
                                skipped_why=why, needs_you=needs, ended=True)
        log.info("night review %s: %s, %d suggestion(s), %d request(s)", night, final, len(made), spent)
        # 18-a: the results of the changes the owner accepted are measured at
        # the end of each pass (no model; never raises; the daily pass then
        # leaves the day alone).
        await _refresh_results(engine, clock())
        await announce_once(platform, night)
        return {"state": final, "night": night, "made": len(made), "requests": spent}
    finally:
        _IN_PASS = False


async def review_loop(d: Any, on_tick: Callable[[bool, Any], None], *, on_off: "Callable[[], None] | None" = None,
                      first: "float | None" = None, interval: "float | None" = None) -> None:
    """The lifespan loop: a look :data:`FIRST_PASS_S` after start, then every
    :data:`PASS_EVERY_S`. Each look reports ITS OWN outcome (``on_tick``);
    off or not answered yet pops the health row (``on_off``)."""
    await asyncio.sleep(FIRST_PASS_S if first is None else first)
    while True:
        try:
            if await review_pass(d) is None:
                if on_off is not None:
                    on_off()
            else:
                on_tick(True, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed look is a health row, not a crash
            on_tick(False, exc)
        await asyncio.sleep(PASS_EVERY_S if interval is None else interval)


# --------------------------------------------------------------------------- #
# what Improvements shows (BLOCKING reads)
# --------------------------------------------------------------------------- #
def _pending_models() -> dict[str, tuple[Any, Any]]:
    """Improvements kind -> (table, the "still waiting" status)."""
    from ..coach.models import PENDING, CoachProposalRecord
    from ..motivation.models import ProposalRecord
    from ..scheduling.offers import WAITING as OFFER_WAITING, ScheduleOfferRecord
    from ..skills.learning_models import SkillProposalRecord

    return {
        "schedule_offer": (ScheduleOfferRecord, OFFER_WAITING),
        "coach": (CoachProposalRecord, PENDING),
        "skill": (SkillProposalRecord, "pending"),
        "wants": (ProposalRecord, "pending"),
    }


def _waiting_refs(engine: Any, kind: str, refs: list[str]) -> set[str]:
    """Which of ``refs`` (suggestions of the Improvements ``kind``) still
    wait for the owner's answer. BLOCKING."""
    tables = _pending_models()
    refs = [str(r) for r in refs if r]
    if kind not in tables or not refs:
        return set()
    model, pending = tables[kind]
    with session_scope(engine) as db:
        return {str(i) for i in db.exec(
            select(model.id).where(model.id.in_(refs), model.status == pending)  # type: ignore[attr-defined]
        )}


def _still_waiting(engine: Any, made: list[dict[str, Any]]) -> int:
    by_kind: dict[str, list[str]] = {}
    for m in made:
        by_kind.setdefault(str(m.get("kind") or ""), []).append(str(m.get("ref") or ""))
    return sum(len(_waiting_refs(engine, kind, refs)) for kind, refs in by_kind.items())


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def last_night(engine: Any, config: Any, at: datetime) -> "NightReviewRecord | None":
    """The ONE night the page speaks of at ``at``: last night. ``night_of(at)``
    is the night the window last opened. With the window closed that IS last
    night (its review finished, or was cut off when the window closed).
    While the window is open it is tonight: until tonight's review has
    finished, last night is the one before it. Never two nights back.
    BLOCKING."""
    from ..harness import night as N

    current = night_of(config, at)
    with session_scope(engine) as db:
        row = db.get(NightReviewRecord, current)
        if N.window_open(config, at) and (row is None or row.state not in (DONE, SKIPPED)):
            previous = (datetime.fromisoformat(current) - timedelta(days=1)).date().isoformat()
            row = db.get(NightReviewRecord, previous)
        if row is not None:
            db.expunge(row)
    return row


def night_line(engine: Any, config: Any, at: datetime) -> "dict[str, Any] | None":
    """Last night's line on Improvements (:func:`last_night`), or None: "From
    last night's review: 3" while 3 of its suggestions still wait, or
    "Skipped: …" when it made nothing for a reason only the owner can
    change."""
    row = last_night(engine, config, at)
    if row is None:
        return None
    made = _loads(row.made_json)
    waiting = _still_waiting(engine, made) if made else 0
    view = {
        "night": row.night, "state": row.state, "made": len(made), "waiting": waiting,
        "skipped_why": row.skipped_why, "needs_you": bool(row.needs_you), "line": "",
    }
    if waiting:
        view["line"] = f"From last night's review: {waiting}"
        return view
    if row.state == SKIPPED and row.needs_you and row.skipped_why:
        view["line"] = f"Skipped: {_capital(row.skipped_why)}."
        return view
    return None


def offer_of(config: Any) -> "dict[str, Any] | None":
    """The one-time offer (D-17.1), while the owner has not answered."""
    if getattr(config, "night_review", None) is not None:
        return None
    cap = B.cap_of(config)
    return {"requests": cap, "words": f"up to {cap} request{'' if cap == 1 else 's'} a night on your plan"}


def page_view(d: Any, now: "datetime | None" = None) -> dict[str, Any]:
    """``GET /improvements/night``: ``{offer: {requests, words} | null,
    night: {night, state, made, waiting, skipped_why, needs_you, line} |
    null}``. BLOCKING; RAISES when the record cannot be read."""
    config = d.platform.config
    at = now or now_local()
    return {"offer": offer_of(config), "night": night_line(d.platform.engine, config, at)}


def reasons(engine: Any) -> dict[tuple[str, str], dict[str, str]]:
    """``{(kind, ref): {night, why}}`` over the newest nights' suggestions:
    why the review made each one (the inbox puts it on the item). BLOCKING;
    RAISES when the record cannot be read."""
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(NightReviewRecord.night, NightReviewRecord.made_json)
            .order_by(NightReviewRecord.night.desc()).limit(REASON_NIGHTS)  # type: ignore[attr-defined]
        ))
    out: dict[tuple[str, str], dict[str, str]] = {}
    for night, made_json in rows:
        for m in _loads(made_json):
            key = (str(m.get("kind") or ""), str(m.get("ref") or ""))
            if key[0] and key[1] and key not in out:
                out[key] = {"night": str(night), "why": str(m.get("why") or "")}
    return out


__all__ = [
    "CALL_TIMEOUT_S", "CLOCK", "CUT_OFF", "DONE", "FIRST_PASS_S", "IDLE_CHAT_S", "MADE", "NAMED", "NOTHING",
    "NOTICE_BODY", "PASS_EVERY_S", "PROPOSERS", "REFUSED", "RUNNING", "SKIPPED", "SKIP_BUSY", "SKIP_DONE",
    "SKIP_NIGHT_TASK", "SKIP_RECENT_CHAT", "SKIP_RUNNING", "SKIP_STOPPED", "SKIP_WINDOW", "WAITING",
    "WHY_BUILT_IN", "WHY_CUT_OFF", "WHY_KEPT", "announce_once", "busy_reason", "coach_why", "enabled", "match", "night_line", "night_of",
    "notice_payload", "notice_title", "now_local", "offer_of", "page_view", "period_of", "reasons", "reset",
    "review_loop", "review_pass", "reviewed_within",
]
