"""Notifier — routes messages to one or more communication channels.

Owns a set of named channels and a routing policy. Also adapts the platform
:class:`EventBus` to outbound alerts: :meth:`on_event` formats and sends a
message whenever a *subscribed* event type fires (e.g. ``review.requested``,
``workflow.completed``, ``provider.failed``) and ignores everything else.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from ..core.events import EventType
from .base import Channel

#: event types that, by default, raise an outbound alert.
#:
#: ``workflow.waiting`` is deliberately NOT here: the workflow engine already
#: delivers the parked-run question to every destination itself at park time
#: (``WorkflowEngine._deliver``, v1.121.0), and the pending-prompt handler
#: (``comm/prompts.py``, v1.137.0) additionally sends chat-enabled identities
#: the answerable copy — adding it to the default alert set would triple-send
#: the same question. :func:`format_event` still knows the type so a user who
#: EXPLICITLY subscribes it (``comm.event_types`` in config) gets a
#: phone-friendly line instead of the generic key=value dump.
#:
#: ``approval.requested`` IS here (v1.200.0), and the asymmetry with
#: ``workflow.waiting`` is deliberate: nothing else delivers an approval pause
#: — the runtime only publishes the event — so without a default subscription
#: the phone stayed silent for the whole 300s answer window, at exactly the
#: moment the two-way channel exists for (CONNECT-AUDIT-2026-08-22 §3). The
#: pending-prompt handler (``comm/prompts.py::handle_approval_requested``)
#: registers the answerable row but sends NO copy of its own, so this alert is
#: the ONE phone message. ``approval.resolved`` is deliberately NOT here:
#: answering is not news — the user (or the timeout) just acted, and echoing
#: every decision back would turn the alert channel into a log.
#: Goal NEWS (v1.209.0): a standing goal SATISFYING itself, TRIPPING its
#: breaker, or REFUSING to run is exactly the news an unattended-autonomy
#: channel exists for — each one is a decision the user may want to act on
#: (celebrate/stop, investigate, raise the budget). ``goal.iteration_started``
#: and ``goal.iteration_completed`` are deliberately NOT here — they are
#: routine heartbeats (a daily goal would buzz the phone twice a morning
#: forever), the same reasoning that keeps ``approval.resolved`` out below:
#: routine motion is a log, not news. The digest (``goals/digest.py``) is
#: where the routine record lives. Plain strings mirroring goals/engine.py's
#: constants (importing the engine would drag its sqlmodel chain into this
#: light module); tests/test_goal_digest_v1209.py pins them against the
#: engine's constants so they cannot drift.
GOAL_SATISFIED_EVENT = "goal.satisfied"
GOAL_TRIPPED_EVENT = "goal.tripped"
GOAL_ITERATION_REFUSED_EVENT = "goal.iteration_refused"

DEFAULT_ALERT_EVENTS: frozenset[str] = frozenset(
    {
        EventType.REVIEW_REQUESTED,
        EventType.WORKFLOW_COMPLETED,
        EventType.PROVIDER_FAILED,
        EventType.SESSION_COMPLETED,
        EventType.AUTONOMY_EXECUTED,
        EventType.PROVIDER_FAILOVER,
        EventType.SKILL_PROPOSAL_CREATED,
        EventType.APPROVAL_REQUESTED,
        GOAL_SATISFIED_EVENT,
        GOAL_TRIPPED_EVENT,
        GOAL_ITERATION_REFUSED_EVENT,
    }
)


def _goal_label(payload: dict) -> str:
    """The goal's NAME for a phone line, best recorded truth first.

    The engine's satisfied/tripped/refused payloads carry ``name`` since
    v1.209.0 (all five publish sites), so the name is the normal case. The
    fallbacks stay for the records that predate it and for defensive parsing
    of a hand-fed event: a v1.208.0 EventRecord replayed through a formatter
    has no ``name``, and an id names the exact goal, uglily — better than
    inventing one."""
    return (
        str(payload.get("name") or "").strip()
        or str(payload.get("goal_id") or "").strip()
        or "a goal"
    )


def _event_field(event: Any, attr: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(attr, default)
    return getattr(event, attr, default)


# --------------------------------------------------------------------------- #
# PLAIN WORDS FOR EVERY LINE THE NOTIFIER SENDS (v1.354.0 integrate)
# --------------------------------------------------------------------------- #
#
# The phone, Telegram, email, Slack, Discord and this PC's desktop toast all
# receive ``format_event``'s line. Every event type without a branch below
# used to fall through to ``"Iron Jarvis: session.completed — status=failed,
# summary=…, origin=schedule:daily (session session_7b32…)"`` — an event type
# name, key=value pairs and an internal id, on the user's lock screen. Each
# default alert now says what happened in the words the dashboard's bell
# uses for the same event (components/NotificationBell ``toActivity``), and
# a type nothing here knows says so in a sentence, never by its name.

#: Plain words for an event type — the twin of the dashboard's
#: ``lib/eventWords.EVENT_WORDS`` (the Time-travel feed and the run page),
#: pinned equal by ``tests/test_integrate_wave4_v13540.py``.
EVENT_WORDS: dict[str, str] = {
    "provider.routed": "Picked a model for this turn",
    "provider.failover": "Switched provider after a failure",
    "provider.downgraded": "No real model answered",
    "autonomy.proposed": "Proposed an action on its own",
    "autonomy.executed": "Ran an action on its own",
    "llm.completed": "Model call finished",
    "session.created": "Session started",
    "session.completed": "Session finished",
    "agent.started": "Agent started",
    "agent.state_changed": "Agent changed state",
    "agent.completed": "Agent finished",
    "action.reverted": "Action undone",
    "tool.executed": "Ran a tool",
    "tool.denied": "Refused a tool",
    "delegation.started": "Handed work to a teammate",
    "delegation.completed": "Teammate finished",
    "comm.desktop": "Sent a desktop notification",
}

#: The line for a subscribed type nothing here has words for.
UNKNOWN_EVENT_LINE = "Iron Jarvis has an update for you. Open the app to see it."

#: How an unattended run is named — the bell's ``UNATTENDED_SUBJECT``
#: (``Session.origin`` is ``<door>:<name>``; a goal's suffix is an internal
#: id and is never shown).
_UNATTENDED_SUBJECT: dict[str, Any] = {
    "schedule": lambda n: f"Your schedule {n}" if n else "A schedule",
    "goal": lambda n: "Your goal's run",
    "workflow": lambda n: f"A step of your workflow {n}" if n else "A workflow step",
    "reflex": lambda n: f"Your reflex {n}" if n else "A reflex rule",
    "comm": lambda n: f"Your request from {n}" if n else "A request from your phone",
    "autonomy": lambda n: "An autonomy run",
    # v1.357.0 (G61 + G76): a task another computer running Iron Jarvis sent
    # (agents/task_door.REMOTE_TASK_ORIGIN, "remote:<caller>") — the bell's
    # lib/remoteTask.remoteTaskSubject words, and its DEFAULT_CALLER.
    "remote": lambda n: f"A task from {n}" if n else "A task from another computer",
}

#: A mission's root run (agents/team.py) — the bell's "Your objective …".
_MISSION_ORIGIN = "job:mission"

#: The longest summary a phone line carries.
_SUMMARY_MAX = 300


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _clip(text: str, limit: int = _SUMMARY_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _plain_summary(summary: Any) -> str:
    """A run's model-written summary as plain words on one line (v1.358.0,
    lane 8A): "# Final Report ## What happened …" reaches no phone line —
    ``run_outcome.plain_text``, the rule the Schedules row's detail is
    written with. Not an exact twin of the bell's ``lib/plainText.
    summaryLine``: Python drops heading words when other text exists, the
    bell keeps them — both are plain words, never markdown. Never raises."""
    if not isinstance(summary, str):
        return ""
    try:
        from ..scheduling.run_outcome import plain_text

        return _text(plain_text(summary))
    except Exception:  # noqa: BLE001 — wording only, never the alert
        return _text(summary)


def _with_summary(title: str, summary: Any) -> str:
    said = _clip(_plain_summary(summary))
    return f"{title} — {said}" if said else title


def _run_words(payload: dict) -> str:
    """How a run ended, in the bell's words (``runVerdict`` + its rows): only
    a positive ``completed`` outcome is "finished". v1.358.0 (lane 8A): a run
    that ENDED FAILED "could not finish" even when an ask expired on the way
    — the schedule row says Failed and the run's own badge says Failed;
    "needs you" is a run that finished with the user as the next step."""
    status = _text(payload.get("status"))
    outcome = _text(payload.get("outcome"))
    ran = status == "completed" and payload.get("ok") is not False
    if status == "cancelled":
        return "was stopped"
    if not ran:
        return "could not finish"
    if outcome == "needs_you":
        return "needs you"
    if outcome == "completed":
        return "finished"
    if outcome == "completed_with_failures":
        return "finished, but part of it failed"
    return "finished. Check what it did"


def _session_line(payload: dict) -> str:
    origin = _text(payload.get("origin"))
    summary = payload.get("summary")
    if origin == _MISSION_ORIGIN:
        status = _text(payload.get("status"))
        outcome = _text(payload.get("outcome"))
        ran = status == "completed" and payload.get("ok") is not False
        if status == "cancelled":
            title = "Your objective was stopped"
        elif not ran:
            title = "Your objective could not be finished"
        elif outcome == "completed":
            title = "Your objective is done"
        elif outcome == "needs_you":
            title = "Your objective finished, but something needs you"
        elif outcome == "completed_with_failures":
            title = "Your objective finished, but part of it failed"
        elif not outcome:
            title = "Your objective finished"
        else:
            title = "Your objective finished. Check what it did"
        return _with_summary(title, summary)
    kind, _, name = origin.partition(":")
    subject_for = _UNATTENDED_SUBJECT.get(kind.strip())
    subject = subject_for(name.strip()) if subject_for else "A run"
    return _with_summary(f"{subject} {_run_words(payload)}", summary)


def _workflow_line(payload: dict) -> str:
    name = _text(payload.get("workflow"))
    subject = f"Your workflow {name}" if name else "A workflow"
    status = _text(payload.get("status"))
    if status == "failed":
        return f"{subject} could not finish — the Workflows page shows which step stopped."
    if status == "completed":
        return f"{subject} finished"
    if status == "cancelled":
        return f"{subject} was stopped"
    return f"{subject} ended"


def build_took_words(seconds: Any) -> str:
    """How long a Build turn took, in the bell's words ("took 3 min 12 s");
    "" for a value that is not a number of seconds. The twin of the
    dashboard's ``lib/buildLinks.buildTookWords`` and desktop/main.js's
    ``runOutcomeBuildTook`` (one fixture, one-notice-v13600)."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
        return ""
    whole = int(round(seconds))
    if whole < 60:
        return f"took {whole} s"
    minutes, rest = divmod(whole, 60)
    return f"took {minutes} min {rest} s" if rest else f"took {minutes} min"


#: v1.392.0 review: one session of Build's "Coding sessions" view, mid-sentence
#: — the daemon's twin of the dashboard's ``lib/buildWords.CODING_SESSION``
#: (v1.389.0, the user's word). Every NEW sentence a person reads uses it;
#: "Build session" stays only where an older fixture holds it equal.
CODING_SESSION = "coding session"


def build_ask_words(payload: dict) -> str:
    """A coding session's card on the phone (v1.392.0, decision 3): "Your
    coding session “Fix the nav bar” is waiting for your OK to run a command"
    — the session's title, and the call in the card's own words
    (``agents/mission.card_words``). Names only; never the call's
    arguments. Never raises."""
    if not isinstance(payload, dict):
        payload = {}
    title = _text(payload.get("title"))
    title = title if len(title) <= 80 else title[:79] + "…"
    subject = f"Your {CODING_SESSION} “{title}”" if title else f"Your {CODING_SESSION}"
    tool = _text(payload.get("tool"))
    try:
        from ..agents.mission import card_words

        what = card_words(tool) if tool else "use a tool"
    except Exception:  # noqa: BLE001 — wording only
        what = f"use {tool}" if tool else "use a tool"
    count = payload.get("count")
    many = (
        f" ({count} calls)"
        if isinstance(count, int) and not isinstance(count, bool) and count > 1 else ""
    )
    return f"{subject} is waiting for your OK to {what}{many}"


def build_finished_title(payload: dict) -> str:
    """The bell's headline for ``build.turn_finished`` (v1.374.0)."""
    return (
        "Your Build session could not finish"
        if _text(payload.get("outcome")) == "failed"
        else "Your Build session finished"
    )


def _build_finished_line(payload: dict) -> str:
    """A long Build turn ended (v1.374.0) — said on a destination that
    subscribed the type (never "This PC": the bell / the tray watcher say it
    there). The session's title and how long it took, never an id."""
    title = str(payload.get("title") or "").strip() if isinstance(payload.get("title"), str) else ""
    title = title if len(title) <= 120 else title[:119] + "…"
    bits = [b for b in (title, build_took_words(payload.get("seconds"))) if b]
    return build_finished_title(payload) + (f" — {' · '.join(bits)}" if bits else "")


#: v1.392.0: the bell's headline for ``build.turn_stalled`` (the twin of the
#: dashboard's ``lib/buildLinks.BUILD_STALLED_HEADLINE`` and desktop/main.js —
#: one fixture, one-notice-v13600).
BUILD_STALLED_HEADLINE = f"Your {CODING_SESSION} has stopped making progress"


def build_stalled_body(payload: dict) -> str:
    """The bell's body for ``build.turn_stalled``: the session's title and
    the live line's own sentence ("No progress for 10 minutes — waiting on
    …"), joined like a finished turn's."""
    if not isinstance(payload, dict):
        payload = {}
    title = _text(payload.get("title"))
    title = title if len(title) <= 120 else title[:119] + "…"
    words = _text(payload.get("words"))
    return " · ".join(b for b in (title, words) if b)


def _build_stalled_line(payload: dict) -> str:
    """A Build reply made no progress for ten minutes (v1.392.0) — said on a
    destination that subscribed the type (never "This PC": the bell / the
    tray watcher say it there)."""
    body = build_stalled_body(payload)
    return BUILD_STALLED_HEADLINE + (f" — {body}" if body else "")


def _provider_name(provider: str) -> str:
    try:
        from ..providers.failure_words import heading, plain_name

        return heading(plain_name(provider)) or "The AI service"
    except Exception:  # noqa: BLE001 — wording only
        return provider or "The AI service"


#: ``"ProviderError: "`` — the exception class the router puts first.
_CLASS_PREFIX_RX = re.compile(r"^[A-Za-z_][\w.]*: ")


def _provider_failed_line(payload: dict) -> str:
    """The SAME sentence the chat's failure line shows for this failure
    (``failure_words`` title + fix), read from the event's error text — the
    router publishes ``"<Class>: <message>"``, so the class is set aside and
    the message classified. Never the server's raw text."""
    provider = _text(payload.get("provider"))
    error = str(payload.get("error") or "")
    head = _CLASS_PREFIX_RX.match(error)
    message = error[head.end():] if head else error
    try:
        from ..providers.failure_words import GENERIC_FIX, classify

        words = classify(RuntimeError(message), provider=provider)
        if words is None:
            words = (f"{_provider_name(provider)} couldn't answer", GENERIC_FIX)
        return f"{words[0]}. {words[1]}"
    except Exception:  # noqa: BLE001 — wording only
        return f"{_provider_name(provider)} couldn't answer."


def _failover_line(payload: dict) -> str:
    was = _provider_name(_text(payload.get("from")))
    now = _provider_name(_text(payload.get("to")))
    why = _text(payload.get("reason"))
    because = f" ({why})" if why else ""
    return (
        f"{EVENT_WORDS['provider.failover']}: {was} couldn't answer{because}, "
        f"so {now} answered instead."
    )


def _review_line(payload: dict) -> str:
    files = payload.get("changed_files")
    n = len(files) if isinstance(files, (list, tuple)) else 0
    risk = _text(payload.get("risk")).lower()
    bits = []
    if n:
        bits.append(f"{n} file{'' if n == 1 else 's'} changed")
    if risk:
        bits.append(f"{risk} risk")
    detail = f" ({', '.join(bits)})" if bits else ""
    return f"A change is waiting for your review{detail}. Open Iron Jarvis to look it over."


def format_event(event: Any) -> str:
    """Build a concise human-readable alert line from an event."""
    etype = _event_field(event, "type", "event")
    payload = _event_field(event, "payload", {}) or {}
    if etype == "workflow.waiting":
        # Phone-friendly park alert (v1.137.0): carry the question and point
        # at the answer paths a pocket actually has.
        wf = str(payload.get("workflow") or payload.get("run_id") or "a workflow")
        q = str(payload.get("question") or "").strip()
        detail = f": {q}" if q else ""
        return (
            f"Workflow '{wf}' needs you{detail} — reply with a number or "
            "/answer <text> from a chat-enabled destination."
        )
    if etype == EventType.APPROVAL_REQUESTED:
        # Phone-friendly pause alert (v1.200.0): name the tool and BOTH answer
        # paths a pocket actually has — the dashboard bell, or replying right
        # here (comm/prompts.py registers the answerable prompt that makes
        # "reply here" true on chat-enabled destinations).
        tool = str(payload.get("tool") or "").strip() or "a tool"
        if isinstance(payload, dict) and payload.get("surface") == "build":
            # v1.392.0 (decision 3): a coding session's card nobody answered
            # in the app for five minutes — the session by its title, what
            # the call would do in the card's own words.
            return f"⏸ {build_ask_words(payload)} — answer in Iron Jarvis, or reply here: approve / deny."
        return (
            f"⏸ An agent is asking to use {tool} — approve from the "
            "dashboard bell, or reply here: approve / deny."
        )
    if etype == GOAL_SATISFIED_EVENT:
        # House voice (v1.209.0): the verifier's checks all held — say so
        # plainly; the digest carries the evidence trail.
        return f"✅ Goal satisfied: {_goal_label(payload)}"
    if etype == GOAL_TRIPPED_EVENT:
        reason = str(payload.get("reason") or "").strip()
        return f"🛑 Goal breaker tripped: {_goal_label(payload)}" + (
            f" — {reason}" if reason else ""
        )
    if etype == GOAL_ITERATION_REFUSED_EVENT:
        # A refusal names its reason (budget exhausted / tripped / paused /
        # already running) — the one line that tells the user WHY the goal
        # went quiet instead of letting silence read as progress.
        reason = str(payload.get("reason") or "").strip()
        return f"⏸ Goal run refused: {_goal_label(payload)}" + (
            f" — {reason}" if reason else ""
        )
    if etype == EventType.SKILL_PROPOSAL_CREATED:
        name = str(payload.get("skill_name") or "").strip() or "a new skill"
        if payload.get("auto"):
            # auto=True: the explicit auto-approve setting already wrote the
            # skill to disk — "review it" would point at an empty review queue.
            return f"New skill added automatically: {name} — see it on the Skills page"
        return f"New skill suggested: {name} — review it on the Skills page"
    # v1.354.0 integrate: every other default alert in the bell's words, and
    # never an event type name, a key=value pair or an internal id.
    if not isinstance(payload, dict):
        payload = {}
    if etype == EventType.SESSION_COMPLETED:
        return _session_line(payload)
    if etype == EventType.WORKFLOW_COMPLETED:
        return _workflow_line(payload)
    if etype == EventType.PROVIDER_FAILED:
        return _provider_failed_line(payload)
    if etype == EventType.PROVIDER_FAILOVER:
        return _failover_line(payload)
    if etype == EventType.REVIEW_REQUESTED:
        return _review_line(payload)
    if etype == EventType.AUTONOMY_EXECUTED:
        return "Iron Jarvis ran an action on its own — see it on the Autonomy page."
    if etype == EventType.BUILD_TURN_FINISHED:
        return _build_finished_line(payload)
    if etype == EventType.BUILD_TURN_STALLED:
        return _build_stalled_line(payload)
    words = EVENT_WORDS.get(str(etype or ""))
    if words:
        return _with_summary(f"Iron Jarvis: {words}", payload.get("summary"))
    return UNKNOWN_EVENT_LINE


# --------------------------------------------------------------------------- #
# ONE NOTICE PER OUTCOME (v1.360.0, lane 10A — the re-audit's G03 regression)
# --------------------------------------------------------------------------- #
#
# One failed scheduled run used to raise up to THREE desktop notifications
# while a window was open, in different words: the bell's own ping ("Your
# schedule X could not finish"), the schedule's delivery to "This PC"
# ("Schedule “X”: FAILED — …") and this notifier's generic session.completed
# alert to "This PC" ("Your schedule X could not finish — …"); a failed
# workflow rang a step row AND a workflow row, and "This PC" toasted both.
#
# Who owns the desktop notice for a run's outcome:
#
# * a window is open → the dashboard's bell, which pings exactly the rows
#   `bell_pings` describes (its twin is NotificationBell `toActivity`: a row
#   that is neither null nor quiet; both read
#   dashboard/__tests__/fixtures/one-notice-v13600.json);
# * the app sits in the tray → desktop/main.js's run-outcome watcher, which
#   says the bell's attention rows in the bell's words (the renderer, and so
#   both the bell and the "This PC" bridge, is gone then);
# * the "This PC" destination stays QUIET for exactly those outcomes
#   (`Notifier.on_event` here, `platform._deliver_outcome` for a schedule's
#   own delivery), and for a workflow STEP's completion: its workflow's own
#   notice says how the workflow ended.
#
# And a run a SCHEDULE started is announced by that schedule's own delivery
# (which honours its `notify` / `notify_channels`), so inside a schedule fire
# this notifier sends no generic run alert to ANY destination — one notice
# per outcome on the phone too (it used to get the schedule's line AND this
# alert, and `notify: false` silenced only the first).

#: The run-outcome alerts the rules above apply to.
RUN_OUTCOME_EVENTS: frozenset[str] = frozenset(
    {EventType.SESSION_COMPLETED, EventType.WORKFLOW_COMPLETED}
)


def _origin_kind(payload: dict) -> str:
    origin = _text(payload.get("origin"))
    return origin.partition(":")[0].strip()


def is_workflow_step(payload: dict) -> bool:
    """A ``session.completed`` of one STEP of a workflow (origin
    ``workflow:<name>``): its workflow's own completion says how it ended."""
    return _origin_kind(payload) == "workflow"


def bell_pings(event: Any) -> bool:
    """Does the dashboard bell raise a desktop ping for this event while a
    window is open? The Python twin of ``NotificationBell.toActivity`` for the
    two run-outcome types (a row that is neither null nor ``quiet``):

    * ``session.completed`` of an UNATTENDED door (schedule, goal, workflow
      step, reflex, phone, autonomy, another computer) that did not end
      cleanly done and was not stopped by the user — the bell's "could not
      finish" / "needs you" / "finished, but part of it failed" rows;
    * ``session.completed`` of a mission's root run, unless stopped;
    * ``workflow.completed`` that FAILED;
    * ``build.turn_finished`` (v1.374.0): a Build turn that ran longer than a
      minute and ended — the bell's "Your Build session finished" row (it
      rings unless the user is LOOKING at that session in a window, a rule
      only the window can apply).

    A run with no session id has no bell row. Never raises."""
    try:
        etype = _event_field(event, "type")
        payload = _event_field(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            return False
        if etype == EventType.WORKFLOW_COMPLETED:
            return _text(payload.get("status")) == "failed"
        if etype in (EventType.BUILD_TURN_FINISHED, EventType.BUILD_TURN_STALLED):
            # v1.392.0: a Build reply that made no progress for ten minutes
            # rings exactly like a long one that ended (one per episode).
            return bool(_text(payload.get("thread_id")) or _text(_event_field(event, "session_id")))
        if etype != EventType.SESSION_COMPLETED:
            return False
        if not _text(_event_field(event, "session_id")):
            return False
        status = _text(payload.get("status"))
        if status == "cancelled":
            return False
        origin = _text(payload.get("origin"))
        if origin == _MISSION_ORIGIN:
            return True
        if _origin_kind(payload) not in _UNATTENDED_SUBJECT:
            return False
        ran = status == "completed" and payload.get("ok") is not False
        done = ran and _text(payload.get("outcome")) == "completed"
        return not done
    except Exception:  # noqa: BLE001 — a wording rule must never drop an alert
        return False


def bell_attention(event: Any) -> bool:
    """The bell's ATTENTION rows among the ones it pings (counted on its
    badge): every unattended shortfall and a failed workflow — never a
    mission's row. Exactly what desktop/main.js says while the app sits in
    the tray."""
    if not bell_pings(event):
        return False
    payload = _event_field(event, "payload", {}) or {}
    return not (
        _event_field(event, "type") == EventType.SESSION_COMPLETED
        and _text(payload.get("origin")) == _MISSION_ORIGIN
    )


class _FireScope:
    """One schedule fire's announcing window. ``open`` is cleared when the
    fire returns, so work it started that outlives it (a background run
    copies the context) is announced as usual."""

    __slots__ = ("name", "open")

    def __init__(self, name: str) -> None:
        self.name = name
        self.open = True


_SCHEDULE_FIRE: ContextVar["_FireScope | None"] = ContextVar(
    "iron_jarvis_schedule_fire", default=None
)


@contextmanager
def schedule_fire(name: str) -> Iterator[None]:
    """Mark everything this fire runs as announced by the schedule's own
    delivery (``platform._run_scheduled``). The event bus runs its handlers
    through ``asyncio.to_thread``, which copies the context, so the notifier
    sees the mark for every run the fire awaits."""
    scope = _FireScope(str(name or ""))
    token = _SCHEDULE_FIRE.set(scope)
    try:
        yield
    finally:
        scope.open = False
        _SCHEDULE_FIRE.reset(token)


def in_schedule_fire() -> bool:
    """Is the current code running inside a schedule fire that will deliver
    its own notice?"""
    scope = _SCHEDULE_FIRE.get()
    return bool(scope is not None and scope.open)


def is_desktop_channel(channel: Any) -> bool:
    """The "This PC" destination (a ``DesktopChannel``, whatever name it was
    registered under)."""
    try:
        from .channels import DesktopChannel

        return isinstance(channel, DesktopChannel)
    except Exception:  # noqa: BLE001
        return False


class Notifier:
    def __init__(
        self,
        *,
        default_channel: str | None = None,
        event_types: set[str] | None = None,
        formatter: Callable[[Any], str] | None = None,
    ) -> None:
        self._channels: dict[str, Channel] = {}
        self.default_channel = default_channel
        self.event_types: set[str] = (
            set(event_types) if event_types is not None else set(DEFAULT_ALERT_EVENTS)
        )
        self._formatter = formatter or format_event

    # -- channel management ---------------------------------------------
    def add_channel(self, name: str, channel: Channel) -> None:
        self._channels[name] = channel
        if self.default_channel is None:
            self.default_channel = name

    def remove_channel(self, name: str) -> bool:
        """Drop a channel; returns whether it existed. Re-points the default."""
        existed = self._channels.pop(name, None) is not None
        if self.default_channel == name:
            self.default_channel = next(iter(sorted(self._channels)), None)
        return existed

    def get(self, name: str) -> Channel | None:
        return self._channels.get(name)

    def channels(self) -> list[str]:
        return sorted(self._channels)

    def without_desktop(self, names: list[str]) -> list[str]:
        """``names`` minus the "This PC" destinations (v1.360.0, lane 10A):
        for an outcome the bell (window open) or the desktop app's tray
        watcher (no window) already says on this PC."""
        return [n for n in names if not is_desktop_channel(self._channels.get(n))]

    # -- routing ---------------------------------------------------------
    def _targets(self, channels: list[str] | None) -> list[str]:
        if channels:
            return list(channels)
        if self.default_channel and self.default_channel in self._channels:
            return [self.default_channel]
        return self.channels()

    def notify(
        self, message: str, channels: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Send ``message`` to ``channels`` (or the default/all) and report results."""
        results: dict[str, dict[str, Any]] = {}
        for name in self._targets(channels):
            channel = self._channels.get(name)
            if channel is None:
                results[name] = {"ok": False, "detail": f"unknown channel '{name}'"}
                continue
            try:
                results[name] = channel.send(message)
            except Exception as exc:  # a channel must never break the fan-out
                results[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return results

    # -- event bus adapter ----------------------------------------------
    def on_event(self, event: Any) -> dict[str, dict[str, Any]] | None:
        """EventBus handler: alert on subscribed event types, ignore the rest.

        Returns the per-channel results when it fired, ``None`` when ignored.
        Safe to register via ``event_bus.add_handler(notifier.on_event)``.
        """
        etype = _event_field(event, "type")
        if etype not in self.event_types:
            return None
        if etype == EventType.PROVIDER_FAILED:
            # v1.378.0 review: a provider's SAFETY REFUSAL is not an outage —
            # the turn or run that was declined already says so where it was
            # asked (ONE notice, v1.360.0), and this alert's words ("couldn't
            # answer. Press Retry … pick another model") were the Retry and
            # the "send it elsewhere" the refusal rule forbids. The ledger
            # keeps it (`kind: "refusal"`).
            payload = _event_field(event, "payload", {}) or {}
            if isinstance(payload, dict) and payload.get("kind") == "refusal":
                return None
        # v1.118.0: fan out to EVERY channel whose per-registration config
        # allows this event type — not just the default. A channel config may
        # carry ``events: [...]`` to narrow what it receives (the Notifications
        # page's per-destination checkboxes); absent/empty means everything,
        # which is exactly the old behaviour for existing channels. Before this,
        # notify(None-targets) meant auto-alerts only ever reached the DEFAULT
        # channel — a second connected destination silently got nothing.
        targets = [
            name
            for name, ch in sorted(self._channels.items())
            if not (getattr(ch, "config", None) or {}).get("events")
            or etype in (ch.config.get("events") or [])
        ]
        if etype in RUN_OUTCOME_EVENTS:
            # v1.360.0 (lane 10A): ONE notice per outcome. A run a schedule
            # started is said by that schedule's own delivery, on every
            # destination; on this PC the bell / the tray watcher say what
            # they ring, and a workflow's own notice says its steps.
            if in_schedule_fire():
                return {}
            payload = _event_field(event, "payload", {}) or {}
            if bell_pings(event) or (
                etype == EventType.SESSION_COMPLETED
                and isinstance(payload, dict)
                and is_workflow_step(payload)
            ):
                targets = self.without_desktop(targets)
        elif etype == EventType.APPROVAL_REQUESTED:
            # v1.360.0 integrate (lane 10A note g): ONE notice per ask on this
            # PC too. Every agent ask is listed by GET /chat/approvals/pending,
            # which the bell counts and pings ("N pending · an agent asking
            # permission") while a window is open, and which the desktop app's
            # ask watcher toasts while it sits in the tray. "This PC" (alive
            # only while a window is) added a second toast beside the first.
            # The phone and every other destination keep their one line.
            targets = self.without_desktop(targets)
        elif etype in (EventType.BUILD_TURN_FINISHED, EventType.BUILD_TURN_STALLED):
            # v1.374.0: a long Build turn that ended is said on this PC by the
            # bell (a window is open) or the desktop app's tray watcher (none
            # is) — never a second toast from "This PC". A destination that
            # subscribed the type (the phone) still hears it.
            targets = self.without_desktop(targets)
        if not targets:
            return {}
        return self.notify(self._formatter(event), targets)
