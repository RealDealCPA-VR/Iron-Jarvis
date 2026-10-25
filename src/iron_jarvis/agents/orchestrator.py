"""Orchestrator (§12 host; §14 sessions; §15 workspaces).

Creates sessions with isolated, disposable workspaces and drives the agent
runtime. The supervisor → subagent hierarchy (§12) is LIVE: ``run_session``
dispatches SUPERVISOR sessions to ``run_supervised``, whose ``delegate`` tool
spawns child sessions through ``AgentRuntime.run(parent_id=...)`` (depth-capped,
parallel siblings via the runtime's gathered tool calls). Since v1.166.0 the
orchestrator is also the background-run concurrency governor: ``spawn_managed``
runs or parks (QUEUED) session work under ``config.max_concurrent_sessions``.
Since v1.333.0 ``run_session`` itself is the door every root run passes: it
marks an unattended run as background work (``core.procs.background_lane``),
counts it, offers the "busy" notice, and holds the cap for the doors that
never went through ``spawn_managed`` (schedules, goals, workflow steps, phone).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from sqlmodel import select

from ..core import procs
from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.trust import TRUST_LOW as _TRUST_LOW, normalize_trust as _normalize_trust
from ..core.models import (
    SESSION_MAX_STEPS_MAX,
    SESSION_MAX_STEPS_MIN,
    AgentRun,
    AgentState,
    AgentType,
    PendingReviewRecord,
    Project,
    Session,
    SessionStatus,
    ToolInvocation,
)
from ..git.integration import GitSession
from ..git.review import (
    ReviewRequest,
    approve as _approve_review,
    build_review,
    reject as _reject_review,
)
from .chat_delivery import STOPPED_BEFORE_START_SENTENCE, STOPPED_SENTENCE
from .runtime import (
    ATTENDED_ORIGINS,
    AgentRuntime,
    inherited_approval_mode,
    is_direct_workspace,
    normalize_prior_tool_results,
)
from .team import MISSION_ORIGIN
from . import chat_delivery as _chat_delivery
from .task_door import REPLY_OPTION as _REMOTE_REPLY_OPTION
from .decompose import is_bulk_task
from .supervisor import run_supervised, with_worklist
from .types import AgentDefinition, get_agent_definition

log = get_logger("orchestrator")

#: A session whose project folder could NOT be used carries the reason in
#: ``summary`` from creation until it finishes (v1.231.0, audit AE1 — the
#: session-row twin of the workflow run's ``notes_json`` note, v1.225.0), so
#: "it ran but nothing landed in my folder" has a reason on the row instead of
#: a mystery. The finalizers keep it under the result via ``_with_folder_note``.
FOLDER_NOTE_PREFIX = "Folder note: "


def folder_note(summary: str | None) -> str:
    """The create-time folder note a summary holds, or "" when it is prose."""
    text = summary or ""
    return text if text.startswith(FOLDER_NOTE_PREFIX) else ""


def _with_folder_note(prior: str | None, text: str) -> str:
    """``text`` with the create-time folder note (if ``prior`` was one) kept
    under it — a finished summary must not erase why the folder was skipped."""
    note = folder_note(prior)
    return f"{text}\n\n{note}" if note else text


def _prose(summary: str | None) -> str:
    """A summary that is real prose (not the create-time folder note), or ""."""
    return "" if folder_note(summary) else (summary or "")


def is_managed_workspace(config, workspace_path: str | Path | None) -> bool:
    """True only when a workspace is PROVABLY a disposable dir the app made,
    i.e. strictly inside ``config.workspaces_dir``.

    The counterpart of ``is_direct_workspace`` — and deliberately NOT its
    negation, because deletion is irreversible and the two default in opposite
    directions. ``is_direct_workspace`` answers "should this rerun land in the
    user's folder?", so an unresolvable path (or the managed dir itself)
    safely answers False there. Here the same False would mean "safe to
    ``rmtree``", which is the wrong way to be wrong: a session whose
    ``workspace_path`` is the managed ROOT would take every other session's
    workspace with it, and an unresolvable path is not evidence of anything.
    So this asserts membership positively and refuses to guess.
    """
    if not workspace_path:
        return False
    try:
        ws = Path(workspace_path).resolve()
        managed = Path(config.workspaces_dir).resolve()
    except (OSError, ValueError):  # unresolvable path -> not provably ours
        return False
    return managed in ws.parents


def inherited_workspace_root(config, parent: Session | None) -> str | None:
    """The folder a DELEGATED/SPAWNED child should work in, or None for the
    disposable scratch dir it has always had (v1.193.0).

    ``delegate``/``spawn_agent`` forwarded provider, model and project to the
    child and never the WORKSPACE, so a Projects in-folder task — the one lane
    that runs directly in the user's real folder — had a parent reading the
    user's files while every child it handed work to got an empty scratch dir
    somewhere under ``workspaces_dir``. The child could not see what it was
    asked to work on, and anything it wrote landed where neither the parent nor
    the user ever looks: a delegated deliverable was effectively lost unless the
    child inlined it into its summary string.

    Only a DIRECT workspace is forwarded (``is_direct_workspace`` — the one
    honest signal that the user chose this folder). A managed scratch dir or a
    git worktree keeps today's isolation: a worktree is a review-gated branch
    whose whole point is that the work is separable, and handing a second agent
    the same worktree would fold two agents' changes into one review.

    THE CONSEQUENCE IS REAL AND IS NOT PAPERED OVER: two agents can now write
    the same folder. There is no file locking here on purpose — the primitive
    that fits is the durable worklist CLAIM (``worklist_next``), which
    ``supervisor.WORKLIST_PATTERN`` already describes as "what stops two
    subagents doing the same file".
    """
    if parent is None:
        return None
    path = getattr(parent, "workspace_path", None)
    if not path or not is_direct_workspace(config, path):
        return None
    return str(path)


def inherited_trust(parent: Session | None) -> tuple[str | None, str]:
    """The TRUST posture a child (delegate / spawn_agent / an agent-queued
    assignment) inherits from its parent: ``(trust, trust_reason)``
    (v1.298.0). A low parent hands down ``("low", <its reason>)``; a full or
    unknown parent hands down ``(None, "")`` so ``create_session`` applies
    its default. Trust only ever flows DOWN: a child never starts higher
    than the run that asked for it, because the ask itself came from a run
    that may be acting on flagged content."""
    from ..core.trust import TRUST_LOW, normalize_trust

    if parent is None or normalize_trust(getattr(parent, "trust", "")) != TRUST_LOW:
        return None, ""
    reason = " ".join(str(getattr(parent, "trust_reason", "") or "").split())
    return TRUST_LOW, reason or "inherited from a low-trust parent run"


def session_local_only(session: Any) -> bool:
    """True when a session row is LOCAL ONLY (v1.332.0): its
    ``options_json`` carries ``local_only: true``. A legacy row, a stub
    without the column or junk in it reads False."""
    raw = getattr(session, "options_json", None)
    if not raw:
        return False
    try:
        import json as _json

        opts = _json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    return isinstance(opts, dict) and opts.get("local_only") is True


def inherited_local_only(parent: Session | None) -> bool:
    """The LOCAL-ONLY posture a child inherits from its parent (v1.332.0),
    the way :func:`inherited_trust` hands trust down: a local-only parent's
    children are local only. Only ever flows DOWN. A child created INSIDE a
    local-only run (delegate, spawn_agent, a mission teammate) also inherits
    it through the context (``create_session`` reads the scope); this
    predicate is for the doors that create the child LATER, outside that
    context (an agent-queued assignment)."""
    return parent is not None and session_local_only(parent)


def inherited_grants(parent: Session | None) -> tuple[list[str], str]:
    """The up-front tool grant and approval posture a DELEGATED/SPAWNED child
    inherits from its parent: ``(allow_tools, approval_mode)`` (v1.288.0).

    ``delegate``/``spawn_agent`` forwarded the parent's folder and project and
    never its GRANTS, so a Team job the user started with shell pre-approved
    handed the actual work to a child with ``allow_tools=[]``. Outside a
    mission the child has no origin, so it cannot pause to ask either — every
    ask-tier call it made died on the headless resolver with "grant it with
    allow_tools", which the user had already done. One predicate, both doors,
    like ``inherited_workspace_root``.

    Exactly what the user granted the parent, never more: a session grant never
    lifts a base ``deny`` or an agent definition's floor (``invoke`` refuses
    those in every posture), and ``create_session`` normalises the posture
    through ``inherited_approval_mode`` so ``yolo`` reaches no row.

    ``always_ask`` forwards the posture but NO grant list. Under that posture
    the stored list does not pre-approve — the parent asks once per run — so
    handing the child the list would let it silently run what its own parent
    would have asked about. A mission teammate (v1.309.0) carries the posture
    and ASKS once per run like its coordinator; every other child has no
    channel and keeps the honest headless refusal (fail closed).

    The ORIGIN is a separate predicate, :func:`inherited_origin`: a mission's
    teammates are stamped ``job:mission-member`` and their asks wait for the
    user (the mission screen, the bell and the desktop ask watcher all read
    an ask by the teammate's own session id). Any other child keeps no origin
    — a chat-started supervisor's child would card under a session id the
    chat page does not render, so it takes the instant headless denial.
    """
    if parent is None:
        return [], ""
    mode = inherited_approval_mode(getattr(parent, "approval_mode", ""))
    if mode == "always_ask":
        return [], mode
    return _stored_allow_tools(parent), mode


def inherited_origin(parent: Session | None, root: Session | None = None) -> str | None:
    """The ORIGIN a delegated/spawned child is created with (v1.309.0).

    ``job:mission-member`` when the child's parent — or the team ROOT the
    ``AgentRun.parent_id`` walk reaches (``team.mission_root``) — is a
    mission's coordinator or one of its teammates; ``None`` for every other
    child, exactly as before. Deriving from the root too means a teammate that
    hands work on gives ITS child the member origin as well.

    WHY a mission's teammate may ask and no other child may: an origin is a
    claim that a person is watching the run. The mission screen shows every
    teammate's ask by its own session id and the bell + desktop ask watcher
    deliver it from any page, so a watched mission's builder could finally
    use the shell the user is right there to allow — before this it died on
    "nothing here could ask" with the user looking at it. A chat-escalated
    supervisor's child has no such surface, so it keeps the instant headless
    denial (pinned by a control).

    THE COST, said plainly: ``job`` is an ATTENDED prefix, so a teammate's
    ask waits with NO clock — and while it waits it holds its parent's
    ``child_slot`` and the coordinator stays blocked on that delegation.
    That is the right trade on a screen the user is watching; Stop on the
    teammate (``cancel_session``) declines its open asks so a parked child
    is never stranded. Never ``job:mission`` itself: ``GET /missions`` lists
    by that exact origin, so a teammate would become an objective of its own.
    """
    from .team import MISSION_MEMBER_ORIGIN, is_mission_origin

    for row in (parent, root):
        if row is not None and is_mission_origin(getattr(row, "origin", None)):
            return MISSION_MEMBER_ORIGIN
    return None


#: Default cap on how many children ONE delegating parent may run AT ONCE
#: (v1.193.0). Config-overridable with ``max_concurrent_children``; <= 0 means
#: unlimited, i.e. exactly the pre-v1.193.0 behavior.
_DEFAULT_MAX_CONCURRENT_CHILDREN = 4


@dataclass
class _FanoutSlot:
    """One parent's child-concurrency semaphore plus a live user count, so the
    registry entry is dropped when the last child of that parent is done
    (a semaphore per run id would otherwise accumulate for the process's life)."""

    sem: asyncio.Semaphore
    users: int = 0


#: (loop id, fan-out key) -> slot. Keyed by LOOP too: a semaphore is only ever
#: awaited on the loop that created it.
_child_slots: dict[tuple[int, str], _FanoutSlot] = {}


def max_concurrent_children(config) -> int:
    """``config.max_concurrent_children``, defaulted and sanitized (0 = off)."""
    raw = getattr(config, "max_concurrent_children", None)
    if raw is None:
        return _DEFAULT_MAX_CONCURRENT_CHILDREN
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_CONCURRENT_CHILDREN
    return max(value, 0)


def child_fanout_key(agent_run_id: str | None, session_id: str | None) -> str:
    """The bucket a delegating caller's children are counted in.

    PER CALLER, never per department — that distinction is what makes the cap
    deadlock-free (see :func:`child_slot`). A caller with no persisted run
    (chat-origin tool calls) falls back to its session id, and only then to a
    shared root bucket.
    """
    if agent_run_id:
        return f"run:{agent_run_id}"
    return f"session:{session_id or 'root'}"


@asynccontextmanager
async def child_slot(config, key: str) -> AsyncIterator[None]:
    """Hold one of ``key``'s bounded child slots for the duration of a child run.

    WHY NOT ``spawn_managed`` (deliberate, v1.193.0). The obvious fix for
    "delegate bypasses the concurrency governor" is to route children through
    the governor — and it deadlocks. The delegating parent is BLOCKED awaiting
    its child while itself occupying a governed slot, so a child parked by the
    governor waits for a slot that only its own parent can free. The codebase
    already knows this hazard: ``spawn_managed`` exempts non-session work for
    exactly this reason ("a parked workflow that itself spawns sessions could
    deadlock the queue"). So delegation gets its own cap, and it is bounded on
    a key nobody can wait on transitively:

    * a waiter on key K is a child of the run K names;
    * the slot holders on key K are siblings of that waiter, and a sibling's
      OWN children wait on a different key (their parent's run id, minted per
      run and never reused);
    * therefore no holder of K can ever be blocked on K, and the wait graph has
      no cycle. Progress is guaranteed by the parent chain, never by the queue.

    ``limit <= 0`` yields immediately (unlimited). With no running loop the
    context manager is a no-op rather than an error — a bare sync caller has no
    concurrency to bound.
    """
    limit = max_concurrent_children(config)
    if limit <= 0:
        yield
        return
    try:
        slot_key = (id(asyncio.get_running_loop()), key)
    except RuntimeError:  # pragma: no cover - tools always run on a loop
        yield
        return
    slot = _child_slots.get(slot_key)
    if slot is None:
        slot = _FanoutSlot(asyncio.Semaphore(limit))
        _child_slots[slot_key] = slot
    slot.users += 1
    try:
        async with slot.sem:
            yield
    finally:
        slot.users -= 1
        if slot.users <= 0:
            _child_slots.pop(slot_key, None)


# --- background runs: the lane, the count, the busy notice (v1.333.0) -------

#: True while the current task is inside a ``run_session``. A run started from
#: inside another run (an agent tool that runs a workflow, a goal iteration a
#: run kicked off) is never PARKED by the cap: its ancestor holds a governed
#: slot and may be awaiting it, so parking it would wait on its own parent —
#: the deadlock ``child_slot`` documents. It still counts as running. The cost,
#: said plainly: work a run fires off and does NOT await is exempt too; the
#: context cannot tell the two apart.
_IN_RUN: ContextVar[bool] = ContextVar("ij_in_run", default=False)

#: Default for ``config.busy_runs_notice_at``: more than this many unattended
#: runs at once (with no cap set) rings the bell once and asks about a queue.
BUSY_NOTICE_DEFAULT = 4
#: A new busy episode may ring again, but never sooner than this after the last.
BUSY_NOTICE_GAP_S = 3600.0
#: "Not now" keeps the daemon quiet about busy runs this long.
BUSY_SNOOZE_S = 24 * 3600.0
#: The cap "Queue extras" offers (never above the notice threshold itself).
BUSY_SUGGESTED_CAP = 3


def is_unattended_origin(origin: str | None) -> bool:
    """True when nobody is watching a run of ``origin``: a tagged origin that
    does not start with ``runtime.ATTENDED_ORIGINS`` (schedules, goals,
    workflows, reflex, the phone, autonomy, assignments, memory review).

    An UNTAGGED run (no origin, or "") is attended. Every door that creates
    one is a person: the Sessions page's New form, Spotlight, the first-run
    wizard, the self-development page, ``ij run`` and a custom agent's Run
    (``POST /sessions`` / ``/agents/{name}/spawn`` with no origin), and a
    rerun of one of those. Every unattended door stamps ``<door>:<name>``
    (the v1.231.0 rule), so a run a person started by hand never lands in
    the background lane, the busy count or the cap's queue for want of a
    tag. (The approval clock still reads an untagged run as unattended:
    that is ``runtime._ask_timeout_for``'s own rule.)"""
    text = str(origin or "").strip()
    if not text:
        return False
    return not text.startswith(ATTENDED_ORIGINS)


#: EMERGENCY STOP (v1.351.0, readiness audit G13). ``config.
#: autonomy_kill_switch`` used to be read by the motivation loop alone, while
#: the setting promised "Immediately blocks every self-initiated action" and
#: the Autonomy page flashed "all autonomy halted" — schedules, goals, reflex
#: rules, the phone and queued assignments kept running. Every unattended
#: door ends in ``Orchestrator.create_session``, so the refusal lives THERE
#: (one rule, ``emergency_stop_covers``), and ``stop_unattended`` cancels what
#: such doors already have running when the switch is engaged. Chat, a
#: mission, a project task and a session a person started are attended and
#: are never touched. v1.357.0 (G76 + G61): in the canon's unit of work
#: ("task", VOCABULARY.md) and naming a task another computer running Iron
#: Jarvis sent (``task_door``'s ``remote:<caller>`` origin), which the sender
#: reads quoted in the door's 409.
EMERGENCY_STOP_REFUSAL = (
    "Emergency stop is on, so work that starts on its own (schedules, goals, "
    "rules, phone tasks, queued tasks, tasks from another computer) cannot "
    "start. Turn it off on the Autonomy page to let it run again."
)
#: The summary a run Emergency stop cut off carries (and the schedule row
#: shows): the stop was the user's switch, not "cancelled by the user" at a
#: Cancel button they never pressed.
EMERGENCY_STOP_STOPPED = "Emergency stop was turned on, so this run was stopped."
#: The summary (and run result) the boot reconcile gives a run a restart cut
#: off (v1.354.0, G30): Iron Jarvis, never "the daemon". The SAME words the
#: run page's restart line, the bell's interrupted-jobs rows and the chat line
#: a chat-started run delivers home say (dashboard
#: components/sessions/runPageWords.RESTART_WORDS, pinned equal by
#: tests/test_integrate_wave4_v13540.py). Rows stored before v1.354.0 keep
#: the old "interrupted by a daemon restart"; the run page reads both.
RESTART_SUMMARY = "Stopped when Iron Jarvis restarted."
#: The summary (and the reply a chat-started run leaves in its chat, and the
#: answer a task from another computer gets back) when a run was stopped with
#: no words of its own (v1.359.0, wave 9): the SHARED stopped sentence — the
#: chat page's own words for a stop with no summary
#: (``chat_delivery.STOPPED_SENTENCE``), beside the canon's "Stopped" badge
#: (lib/statusWords). Before: "Session cancelled by the user." — the engine's
#: "session" and "cancelled". Neutral on purpose: the same summary goes home
#: to ANOTHER computer for a task it sent, where "by you" would name the
#: wrong person. Rows stored before v1.359.0 keep their old words
#: (:data:`LEGACY_STOPPED_SUMMARY`).
STOPPED_SUMMARY = STOPPED_SENTENCE
#: The same, for a run stopped while it still waited in the queue.
STOPPED_BEFORE_START = STOPPED_BEFORE_START_SENTENCE
#: What a stopped run's summary read before v1.359.0 (rows on disk keep it).
LEGACY_STOPPED_SUMMARY = "Session cancelled by the user."


class EmergencyStopOn(RuntimeError):
    """``create_session`` refused an unattended origin because Emergency stop
    is on. A ``RuntimeError`` so every door that already turns a refused
    start into a recorded failure (a reflex row, a workflow step, the memory
    review's 400) says the sentence without a new branch."""

    #: Readiness 12A (G28): its message IS the person's sentence, so a run
    #: summary keeps it as it is (``failure_words.worded_message``) instead of
    #: the generic "Iron Jarvis couldn't finish this task".
    plain_words = True


def emergency_stop_on(config) -> bool:
    """Is Emergency stop engaged? Read live (a settings write flips the
    attribute in place). Anything but a real ``True`` reads as off."""
    return getattr(config, "autonomy_kill_switch", False) is True


#: The origin ``create_session`` writes for a self-development run when the
#: door stamped none: the Self-development page, ``ij`` and a rerun of one
#: — a PERSON started it. (Autonomy's own self-dev proposals stamp
#: "autonomy".)
SELF_DEV_ORIGIN = "self_dev"


def emergency_stop_covers(origin: str | None) -> bool:
    """Does Emergency stop refuse (and stop) a run of ``origin``? Every
    unattended door (``is_unattended_origin``) EXCEPT the ``self_dev``
    default above: that run was started by a person, and the setting
    promises "work you start keeps going". (Review of v1.351.0 lane 1A:
    engaging the switch cancelled a running self-development session and
    refused its rerun.) ``is_unattended_origin`` itself is unchanged — the
    background lane and the busy count keep their own reading."""
    text = str(origin or "").strip()
    return text != SELF_DEV_ORIGIN and is_unattended_origin(text)


#: The run option a session a PERSON started through an unattended-shaped
#: door carries (v1.352.0): a workflow the user ran by pressing Run stamps its
#: agent steps ``workflow:<name>`` exactly as a schedule or a rule does, so
#: the origin cannot tell them apart. ``create_session(started_by_person=
#: True)`` — the ONE way to set it — admits the step under Emergency stop and
#: writes this key, which ``stop_unattended`` reads to leave the run going.
PERSON_STARTED_OPTION = "started_by_person"


def person_started(options_json: Any) -> bool:
    """Does a session row's ``options_json`` say a person started it?
    Junk reads as no. Never raises."""
    try:
        raw = json.loads(options_json) if isinstance(options_json, str) else options_json
    except (TypeError, ValueError):
        return False
    return isinstance(raw, dict) and raw.get(PERSON_STARTED_OPTION) is True


def busy_notice_threshold(config) -> int:
    """``config.busy_runs_notice_at``, defaulted and sanitized (0 = never)."""
    raw = getattr(config, "busy_runs_notice_at", None)
    if raw is None:
        return BUSY_NOTICE_DEFAULT
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return BUSY_NOTICE_DEFAULT


class _SlotTicket:
    """A run waiting at ``run_session``'s gate for a governed slot.

    It sits in the SAME FIFO as ``spawn_managed``'s parked coroutines (in the
    coroutine's place), so ``_dequeue_next``, cancel, delete and shutdown treat
    both alike. The waiter may live on another event loop — a schedule fire
    runs under ``asyncio.run`` on APScheduler's thread — so it is woken with
    ``call_soon_threadsafe`` on its own loop, never by touching its future from
    here."""

    __slots__ = ("loop", "future")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.future: asyncio.Future = loop.create_future()

    def admit(self) -> bool:
        """Wake the waiter: it has a slot. False when its loop is gone."""
        return self._settle(True)

    def close(self) -> None:
        """Wake the waiter WITHOUT a slot (shutdown, removed from the queue)."""
        self._settle(False)

    def _settle(self, admitted: bool) -> bool:
        def _set() -> None:
            if not self.future.done():
                self.future.set_result(admitted)

        try:
            self.loop.call_soon_threadsafe(_set)
        except RuntimeError:  # the loop closed: its run is already gone
            return False
        return True


def _completion_payload(session: Session, **extra: Any) -> dict:
    """The ``session.completed`` payload, the SAME keys at every publish site
    (success, ``_finalize_failed``, ``_finalize_cancelled``).

    ``origin`` and ``project_id`` (v1.309.0) are additive and ALWAYS present
    (``None`` when the run has none): the bell can only say "Objective
    finished" if the event can tell a mission (``job:mission``) from a
    teammate (``job:mission-member``) and from the hundreds of other runs that
    finish every day — and a reader must never have to guess whether a key
    was dropped or simply empty."""
    return {
        "status": session.status.value,
        "summary": session.summary,
        **extra,
        "origin": getattr(session, "origin", None) or None,
        "project_id": getattr(session, "project_id", None) or None,
        # The ledger's verdict on the JOB (v1.309.0 integration): the bell
        # says "Your objective is done" only for a completed outcome, and
        # "needs you" when an ask went unanswered — the status alone cannot.
        "outcome": getattr(session, "outcome", None) or None,
    }


def _stored_options(session: Session) -> dict:
    """The run options a session was created with (v1.299.0), as a dict —
    ``{}`` for a legacy row or junk in the column. Shared by ``rerun_session``
    and ``continue_session`` so neither can disagree about what the user
    asked for."""
    from ..scheduling.knobs import session_options

    return session_options(session)


def _stored_allow_tools(session: Session) -> list[str]:
    """The up-front tool grant a session was created with, as a clean list.

    Shared by ``rerun_session`` and ``continue_session`` so a follow-up run can
    never disagree with a rerun about what the user already approved. Junk in
    the column (hand-edited, older shape) reads as NO grant — the fail-closed
    direction.
    """
    import json as _json

    try:
        raw = _json.loads(getattr(session, "allow_tools_json", "") or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(t) for t in raw if t]


def normalize_max_steps(value: Any) -> int | None:
    """A per-session step budget as it may be STORED (v1.174.0, Contract 4).

    The HTTP boundary already rejects anything outside
    ``SESSION_MAX_STEPS_MIN..MAX`` with a 422 (``schemas._clean_max_steps``) —
    this is the defence for DIRECT Python callers (workflows, schedules,
    delegation, tests), which have no validator in front of them. It is
    deliberately forgiving where the API is strict, because there is nobody to
    show an error to: junk (``None``, a non-number, ``True``, zero or negative)
    becomes ``None`` = "use ``config.max_agent_steps``", the pre-v1.174.0
    behavior; an over-large number is CLAMPED to the ceiling rather than
    honored, since an unbounded loop is a runaway, not a feature.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        steps = int(value)
    except (TypeError, ValueError):
        return None
    if steps < SESSION_MAX_STEPS_MIN:
        return None
    return min(steps, SESSION_MAX_STEPS_MAX)


class Orchestrator:
    def __init__(self, platform) -> None:
        self.p = platform
        self.runtime = AgentRuntime(platform)
        self._git_sessions: dict[str, GitSession] = {}
        self._reviews: dict[str, ReviewRequest] = {}
        # session_id -> the asyncio.Task running it (for cancellation). Only
        # background (wait=false) runs register here; synchronous runs are not
        # cancellable (the request itself blocks).
        self._running: dict[str, asyncio.Task] = {}
        # Serializes the check-then-create of a workspace-reusing continuation so a
        # double-continue can't start two agents writing the same shared workspace.
        self._continue_lock = asyncio.Lock()
        # Concurrency governor (v1.166.0): runs parked because every
        # ``max_concurrent_sessions`` slot was busy — FIFO of (session_id,
        # UN-started coroutine, had_session_row). The coroutine is never
        # awaited while parked and is ``.close()``d on discard so a dropped
        # entry can't leak a "coroutine was never awaited" warning.
        # had_session_row remembers whether a Session row existed at park time:
        # non-session background work (workflow runs, slack handlers) shares
        # the same launcher and has no row, so "row gone" only means
        # "deleted while queued" for entries that HAD one.
        self._queued: deque[tuple[str, Any, bool]] = deque()
        # The GOVERNED denominator (v1.167.0): session ids whose runs count
        # against ``max_concurrent_sessions``. ``_running`` also holds
        # non-session background work (workflow runs, slack handlers) — gating
        # the limit on len(_running) let one long workflow starve every agent
        # session while ZERO sessions ran, the mirror of the surprise the
        # no-row exemption exists to prevent.
        self._governed: set[str] = set()
        # Set by shutdown_queue() during daemon teardown: once draining, the
        # slot-free hook must never promote a parked run (lifespan cancels the
        # running tasks, and each cancellation fires _release -> _dequeue_next,
        # which would otherwise create_task a brand-new agent run mid-shutdown)
        # and spawn_managed refuses new work instead of parking it into a queue
        # that has already been discarded.
        self._draining = False
        # Strong refs to fire-and-forget event publishes from sync code paths
        # (an unreferenced Task can be garbage-collected mid-flight).
        self._event_tasks: set[asyncio.Task] = set()
        # v1.333.0: the governor is shared across THREADS. A schedule fire
        # runs ``run_session`` under ``asyncio.run`` on APScheduler's thread,
        # so the check-then-take of a slot, the FIFO and the busy count are
        # guarded by one lock (re-entrant, so a helper that takes it may be
        # called by one that already holds it). ``_home_loop`` is the
        # daemon loop ``spawn_managed`` runs on: a parked coroutine promoted
        # by a run finishing on another thread is started THERE, not on a
        # scheduler loop that is about to close.
        self._gov_lock = threading.RLock()
        self._home_loop: asyncio.AbstractEventLoop | None = None
        # The busy notice (v1.333.0): ids of UNATTENDED runs working right
        # now (every door, counted in run_session); whether this episode
        # already rang; when the last notice went out; "Not now" quiet until.
        self._unattended_live: set[str] = set()
        self._busy_notified = False
        self._busy_last_at: float | None = None
        self._busy_quiet_until = 0.0
        self._clock = time.monotonic  # a seam: tests count with a fake clock
        # v1.351.0 (G13): why a run is being stopped when it is not the user's
        # Cancel button — Emergency stop. Read by the cancel paths and popped
        # by ``_finalize_cancelled``, so the row says what really happened.
        self._stop_reasons: dict[str, str] = {}

    def register_running(self, session_id: str, task: asyncio.Task) -> None:
        """Track a background run so it can be cancelled (called by the daemon).

        Always attaches a self-removing done-callback so a finished/failed run can't
        leak its ``_running`` entry — the autonomy non-wait path registers here
        directly (not via the daemon's _spawn_bg), and previously leaked an entry
        per auto-executed/approved session, inflating running_sessions forever.
        The same callback is the queue's slot-free hook (v1.166.0): ANY managed
        run finishing frees a slot, so the next parked run (if any) starts."""
        self._running[session_id] = task

        def _release(t: asyncio.Task, sid: str = session_id) -> None:
            self._running.pop(sid, None)
            self._governed.discard(sid)  # no-op for ungoverned (no-row) work
            self._dequeue_next()  # a slot just freed — start the next parked run

        task.add_done_callback(_release)

    # --- concurrency governor (v1.166.0): spawn or park background runs ----

    def spawn_managed(self, session_id: str, coro) -> asyncio.Task | None:
        """Launch a background run now, or park it FIFO when every slot is busy.

        ``config.max_concurrent_sessions`` (0 = unlimited) governs how many
        managed background tasks may run at once. Under the limit (or with the
        limit unset) this is EXACTLY the daemon's historical ``_spawn_bg``:
        create the task, register it for cancellation, surface (log) any crash,
        return the task. At the limit the coroutine is stored UN-started, the
        session row (when one exists — workflow/comm ids share this launcher
        and have none) is marked QUEUED, SESSION_QUEUED is published, and None
        is returned; ``register_running``'s done-callback starts the next
        parked entry as each slot frees.

        Two refusals return None WITHOUT parking (coroutine closed, row
        untouched): the daemon is draining (``shutdown_queue`` ran — parking
        would leak a coroutine into a queue already discarded), or the session
        row is already terminal — the create->spawn window lost a race to
        cancel/delete (the same race ``run_session`` honors at its top), and
        stamping QUEUED over CANCELLED would resurrect work the user was told
        was cancelled and finish it COMPLETED.

        NON-SESSION work is exempt (coordinator decision, v1.166.0): workflow
        runs, comm/slack handlers, and other no-row ids share this launcher,
        but the setting is named max_concurrent_SESSIONS — parking a workflow
        behind agent sessions would be a surprise the name does not disclose,
        and a parked workflow that itself spawns sessions could deadlock the
        queue. No Session row -> always start."""
        if self._draining:
            coro.close()
            log.warning("refused background spawn for %s: shutting down", session_id)
            return None
        try:
            self._home_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # no loop: _start_managed raises as it always did
        limit = self._session_limit()
        if limit <= 0:
            return self._start_managed(session_id, coro)
        session = self.get_session(session_id)
        if session is None:  # non-session background work is never governed
            return self._start_managed(session_id, coro)
        with self._gov_lock:
            if len(self._governed) < limit:  # count SESSIONS, not every bg task
                self._governed.add(session_id)
                return self._start_managed(session_id, coro)
            if session.status is not SessionStatus.ACTIVE:
                coro.close()  # never park a terminal row (see docstring); honest no-op
                log.info(
                    "not queueing session %s: already %s", session_id, session.status.value
                )
                return None
            self._queued.append((session_id, coro, True))
            session.status = SessionStatus.QUEUED
            self._save(session)
            position = len(self._queued)
        self._publish_bg(
            EventType.SESSION_QUEUED,
            {
                "task": session.task,
                "position": position,
            },
            session_id,
        )
        return None

    def _session_limit(self) -> int:
        """``config.max_concurrent_sessions`` as an int (0 = no cap)."""
        try:
            return int(getattr(self.p.config, "max_concurrent_sessions", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _start_managed(self, session_id: str, coro) -> asyncio.Task:
        """Today's ``_spawn_bg`` semantics: create + register + surface crashes.

        Exceptions are retrieved and logged (never swallowed silently, never
        left as an "exception was never retrieved" warning); the ``_running``
        entry self-removes via ``register_running``'s done-callback."""
        task = asyncio.create_task(coro)
        self.register_running(session_id, task)

        def _done(t: asyncio.Task) -> None:
            try:
                t.result()
            except asyncio.CancelledError:  # pragma: no cover - expected on cancel
                pass
            except Exception:  # noqa: BLE001
                log.exception("background session %s failed", session_id)

        task.add_done_callback(_done)
        return task

    def _dequeue_next(self) -> None:
        """Start the next parked run if a slot is free — ONE per freed slot.

        Entries whose session was cancelled/deleted while parked are skipped
        (their un-started coroutine closed so nothing leaks) and popping
        continues until a runnable entry is found or the queue is empty. No
        recursion: the started run triggers the NEXT dequeue only via its own
        done-callback when it finishes. Inert while draining: lifespan teardown
        cancels the running tasks and each cancellation lands here via
        ``_release`` — promoting a parked row to ACTIVE and create_task'ing a
        brand-new agent run mid-shutdown is exactly wrong.

        v1.333.0: an entry is either an un-started coroutine (``spawn_managed``)
        or a :class:`_SlotTicket` (a run already started by a door that never
        went through ``spawn_managed``, waiting at ``run_session``'s gate) —
        one FIFO, so the two are served in arrival order. A ticket is ADMITTED
        (woken on its own loop) instead of started. A coroutine promoted by a
        run that finished on another thread (a schedule fire) is started on
        the daemon loop it was parked from (``_start_promoted``)."""
        if self._draining:
            return
        limit = self._session_limit()
        with self._gov_lock:
            while self._queued:
                if limit > 0 and len(self._governed) >= limit:
                    return  # every governed slot still busy
                session_id, coro, had_row = self._queued.popleft()
                session = self.get_session(session_id)
                if had_row and (
                    session is None or session.status is not SessionStatus.QUEUED
                ):
                    coro.close()  # cancelled/deleted while parked — never start it
                    continue
                if session is not None:
                    session.status = SessionStatus.ACTIVE
                    self._save(session)
                self._governed.add(session_id)  # promoted into a governed slot
                if isinstance(coro, _SlotTicket):
                    if coro.admit():
                        return  # one dequeue per freed slot
                    # Its loop is gone, so no run will ever take the slot.
                    self._governed.discard(session_id)
                    continue
                self._start_promoted(session_id, coro)
                return  # one dequeue per freed slot

    def _start_promoted(self, session_id: str, coro) -> None:
        """Start a promoted coroutine on the daemon loop (v1.333.0). Called on
        a scheduler thread, ``create_task`` would put the run on THAT loop,
        which ``asyncio.run`` closes (cancelling the run) the moment its fire
        returns — so it hops to ``_home_loop`` when that loop is live and is
        not the one running here."""
        home = self._home_loop
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if home is not None and home is not here and home.is_running():
            try:
                home.call_soon_threadsafe(self._start_promoted_here, session_id, coro)
                return
            except RuntimeError:  # closed between the check and the call
                pass
        self._start_promoted_here(session_id, coro)

    def _start_promoted_here(self, session_id: str, coro) -> None:
        # A FRESH context (v1.333.0 review). This runs in whatever context
        # freed the slot: ``register_running``'s done-callback captured the
        # context of the run that registered it, and a self-registered run
        # registers INSIDE ``run_session`` — so a person's parked run promoted
        # by a schedule fire's end inherited that fire's background lane
        # (below-normal priority for every child it starts) and its
        # ``_IN_RUN`` mark. A promoted run is a root run: it starts clean and
        # ``run_session`` sets its own lane.
        try:
            contextvars.Context().run(self._start_managed, session_id, coro)
        except RuntimeError:  # event loop gone (shutdown) — never started
            self._governed.discard(session_id)
            coro.close()  # reconcile marks the stranded row on next boot
            log.warning("could not start queued session %s (no loop)", session_id)

    def _remove_queued(self, session_id: str) -> bool:
        """Drop a parked entry, closing its un-started coroutine (or waking a
        gate ticket without a slot). True if found."""
        with self._gov_lock:
            for i, entry in enumerate(self._queued):
                if entry[0] == session_id:
                    del self._queued[i]
                    entry[1].close()
                    return True
        return False

    def shutdown_queue(self) -> int:
        """Daemon shutdown: stop the governor and discard every parked run.

        Sets ``_draining`` FIRST, then pops + ``.close()``s every un-started
        coroutine (no "never awaited" warning can leak). Call this BEFORE the
        lifespan teardown cancels running tasks — each cancellation fires
        ``_release`` -> ``_dequeue_next``, which the flag makes inert; without
        it a cancellation would promote a parked row and start a brand-new
        agent run mid-shutdown. Rows are left QUEUED on purpose:
        ``reconcile_interrupted_sessions`` marks them FAILED ("interrupted by
        a daemon restart") at next boot, which is the truth — this process
        never ran them. Returns the number of parked entries discarded.
        Wired into daemon/app.py's lifespan finally by the coordinator."""
        self._draining = True
        discarded = 0
        with self._gov_lock:
            while self._queued:
                # A gate ticket's close() wakes its run WITHOUT a slot: that
                # run returns its row still QUEUED, for the same reconcile.
                _sid, coro, _had_row = self._queued.popleft()
                coro.close()
                discarded += 1
        if discarded:
            log.info("discarded %d queued session(s) at shutdown", discarded)
        return discarded

    def _publish_bg(self, type_: str, payload: dict, session_id: str) -> None:
        """Publish an event from a SYNC caller: schedule it on the running loop
        and keep a strong ref until it settles. No loop (bare sync test) = no
        event — spawn paths always run on the daemon's loop in production."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        t = loop.create_task(
            self.p.event_bus.publish(type_, payload, session_id=session_id)
        )
        self._event_tasks.add(t)
        t.add_done_callback(self._event_tasks.discard)

    def _save(self, session: Session) -> None:
        with session_scope(self.p.engine) as db:
            db.merge(session)
            db.commit()

    def _git_enabled(self) -> bool:
        cfg = self.p.config
        return bool(getattr(cfg, "git_native", False)) and (
            Path(cfg.project_root) / ".git"
        ).exists()

    def _self_dev_repo(self) -> Path:
        """Resolve the Iron Jarvis repo for a self-dev session, or raise.

        Self-development is OPT-IN: it requires ``config.self_dev_enabled`` and a
        locatable git checkout of Iron Jarvis. Raising here keeps the capability
        fail-closed — an agent cannot reach its own source unless the user has
        explicitly turned it on.
        """
        from ..core.self_dev import iron_jarvis_repo_root

        cfg = self.p.config
        if not getattr(cfg, "self_dev_enabled", False):
            raise PermissionError(
                "self-dev is disabled; set self_dev_enabled = true in config to let "
                "agents edit Iron Jarvis's own source"
            )
        root = iron_jarvis_repo_root(cfg)
        if root is None:
            raise RuntimeError(
                "self-dev is enabled but the Iron Jarvis git repo could not be located "
                "(running from an installed package?); set self_dev_root to the checkout path"
            )
        return root

    async def create_session(
        self,
        task: str,
        agent_type: AgentType = AgentType.BUILDER,
        provider: str | None = None,
        model: str | None = None,
        self_dev: bool = False,
        project_id: str | None = None,
        allow_tools: list[str] | None = None,
        workspace_root: str | None = None,
        origin: str | None = None,
        max_steps: int | None = None,
        agent_name: str | None = None,
        approval_mode: str | None = None,
        trust: str | None = None,
        trust_reason: str = "",
        options: dict | None = None,
        prior_tool_results: list | None = None,
        chat_thread_id: str = "",
        started_by_person: bool = False,
    ) -> Session:
        """Create (never start) a session row.

        ``started_by_person`` (v1.352.0): a PERSON started this run through a
        door whose origin reads as unattended — today one door, a workflow the
        user ran by pressing Run (its agent steps are stamped ``workflow:
        <name>`` like a trigger's). Emergency stop admits it, and the row
        carries ``options["started_by_person"] = True`` so engaging the switch
        later (``stop_unattended``) leaves it going. The kwarg is the ONLY way
        to set the key: one a door put in ``options`` is dropped.

        ``chat_thread_id`` (v1.345.0, lane T): the SAVED chat this work was
        asked in. Stored under ``options["chat_thread_id"]`` only when
        non-empty (a door may also put it in ``options``; the kwarg wins),
        so a run no chat waits on is byte-identical. Every terminal path of
        a session carrying it appends ONE line to that chat
        (``agents/chat_delivery``) — the reply comes home even when the user
        has moved on.

        ``prior_tool_results`` (v1.344.0, lane P): what the chat turn that
        escalated into this session already ran (the done frame's rows,
        ``{tool, args_summary, ok, output_head}``). Stored under
        ``options["prior_tool_results"]`` after ``runtime.
        normalize_prior_tool_results`` (masked, capped, newest kept) — a
        door may also hand the rows in ``options`` directly; either way the
        row holds the normalised list or no key at all, so a run with
        nothing to carry is byte-identical to before.

        ``options`` (v1.299.0): the RUN OPTIONS a door states, stored as the
        row's ``options_json`` and read by the runtime through
        ``scheduling/knobs.session_options`` — ``skip_memory`` (no lessons /
        memory index / fabric injection) and ``folder_rules`` (load the
        workspace's AGENTS.md / .ironjarvis.md). None = ``{}`` = today's
        behaviour. Rerun and continue inherit it: the options are part of the
        session's INPUTS, like the grant and the step budget.

        ``trust`` / ``trust_reason`` (v1.298.0): the posture the DOOR states —
        ``"low"`` for a run started from an inbound message or by a low
        parent, anything else (None, "", "full") normalises to full HERE, at
        the one door every session passes (``core.trust.normalize_trust``).
        The reason is kept only when the row is low.

        ``approval_mode`` (v1.232.0, audit A7) is the chat posture the
        escalation carried — normalised through ``inherited_approval_mode``
        HERE, at the one door every session passes, so ``yolo`` can reach no
        row from any caller.

        ``agent_name`` (v1.193.0) is the ROSTER NAME this run must be CREDITED
        to — ``"custom:tax-reader"`` / ``"remote:hermes"`` / a bare builtin type
        — passed by whichever door resolved it (``POST /agents/{name}/spawn``,
        ``delegate``, ``spawn_agent``). It is the strongest attribution signal
        there is, because it does not depend on an event having been published;
        omitted (the normal case for a plain ``POST /sessions``) the readers
        fall back to the delegation ledger and then to ``agent_type``.

        DELIBERATELY NOT CLONED BY ``rerun_session`` / ``continue_session``:
        both re-run through ``run_session`` with NO definition, i.e. with the
        BASE builtin prompt, so stamping the custom teammate's name on them
        would manufacture a track record for a prompt that never ran.
        """
        import json as _json

        # EMERGENCY STOP (v1.351.0, G13): every unattended door — a schedule,
        # a goal iteration, a reflex rule, a workflow's agent step, the
        # phone, a queued assignment, autonomy — ends here, so this is the one
        # refusal. Judged on the origin the DOOR stamped (before the self-dev
        # default below): an untagged run is a person, never refused.
        # v1.352.0: a run a PERSON started (a workflow's Run press) is
        # admitted — the setting promises "work you start keeps going".
        if (
            not started_by_person
            and emergency_stop_covers(origin)
            and emergency_stop_on(self.p.config)
        ):
            raise EmergencyStopOn(EMERGENCY_STOP_REFUSAL)

        # LOCAL ONLY (v1.332.0). A session is local only when the door says so
        # (``options["local_only"]``: the chat's escalation, an assignment
        # queued by a local-only run) OR it is created INSIDE a local-only
        # scope — a delegate / spawn_agent child, a mission teammate, anything
        # a local-only turn starts — so the posture spreads down like trust.
        # Refused HERE, at the one door every session passes, when its model
        # leaves this network: nothing is created, the caller gets
        # ``LocalOnlyRefused`` (the routes' 409 shape). Rerun and continue
        # keep it, because they copy the options.
        from ..providers import egress as _egress

        options = dict(options) if isinstance(options, dict) else {}
        # WHO STARTED IT (v1.352.0): only the kwarg writes the marker.
        if started_by_person:
            options[PERSON_STARTED_OPTION] = True
        else:
            options.pop(PERSON_STARTED_OPTION, None)
        if options.get("local_only") is True or _egress.current_scope().on:
            options["local_only"] = True
            _lo_provider = provider or self.p.config.default_provider
            # v1.362.0: the model too — a name a model proxy sends to the
            # cloud is refused like a cloud provider. The configured default
            # model belongs to the configured default provider only.
            _lo_model = str(model or (
                self.p.config.default_model if _lo_provider == self.p.config.default_provider else ""
            ) or "")
            if not _egress.stays_on_this_network(
                _lo_provider, getattr(self.p, "providers", None), self.p.config, _lo_model
            ):
                raise _egress.LocalOnlyRefused(
                    _lo_provider,
                    str(model or self.p.config.default_model or ""),
                    manager=getattr(self.p, "providers", None),
                )
        else:
            options.pop("local_only", None)
        # WHAT THE CHAT ALREADY DID (v1.344.0, lane P): the kwarg wins over a
        # list a door put in ``options``; both go through the ONE normaliser
        # the runtime renders with, so the row never holds a secret value or
        # more than the caps — and never an empty key.
        _prior_rows = (
            prior_tool_results
            if prior_tool_results is not None
            else options.get("prior_tool_results")
        )
        _prior_clean = normalize_prior_tool_results(_prior_rows)
        if _prior_clean:
            options["prior_tool_results"] = _prior_clean
        else:
            options.pop("prior_tool_results", None)
        # THE CHAT THIS WAS ASKED IN (v1.345.0, lane T): same rule as the
        # rows above — the kwarg wins over a door's ``options`` entry, one
        # trimmed id or no key at all.
        _chat_id = _chat_delivery.clean_chat_thread_id(
            chat_thread_id or options.get(_chat_delivery.CHAT_THREAD_OPTION)
        )
        if _chat_id:
            options[_chat_delivery.CHAT_THREAD_OPTION] = _chat_id
        else:
            options.pop(_chat_delivery.CHAT_THREAD_OPTION, None)

        repo_for_worktree: Path | None = None
        # A project-folder task runs DIRECTLY in the user's folder (full
        # read/write there, confined to it) — not a disposable worktree — so
        # its deliverables land where the user expects. workspace_root wins
        # over git-native for exactly this reason.
        direct_root = None
        note = ""
        if workspace_root:
            direct_root = Path(workspace_root)
        elif project_id and not self_dev:
            # ONE execution seam (v1.231.0, audit AE1/T4): a session tagged to
            # a project but handed no folder — a schedule, a reflex rule, a
            # goal iteration, a phone escalation, a bare ``POST /sessions``
            # — runs in the PROJECT'S folder through the same door the
            # Projects route and ``POST /sessions`` apply to an explicit
            # root (``fs_policy.root_problem``), so every door lands where
            # the Projects door does with no per-door change. A root that is
            # set but unusable is RECORDED on the row (``note``), never
            # silently swapped for a scratch dir. Off the loop: the door
            # probes writability.
            root, note = await asyncio.to_thread(self._project_folder, project_id)
            if root:
                direct_root = Path(root)
        if direct_root is None and self_dev:
            # Gated self-development: edit Iron Jarvis itself on a worktree of its
            # OWN repo, as the Maintainer, still review-gated (never auto-merge).
            repo_for_worktree = self._self_dev_repo()
            agent_type = AgentType.MAINTAINER
        elif direct_root is None and self._git_enabled():
            repo_for_worktree = Path(self.p.config.project_root)

        session = Session(
            task=task,
            agent_type=agent_type,
            # WHO ran (v1.193.0) — see the docstring. "" = not recorded, which
            # readers resolve exactly as they did before this column existed.
            agent_name=" ".join(str(agent_name or "").split()),
            provider=provider or self.p.config.default_provider,
            model=model or self.p.config.default_model,
            status=SessionStatus.ACTIVE,
            # A project only applies INSIDE the Projects module: a session carries
            # a project ONLY when one is passed explicitly (project tasks, and
            # delegated/spawned children inheriting their parent's). Sessions
            # started anywhere else are project-agnostic — the globally "active"
            # project never leaks in.
            project_id=project_id,
            allow_tools_json=_json.dumps(list(allow_tools or [])),
            approval_mode=inherited_approval_mode(approval_mode),
            # TX-01 provenance: WHO/WHAT started this (user_chat, autonomy,
            # schedule, comm, reflex, …). self_dev is inferable; everything else
            # is passed by the caller. Defaults None = unattributed (the audit
            # timeline falls back to inferring from the spawning event).
            origin=origin or ("self_dev" if self_dev else None),
            # Contract 4 (v1.174.0): per-session step budget. None = the
            # configured ``max_agent_steps`` (today's behavior).
            max_steps=normalize_max_steps(max_steps),
            # The folder note (see ``_project_folder``) rides ``summary`` until
            # the run finishes; the finalizers keep it under the result.
            summary=note,
            # THE TRUST POSTURE (v1.298.0), normalised at this one door.
            trust=_normalize_trust(trust),
            trust_reason=(
                " ".join(str(trust_reason or "").split())
                if _normalize_trust(trust) == _TRUST_LOW
                else ""
            ),
            # THE RUN OPTIONS (v1.299.0) — see the docstring. A non-dict is
            # refused into "{}", never stored as something the reader cannot
            # decode.
            options_json=_json.dumps(
                dict(options) if isinstance(options, dict) else {}, default=str
            ),
        )
        if direct_root is not None:
            direct_root.mkdir(parents=True, exist_ok=True)
            workspace = direct_root  # the agent works IN the real project folder
        else:
            workspace = self.p.config.workspaces_dir / session.id
            if repo_for_worktree is not None:
                try:  # git-native: a worktree on a session branch (§27)
                    gs = GitSession.start(
                        repo_for_worktree, workspace, slug=task[:40] or "session"
                    )
                    self._git_sessions[session.id] = gs
                except Exception:
                    if self_dev:
                        raise  # self-dev MUST run on a worktree; never fall back
                    workspace.mkdir(parents=True, exist_ok=True)  # plain ws
            else:
                workspace.mkdir(parents=True, exist_ok=True)
        session.workspace_path = str(workspace)
        self._save(session)
        await self.p.event_bus.publish(
            EventType.SESSION_CREATED,
            {"task": task, "agent": agent_type.value, "workspace": session.workspace_path},
            session_id=session.id,
        )
        return session

    def _project_folder(self, project_id: str) -> tuple[str | None, str]:
        """``(folder, note)`` for a project-tagged session with no explicit
        root (v1.231.0, audit AE1/T4). The folder is the project's root when
        it passes ``fs_policy.root_problem`` — the SAME door the Projects
        route (``_root_problem``) and ``POST /sessions`` apply — else None
        with a note the row carries (the twin of the workflow engine's
        ``_project_folder`` note, v1.225.0): the project row is gone, or its
        folder is set but missing / protected / not writable. A project
        without a folder is chat-only by design: no folder, no note.
        BLOCKING (the writability probe creates a file) — callers hop it
        through ``asyncio.to_thread``."""
        from ..core.fs_policy import root_problem

        with session_scope(self.p.engine) as db:
            project = db.get(Project, project_id)
        if project is None:
            return None, (
                f"{FOLDER_NOTE_PREFIX}this run is tagged to project {project_id}, "
                "which no longer exists — it worked in a scratch workspace, not "
                "in a project folder"
            )
        root_raw = (project.root or "").strip()
        if not root_raw:
            return None, ""
        try:
            problem = root_problem(root_raw)
        except Exception as exc:  # noqa: BLE001 — an unparseable root is a problem, not a crash
            problem = f"folder cannot be checked: {exc}"
        if problem is None:
            return root_raw, ""
        return None, (
            f"{FOLDER_NOTE_PREFIX}project \u201c{project.name}\u201d folder could not be "
            f"used ({problem}) — this run worked in a scratch workspace, NOT in "
            "the project folder; fix the folder on the project page and run again"
        )

    async def run_session(
        self, session_id: str, definition: "AgentDefinition | None" = None
    ) -> Session:
        """Run one session to its end. EVERY root run passes here, whichever
        door started it, so this is the one place (v1.333.0) that:

        * marks an UNATTENDED run (a tagged origin not in
          ``runtime.ATTENDED_ORIGINS``; see ``is_unattended_origin``) as
          background work for its whole length — ``procs.background_lane``
          rides the context into every task and worker thread the run makes,
          so the children it spawns run below normal priority while the user's
          own window keeps the CPU;
        * counts it among the unattended runs in flight and, past
          ``busy_runs_notice_at`` with no cap set, rings the bell ONCE
          (``runs.busy``) to ask whether to queue the extras;
        * holds ``max_concurrent_sessions`` for the doors that never went
          through ``spawn_managed`` (see ``_run_session_body``).
        """
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session '{session_id}'")
        unattended = is_unattended_origin(getattr(session, "origin", None))
        nested = _IN_RUN.get()
        token = _IN_RUN.set(True)
        from ..providers import failure_words as _failure_words

        try:
            # v1.359.0 (wave 9): a failure worded anywhere inside a run (its
            # summary, the background-job banner, an alert) says what to do
            # where a run is read — "Run it again, or choose another model in
            # Settings." — never the chat's "…pick another model for this chat".
            with procs.background_lane(on=unattended), _failure_words.run_surface():
                return await self._run_session_body(
                    session, definition, unattended=unattended, nested=nested
                )
        finally:
            _IN_RUN.reset(token)

    async def _run_session_body(
        self,
        session: Session,
        definition: "AgentDefinition | None",
        *,
        unattended: bool,
        nested: bool,
    ) -> Session:
        session_id = session.id
        # Honor a terminal status that WON the create→register race: a cancel that
        # landed while create_session was parked awaiting the SESSION_CREATED publish
        # leaves no _running task, so cancel_session's else-branch marked the row
        # CANCELLED (without publishing/GC). Never run the agent for an already
        # cancelled/finished session — that would execute (possibly irreversible)
        # work the user was told was cancelled, then overwrite it COMPLETED.
        if session.status is SessionStatus.CANCELLED:
            await self._finalize_cancelled(session)
            return session
        if session.status in (SessionStatus.COMPLETED, SessionStatus.FAILED):
            return session
        # Cancel must reach EVERY lane (v1.166.4). Only background (wait:false)
        # runs were registered via spawn_managed — a schedule-fired session or
        # any wait:true / directly-awaited lane had no _running entry, so
        # cancel_session's else-branch marked the row CANCELLED while the agent
        # kept calling the model and executing tools. Self-register the current
        # task when nobody else did (never clobber spawn_managed's entry — a
        # second register would double the slot-free hook and over-promote the
        # queue), and self-remove in the finally so the handle never outlives
        # the run: a wait:true HTTP task or a multi-step workflow task lives on
        # after run_session returns, and a stale entry would both lie to the
        # concurrency governor and dangle a cancellable handle at a finished run.
        self_registered = False
        current = asyncio.current_task()
        if current is not None and self._running.get(session_id) is None:
            self.register_running(session_id, current)
            self_registered = True
        # THE CAP HOLDS AT EVERY DOOR (v1.333.0). Only ``spawn_managed`` used
        # to park, and schedule fires, goal iterations, workflow agent steps
        # and the phone never pass it — a cap of 2 let three 3am schedules
        # run at once. A run that ``spawn_managed`` (or a promotion) already
        # admitted is in ``_governed``; any OTHER unattended run takes its
        # slot here, waiting QUEUED in the same FIFO when every slot is busy.
        # A run started from INSIDE another run (``nested``) never waits: its
        # ancestor holds a slot and may be awaiting it (see ``_IN_RUN``).
        # Attended runs (chat, a job the user pressed Run on) never wait —
        # the person is right there — but, as since v1.167.0, they occupy a
        # governed slot while they work, so parked runs don't over-promote.
        # Every run with a row is counted, whoever launched it: a run
        # ``spawn_managed`` started with no cap set is in ``_governed`` too,
        # so a cap set mid-flight ("Queue extras") counts what is already
        # running. The slot is released by ``register_running``'s callback
        # (or the self-registered finally below).
        gated = (
            unattended
            and not nested
            and session_id not in self._governed
            and self._session_limit() > 0
        )
        if not gated:
            self._governed.add(session_id)
        counted = False
        try:
            try:
                if gated:
                    admitted = await self._wait_for_slot(session)
                    if admitted is None:
                        # Woken WITHOUT a slot: it never ran. At shutdown the
                        # row stays QUEUED for the boot reconcile, exactly like
                        # a parked spawn_managed run. A cancel from another
                        # thread stamps the row CANCELLED before its
                        # ``task.cancel`` lands, and a promotion that reads
                        # that row first drops the ticket — so the stop must
                        # be finalized HERE (event, worktree, assignment), or
                        # the run returns and the cancel lands on nothing.
                        row = self.get_session(session_id) or session
                        if row.status is SessionStatus.CANCELLED:
                            await self._finalize_cancelled(row)
                        return row
                    session = admitted
                if unattended:
                    counted = True
                    await self._busy_enter(session_id)
                if session.agent_type is AgentType.SUPERVISOR:
                    run = await run_supervised(self.p, session)  # §12 delegate to subagents
                else:
                    # A dynamic (user-authored) agent runs with ITS definition, not the
                    # builtin one its base type maps to — callers pass it explicitly.
                    agent_def = definition or get_agent_definition(session.agent_type)
                    # THE WORKLIST REACHES THE AGENT THAT ACTUALLY DOES BULK WORK
                    # (v1.177.0). `with_worklist` was written general on purpose —
                    # its docstring records that the measured failure was a BUILDER,
                    # not a supervisor — and then nothing ever called it for one:
                    # `supervisor_definition()` was its only caller, so every
                    # `POST /sessions` and every project task (both default to
                    # BUILDER) ran a bulk job with no worklist tools and no
                    # survey-once procedure. MEASURED on a 26-file rename: the
                    # planner wrote "for each file, read its content" as ONE step
                    # because a durable list was not among the things it could use,
                    # and the run died mid-step with nothing recorded. That is the
                    # FOURTH time a capability shipped without reaching a roster
                    # (history_search v1.142, workflow_list v1.172, view_image
                    # v1.174) — check the roster, every time.
                    #
                    # Gated on the task, not applied blanket: a bulk job pays four
                    # tool specs plus the procedure, and a one-file edit should not.
                    if is_bulk_task(session.task or ""):
                        agent_def = with_worklist(agent_def)
                    run = await self.runtime.run(session, agent_def)

                session.status = (
                    SessionStatus.COMPLETED
                    if run.state is AgentState.COMPLETED
                    else SessionStatus.FAILED
                )
                session.provider, session.model = run.provider, run.model  # what actually ran
                prior_summary = session.summary  # "" or the create-time folder note
                session.summary = _with_folder_note(prior_summary, run.result)
                # THE HAND-OFF BUBBLE IS CHECKED AGAINST THE LEDGER (v1.227.0,
                # audit RT5). Both chat lanes already judged a reply's file
                # claim against what actually ran; the agent lane stored
                # ``run.result`` verbatim, so the escalated bubble said "I
                # saved report.docx" while the result card beneath it said
                # nothing was written. Same helper, same wording — and the
                # settle read below also hands the run's worklist claims
                # back and derives the honest ``outcome`` (A5/A8), all in ONE
                # hop off the loop.
                #
                # Lazy import, by import DIRECTION: ``daemon/`` imports the
                # agent layer (routes -> orchestrator), so a module-level
                # import here would be the platform layer importing the daemon
                # — the inversion v1.185.0 removed.
                from ..daemon.chat_turn import _claimed_write_note, custom_tools_used

                tools_used, outcome = await asyncio.to_thread(
                    self._settle_finished_run,
                    session,
                    getattr(run, "id", None),  # test doubles carry no id
                    session.status,
                )
                # v1.351.0: a user-made command tool that ran may have changed
                # the file the summary names (same rule as both chat lanes).
                note = _claimed_write_note(
                    run.result or "", tools_used,
                    custom_tools=custom_tools_used(self.p, tools_used),
                )
                if note:
                    session.summary = _with_folder_note(prior_summary, f"{run.result}{note}")
                session.outcome = outcome
                session.input_tokens = run.input_tokens
                session.output_tokens = run.output_tokens
                session.finished_at = utcnow()
                self._save(session)
                # THE REPLY COMES HOME (v1.345.0, lane T) — BEFORE the
                # completion event, so a page that re-reads the chat on
                # ``agent.completed`` finds the line already there.
                await self._deliver_to_chat(session)
                # v1.357.0 (G61): a task another computer sent answers home.
                self._reply_home(session)
                await self.p.event_bus.publish(
                    EventType.SESSION_COMPLETED,
                    _completion_payload(session),
                    session_id=session.id,
                )
            except asyncio.CancelledError:
                # The user stopped this run (POST /sessions/{id}/cancel). Mark it
                # CANCELLED (not FAILED), GC any worktree, then propagate so the
                # background task ends cancelled.
                await self._finalize_cancelled(session)
                raise
            except Exception as exc:  # noqa: BLE001
                # Any other failure (a provider blow-up that escaped the router, a DB
                # write error, a supervised-run crash) must NOT strand the session in
                # ACTIVE forever. Finalize it FAILED + emit SESSION_COMPLETED(ok=False)
                # so the dashboard stops spinning and the run is recoverable, then
                # re-raise for the caller/HTTP.
                await self._finalize_failed(session, exc)
                raise

            # Close the measurement->learning loop (evaluate + record outcome +
            # reflect). Runs for delegated/spawned children too, via the same
            # helper. Deliberately INSIDE the self-registration window: the
            # learning/review tail is part of the run (the exact tail whose
            # slowness raced the v1.166.1 CI fix), so the governed slot and the
            # cancellable handle release only when run_session truly ends.
            self._post_run_learning(session)
            await self._post_run_allowance(session)
            await self._post_run_assignment(session)

            # Phase 7: if this ran on a git worktree, build a review — never
            # auto-merge.
            gs = self._git_sessions.get(session.id)
            if gs is not None:
                try:
                    review = build_review(
                        gs,
                        session.id,
                        summary=session.summary,
                        tool_history=self.transcript(session.id)["tools"],
                    )
                    self._reviews[session.id] = review
                    self._persist_pending_review(session.id, gs)  # survives restart
                    await self.p.event_bus.publish(
                        EventType.REVIEW_REQUESTED,
                        {
                            "branch": review.branch,
                            "risk": review.risk,
                            "changed_files": review.changed_files,
                        },
                        session_id=session.id,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("failed to build review for session %s", session.id)
                    # v1.333.0 (D9): no review was stored, so nothing will
                    # ever approve or reject this one; do not hold it forever.
                    if session.id not in self._reviews:
                        self._git_sessions.pop(session.id, None)

            return session
        finally:
            if counted:
                self._busy_leave(session_id)
            if self_registered and self._running.get(session_id) is current:
                self._running.pop(session_id, None)
                self._governed.discard(session_id)
                self._dequeue_next()  # the governed slot frees NOW, not at task end

    async def _wait_for_slot(self, session: Session) -> Session | None:
        """Take a governed slot for a door that bypassed ``spawn_managed``
        (v1.333.0), parking QUEUED in the shared FIFO while every slot is
        busy. Returns the refreshed row once admitted, or None when the entry
        was discarded at shutdown (or dropped as cancelled by a promotion). A
        cancel while parked (``cancel_session`` cancels the run's registered
        task) leaves the queue and propagates, so ``run_session`` finalizes it
        CANCELLED like any other stop. While DRAINING it never parks: the
        queue was already discarded, so nothing would ever wake the wait, and
        a schedule fire parked there would hold APScheduler's worker thread
        (which the interpreter joins at exit) forever."""
        session_id = session.id
        loop = asyncio.get_running_loop()
        with self._gov_lock:
            limit = self._session_limit()
            if limit <= 0 or len(self._governed) < limit:
                self._governed.add(session_id)
                return session
            if self._draining:
                return None
            ticket = _SlotTicket(loop)
            # The row says QUEUED BEFORE the entry is visible: a promotion
            # skips an entry whose row is not QUEUED as cancelled-while-parked.
            session.status = SessionStatus.QUEUED
            self._save(session)
            self._queued.append((session_id, ticket, True))
            position = len(self._queued)
        try:
            try:
                await self.p.event_bus.publish(
                    EventType.SESSION_QUEUED,
                    {"task": session.task, "position": position},
                    session_id=session_id,
                )
            except Exception:  # noqa: BLE001 — the event is a courtesy; the wait is not
                log.exception("could not publish session.queued for %s", session_id)
            admitted = await ticket.future
        except asyncio.CancelledError:
            self._remove_queued(session_id)
            raise
        if not admitted:
            return None
        return self.get_session(session_id) or session

    # --- the busy notice (v1.333.0) -----------------------------------------

    async def _busy_enter(self, session_id: str) -> None:
        """Count an unattended run as working and ring the bell when this
        start makes the count unusual. Never raises: the notice is a
        courtesy, the run is the point."""
        with self._gov_lock:
            self._unattended_live.add(session_id)
            payload = self._busy_notice_due(len(self._unattended_live))
        if payload is None:
            return
        try:
            await self.p.event_bus.publish(EventType.RUNS_BUSY, payload)
        except Exception:  # noqa: BLE001
            log.exception("could not publish runs.busy")

    def _busy_leave(self, session_id: str) -> None:
        """The run ended: uncount it. Dropping back to the threshold ends the
        episode, so a LATER pile-up may ring again (at most once an hour)."""
        with self._gov_lock:
            self._unattended_live.discard(session_id)
            if len(self._unattended_live) <= busy_notice_threshold(self.p.config):
                self._busy_notified = False

    def _busy_notice_due(self, running: int) -> dict[str, Any] | None:
        """The ``runs.busy`` payload when a notice should go out now, else
        None. Caller holds ``_gov_lock``. ONE notice per episode (an episode
        lasts while the count stays above the threshold), never sooner than
        BUSY_NOTICE_GAP_S after the last one, never inside a "Not now" quiet,
        and never while a cap is set — a cap already answers the question.
        An episode that could not ring when it began (the hour, the quiet)
        may still ring on a later start inside it, once that has passed."""
        threshold = busy_notice_threshold(self.p.config)
        if threshold <= 0 or running <= threshold or self._busy_notified:
            return None
        if self._session_limit() > 0:
            return None
        now = self._clock()
        if now < self._busy_quiet_until:
            return None
        if self._busy_last_at is not None and now - self._busy_last_at < BUSY_NOTICE_GAP_S:
            return None
        self._busy_notified = True
        self._busy_last_at = now
        return {
            "running": running,
            "threshold": threshold,
            "cap": max(1, min(BUSY_SUGGESTED_CAP, threshold)),
        }

    def snooze_busy_notice(self, seconds: float = BUSY_SNOOZE_S) -> float:
        """The bell's "Not now": no ``runs.busy`` for ``seconds``. In memory
        on purpose — a restart starts with nothing running, so a fresh pile-up
        after one is worth one line in the bell. Returns the quiet length."""
        with self._gov_lock:
            self._busy_quiet_until = self._clock() + max(float(seconds), 0.0)
        return float(seconds)

    def unattended_running(self) -> int:
        """How many unattended runs are working right now (every door)."""
        return len(self._unattended_live)

    # --- Emergency stop (v1.351.0, readiness audit G13) -------------------

    def emergency_stop_reason(self, origin: str | None) -> str:
        """The sentence ``create_session`` would refuse a run of ``origin``
        with right now, or "" when it may start. For a door that should HOLD
        its work instead of trying and failing (the assignment dispatcher —
        a refused start there would strike the job). Never raises."""
        try:
            if emergency_stop_covers(origin) and emergency_stop_on(self.p.config):
                return EMERGENCY_STOP_REFUSAL
        except Exception:  # noqa: BLE001 — an unreadable switch is "off", like the loop reads it
            pass
        return ""

    def stop_unattended(
        self, reason: str = EMERGENCY_STOP_STOPPED, *, limit: int = 500
    ) -> list[str]:
        """Cancel every live run an UNATTENDED door started (a schedule, a
        goal, a reflex rule, a workflow step, the phone, an assignment,
        autonomy) — what engaging Emergency stop does to work already going.
        Attended work (chat, a mission, a project task, a run a person
        started) is never touched, and neither is a delegated teammate (it
        stops with the run it belongs to: cancelling the parent cancels the
        awaited child). Since v1.352.0 that includes a workflow step a
        person's Run press started (``options["started_by_person"]``).

        Reads the live rows (ACTIVE/QUEUED, bounded) rather than ``_running``
        alone, so a run parked in the governor's queue or a schedule fire on
        the scheduler's own loop is found too; each goes through
        ``cancel_session`` — the one stop path, cross-loop safe — with
        ``reason`` as its summary. BLOCKING (SQLite): call it from a sync
        route, a worker thread or ``asyncio.to_thread``. Returns the ids it
        stopped; never raises."""
        try:
            with session_scope(self.p.engine) as db:
                rows = db.exec(
                    select(Session.id, Session.origin, Session.options_json)
                    .where(
                        Session.status.in_(  # type: ignore[attr-defined]
                            [SessionStatus.ACTIVE, SessionStatus.QUEUED]
                        )
                    )
                    .limit(max(int(limit), 1))
                ).all()
        except Exception:  # noqa: BLE001
            log.exception("Emergency stop could not read the live runs")
            return []
        stopped: list[str] = []
        for sid, origin, options_json in rows:
            if not sid or not emergency_stop_covers(origin):
                continue
            if person_started(options_json):
                continue  # a person's workflow Run keeps going (v1.352.0)
            self._stop_reasons[sid] = reason
            try:
                self.cancel_session(sid)
            except (KeyError, ValueError):
                # Finished (or gone) between the read and the stop.
                self._stop_reasons.pop(sid, None)
                continue
            except Exception:  # noqa: BLE001 — one stuck run must not keep the rest going
                self._stop_reasons.pop(sid, None)
                log.exception("Emergency stop could not stop session %s", sid)
                continue
            stopped.append(sid)
        # v1.359.0 (wave 9, G13 remainder): a workflow a schedule, a rule, a
        # goal or the chat tool started that is PARKED on a question has no
        # live session for the loop above to find, so it kept waiting under
        # the switch (and its schedule row kept "Needs you").
        stopped.extend(self._stop_waiting_workflows(reason, limit=limit))
        if stopped:
            log.warning("Emergency stop stopped %d unattended run(s)", len(stopped))
        return stopped

    def _stop_waiting_workflows(self, reason: str, *, limit: int = 500) -> list[str]:
        """Stop every unattended workflow run PARKED on a question (status
        ``waiting``) — Emergency stop's half for work with no live session
        (v1.359.0, wave 9, G13 remainder). A run a person started
        (``WorkflowRunRecord.started_by == "person"``, the v1.352.0 mark) is
        never touched.

        Each run ends exactly as the Workflows page's Cancel ends a waiting
        one (status ``cancelled``, the question cleared, finished now) — a
        compare-and-set on ``waiting``, so an answer that claimed the run
        first (``waiting -> resuming``) wins and the live run is stopped by
        the session loop instead — with ``reason`` added to the run's notes
        so its page says why. Then the schedule that parked it is told
        (``platform.settle_stopped_waiting``, the v1.358.0 path), so its row
        reads Stopped, not Needs you. BLOCKING (SQLite); returns the run ids
        it stopped; never raises."""
        try:
            from sqlalchemy import or_ as _sql_or
            from sqlalchemy import update as _sql_update

            from ..workflows.engine import STARTED_BY_PERSON
            from ..workflows.models import WorkflowRunRecord

            with session_scope(self.p.engine) as db:
                rows = db.exec(
                    select(WorkflowRunRecord.id, WorkflowRunRecord.notes_json)
                    .where(
                        WorkflowRunRecord.status == "waiting",
                        # v1.359.0 review: a row parked before v1.352.0 on an
                        # UPGRADED install holds NULL here (the reconcile adds
                        # the column nullable), and SQL's ``NULL != 'person'``
                        # is NULL — the row was skipped while the engine reads
                        # it as "" (unattended). NULL is not a person.
                        _sql_or(
                            WorkflowRunRecord.started_by.is_(None),  # type: ignore[union-attr]
                            WorkflowRunRecord.started_by != STARTED_BY_PERSON,
                        ),
                    )
                    .limit(max(int(limit), 1))
                ).all()
                stopped: list[str] = []
                for run_id, notes_json in rows:
                    try:
                        notes = json.loads(notes_json or "[]")
                    except (TypeError, ValueError):
                        notes = []
                    if not isinstance(notes, list):
                        notes = []
                    if reason and reason not in notes:
                        notes.append(reason)
                    done = db.exec(
                        _sql_update(WorkflowRunRecord)
                        .where(
                            WorkflowRunRecord.id == run_id,
                            WorkflowRunRecord.status == "waiting",
                        )
                        .values(
                            status="cancelled",
                            waiting_json="",
                            finished_at=utcnow(),
                            notes_json=json.dumps(notes),
                        )
                    )
                    if getattr(done, "rowcount", 0) == 1:
                        stopped.append(str(run_id))
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("Emergency stop could not stop the waiting workflow runs")
            return []
        settle = getattr(self.p, "settle_stopped_waiting", None)
        for run_id in stopped:
            if callable(settle):
                try:
                    # v1.359.0 integrate: the row names Emergency stop as
                    # what stopped it (only stop_unattended calls this).
                    settle(run_id, by_emergency_stop=True)
                except Exception:  # noqa: BLE001 — the run already stopped
                    log.exception("could not settle the schedule row of %s", run_id)
        return stopped

    # --- the truth about a finished run (v1.227.0) ------------------------

    def _release_worklist_claims(self, session_id: str, run_ids=None) -> int:
        """Hand back every worklist item this session's runs still hold.

        BLOCKING (SQLite) — callers hop off the loop. ``WorklistStore.
        release_run`` existed since v1.174.0 with ZERO callers (audit A8): 55
        rows app-wide sat in ``doing``, every one claimed by a run that had
        COMPLETED, and the next run on the board was told they were "being
        worked on right now". Every finalize path lands here now: completion,
        failure, cancel, and the boot reconcile. Never raises — a bookkeeping
        miss must not strand a finalize.
        """
        store = getattr(self.p, "worklist", None)
        if store is None:
            return 0
        try:
            ids = [str(r) for r in (run_ids or []) if r]
            if not ids:  # no run named -> every run of the session
                with session_scope(self.p.engine) as db:
                    ids = [
                        r
                        for r in db.exec(
                            select(AgentRun.id).where(AgentRun.session_id == session_id)
                        ).all()
                        if r
                    ]
            return sum(int(store.release_run(rid) or 0) for rid in ids)
        except Exception:  # noqa: BLE001 — bookkeeping must never break a finalize
            log.exception("worklist release failed for session %s", session_id)
            return 0

    def _settle_finished_run(
        self, session: Session, run_id: str, status: SessionStatus
    ) -> tuple[list[str], str | None]:
        """One off-loop read at finalize: release the run's worklist claims,
        then read the ledger ONCE for both the claimed-write check and the
        honest outcome. Returns ``(tools_used, outcome)``; a broken ledger
        read degrades to ``([], None)`` rather than failing the run."""
        from .outcome import (OUTCOME_COMPLETED, OUTCOME_WITH_FAILURES,
                              derive_outcome, session_result)
        from .team import MISSION_ORIGIN

        # An id-less run (a test double) releases every run of the session.
        self._release_worklist_claims(session.id, [run_id] if run_id else None)
        try:
            result = session_result(self.p.engine, session.id)
        except Exception:  # noqa: BLE001 — the card must never break the run
            log.exception("ledger read failed at finalize for %s", session.id)
            return [], None
        tools = [str(t.get("tool") or "") for t in result.get("tools_used") or []]
        outcome = derive_outcome(result, status)
        # v1.351.0 integrate (G09 seam): a MISSION whose worklist kept failed
        # items did not get all of its objective done, whatever the ledger
        # says of the coordinator's own calls. The mission screen read that
        # from the worklist while the bell read only this verdict ("Your
        # objective is done"); one verdict now, from the same summary the
        # screen reads (agents/mission.py). Missions only: a board is keyed on
        # the JOB's place, so another door's rerun would inherit old items.
        if outcome == OUTCOME_COMPLETED and str(getattr(session, "origin", "") or "") == MISSION_ORIGIN:
            store = getattr(self.p, "worklist", None)
            if store is not None:
                try:
                    summary = store.summary(store.root_session_for(session.id))
                    if int(summary.get("failed") or 0) > 0:
                        outcome = OUTCOME_WITH_FAILURES
                except Exception:  # noqa: BLE001 — the verdict stands without it
                    log.debug("mission worklist read failed for %s", session.id, exc_info=True)
        return tools, outcome

    async def settle_child(self, child_session: Session, run: AgentRun) -> bool:
        """Finalize a DELEGATED / SPAWNED child's row on its success path
        (v1.307.0) — the same settle ``run_session`` gives a solo run.

        ``delegate`` and ``spawn_agent`` await ``AgentRuntime.run`` directly
        and used to copy status/tokens/summary onto the row by hand. That copy
        skipped the finalize contract every other path keeps (v1.227.0, A5/A8):
        the child's worklist claims stayed ``doing`` until the 15-minute stale
        window (a team's next worker was told those items were "being worked
        on right now"), no ``outcome`` verdict was derived, the create-time
        folder note was overwritten, and a result claiming a file nothing wrote
        went unchecked. One helper, both doors. Never raises past the save: a
        ledger hiccup costs the verdict, never the delegation.

        A USER STOP IS KEPT (v1.309.0). ``POST /sessions/{child}/cancel`` on a
        running teammate writes CANCELLED to its row (a child is never in
        ``_running``, so there is no task to cancel) and the runtime ends the
        run at its next step boundary (``runtime.RunStoppedByUser``). This
        settle used to overwrite that row COMPLETED/FAILED from the run, so
        the user's Stop read as a lie. Returns True when the child was
        stopped by the user — finalized CANCELLED here, the caller tells the
        coordinator — else False.
        """
        if run.state is AgentState.CANCELLED or await asyncio.to_thread(
            self._user_cancelled, child_session.id
        ):
            child_session.provider, child_session.model = run.provider, run.model
            child_session.input_tokens = run.input_tokens
            child_session.output_tokens = run.output_tokens
            child_session.cost_usd = max(
                float(getattr(child_session, "cost_usd", 0.0) or 0.0),
                float(getattr(run, "cost_usd", 0.0) or 0.0),
            )
            await self._finalize_cancelled(child_session)
            return True
        child_session.status = (
            SessionStatus.COMPLETED
            if run.state is AgentState.COMPLETED
            else SessionStatus.FAILED
        )
        child_session.provider, child_session.model = run.provider, run.model
        # The child's spend lands on ITS row (v1.295.0): the allowance ledger
        # sums Session tokens by roster name, exactly as run_session stamps them.
        child_session.input_tokens = run.input_tokens
        child_session.output_tokens = run.output_tokens
        # v1.300.0: and its DOLLARS — the runtime already added each step to the
        # session object it ran with; the run's figure covers a row loaded
        # separately. The larger of the two, never a sum (no double count).
        child_session.cost_usd = max(
            float(getattr(child_session, "cost_usd", 0.0) or 0.0),
            float(getattr(run, "cost_usd", 0.0) or 0.0),
        )
        prior = child_session.summary
        child_session.summary = _with_folder_note(prior, run.result)
        try:
            from ..daemon.chat_turn import _claimed_write_note, custom_tools_used

            tools_used, outcome = await asyncio.to_thread(
                self._settle_finished_run,
                child_session,
                getattr(run, "id", None),
                child_session.status,
            )
            note = _claimed_write_note(
                run.result or "", tools_used,
                custom_tools=custom_tools_used(self.p, tools_used),
            )
            if note:
                child_session.summary = _with_folder_note(prior, f"{run.result}{note}")
            child_session.outcome = outcome
        except Exception:  # noqa: BLE001 — bookkeeping never fails a delegation
            log.exception("child settle failed for %s", child_session.id)
        child_session.finished_at = utcnow()
        self._save(child_session)
        return False

    def _user_cancelled(self, session_id: str) -> bool:
        """Does the STORED row say the user cancelled ``session_id``?
        BLOCKING (one read); never raises (an unreadable row is not a Stop)."""
        try:
            with session_scope(self.p.engine) as db:
                row = db.get(Session, session_id)
                return row is not None and row.status is SessionStatus.CANCELLED
        except Exception:  # noqa: BLE001
            return False

    def _post_run_learning(self, session: Session) -> None:
        """Post-run learning pipeline: score -> record outcome -> reflect.

        Each step is best-effort and individually guarded so a learning failure
        never cascades into the run. Shared by solo ``run_session`` AND the
        delegate/spawn tools, so multi-agent work teaches the system too. Does
        NOT build a git review — that is git-worktree-only and stays in
        ``run_session``.
        """
        # Phase 9: score the run (never fatal to the session).
        try:
            self.p.evaluator.evaluate(session.id)
        except Exception:  # noqa: BLE001
            log.exception("evaluation failed for session %s", session.id)

        # ImprovementEngine: record the measured outcome + update rolling lesson /
        # agent stats so scores actually feed back into weighting. Runs on EVERY
        # completion BEFORE reflection (so this run's own new lesson isn't
        # mis-attributed). Cheap, pure-DB, and internally never-raising.
        improvement = getattr(self.p, "improvement", None)
        if improvement is not None:
            try:
                improvement.record_outcome(session.id)
            except Exception:  # noqa: BLE001
                log.exception("outcome recording failed for session %s", session.id)

        # Self-correction: reflect on what happened into a durable lesson.
        try:
            self.p.learning.reflect(
                session.id,
                task=session.task,
                summary=session.summary,
                ok=session.status is SessionStatus.COMPLETED,
            )
        except Exception:  # noqa: BLE001
            log.exception("reflection failed for session %s", session.id)

        # Skill learning (v1.135.0): derive skill uses + mint suggest-only
        # create/refine candidates from this finished run. Runs AFTER
        # record_outcome (it reads the OutcomeRecord score, never re-scores).
        # Pure-DB, deterministic, internally never-raising — the guard here is
        # belt-and-braces like the steps above.
        skill_learning = getattr(self.p, "skill_learning", None)
        if skill_learning is not None:
            try:
                skill_learning.observe_session(session)
            except Exception:  # noqa: BLE001
                log.exception("skill-learning observe failed for session %s", session.id)

        # History search (v1.142.0): index this finished run's task + summary +
        # result so it is findable the moment it lands, instead of waiting for
        # the periodic backfill. Owns its own transaction (no db= — the session
        # row was already committed by ``_save``), upserts by session id, and is
        # internally never-raising; the guard matches the steps above.
        try:
            from ..core.db import search_index

            index = search_index(self.p.engine)
            if index is not None:
                index.sync_session(session)
        except Exception:  # noqa: BLE001
            log.exception("history-search sync failed for session %s", session.id)

    async def _post_run_allowance(self, session: Session) -> None:
        """The allowance tail of a CUSTOM agent's run (v1.295.0, the job card).

        Sits beside ``_post_run_learning`` at every site that calls it (solo
        ``run_session``, ``delegate``, ``spawn_agent``) and is AWAITED rather
        than folded into that sync helper: the step publishes ``agent.paused``
        / ``agent.allowance_warning`` through the async bus, and a
        fire-and-forget task would be cancelled by the scheduler thread's
        ``asyncio.run`` teardown before the ledger row landed. Guarded like the
        learning steps — never raises, logged. A builtin's run returns at once.
        """
        try:
            name = str(getattr(session, "agent_name", "") or "").strip()
            if not name.lower().startswith("custom:"):
                return
            registry = getattr(self.p, "agents_registry", None)
            if registry is None:
                return
            from . import allowance

            await allowance.after_run(
                registry, self.p.engine, self.p.event_bus, name
            )
        except Exception:  # noqa: BLE001
            log.exception("allowance tail failed for session %s", session.id)

    async def _post_run_assignment(self, session: Session) -> None:
        """The ASSIGNMENT tail of a run (v1.296.0): a session whose origin is
        ``assignment:<id>`` settles its assignment — ``done`` on COMPLETED,
        ``failed`` (the 3-strike breaker counts it) on FAILED, ``cancelled``
        (no strike) on CANCELLED — and
        publishes ``assignment.finished``. Called at EVERY finalize site
        (the solo success path, ``_finalize_failed``, ``_finalize_cancelled``)
        so no outcome leaves a job stuck in ``running``; the dispatcher's
        sweep is the backstop for a row this hook never saw (a reconcile).
        Guarded like the learning steps — never raises, logged; a run that is
        not an assignment returns at once. The store write leaves the loop.
        """
        try:
            origin = str(getattr(session, "origin", "") or "")
            if not origin.startswith("assignment:"):
                return
            store = getattr(self.p, "assignments", None)
            if store is None:
                return
            assignment_id = origin[len("assignment:"):]
            ok = session.status is SessionStatus.COMPLETED
            error = ""
            if session.status is SessionStatus.CANCELLED:
                # A stop is not a failure (reviewer, v1.296.0): the row ends
                # ``cancelled`` (Retry stays offered) and the breaker does not
                # count it — whichever of the cancel route's flip and this
                # hook lands first, the row reads the same.
                row = await asyncio.to_thread(store.cancel, assignment_id)
            else:
                if not ok:
                    error = _prose(session.summary) or str(session.status.value)
                row = await asyncio.to_thread(store.finish, assignment_id, ok, error)
            if row is None:
                return
            await self.p.event_bus.publish(
                EventType.ASSIGNMENT_FINISHED,
                {
                    "id": assignment_id,
                    "assignee": row.assignee,
                    "title": row.title,
                    "ok": ok,
                    "session_id": session.id,
                    "error": row.last_error or error,
                    "status": row.status,
                    # v1.351.0 (G01): the ledger's verdict on the JOB. ``ok``
                    # says the run completed; a run that completed while an
                    # ask expired (``needs_you``) or a step failed
                    # (``completed_with_failures``) is not done, and a
                    # reader (the bell) must be able to tell.
                    "outcome": getattr(session, "outcome", None),
                },
                session_id=session.id,
            )
            disp = getattr(self.p, "assignment_dispatcher", None)
            if disp is not None:
                disp.wake()  # the agent is free — its next job starts now
        except Exception:  # noqa: BLE001
            log.exception("assignment tail failed for session %s", session.id)

    def _deliver_to_chat_sync(self, session: Session) -> dict[str, Any]:
        """The reply comes home (v1.345.0, lane T): append this session's ONE
        line to the chat it was asked in (``options.chat_thread_id``) through
        the daemon's one writer. A session no chat waits on returns at once
        with nothing read or written. BLOCKING — the async twin hops it off
        the loop; the boot reconcile calls it directly. Never raises."""
        try:
            if not _chat_delivery.chat_thread_of(session):
                return {"ok": False, "why": "no chat", "appended": 0, "skipped": 0}
            # The provider manager + config let the delivered card judge
            # where the run's model ran (v1.352.0, G21) exactly as GET
            # /sessions/{id}/result does — without them the flag is dropped.
            out = _chat_delivery.deliver_session_line(
                self.p.engine,
                session,
                event_bus=getattr(self.p, "event_bus", None),
                manager=getattr(self.p, "providers", None),
                config=getattr(self.p, "config", None),
            )
            if not out.get("ok"):
                log.warning(
                    "session %s could not report to its chat: %s", session.id, out.get("why")
                )
            return out
        except Exception:  # noqa: BLE001 — a delivery miss must never break a finalize
            log.exception("chat delivery failed for session %s", session.id)
            return {"ok": False, "why": "delivery failed", "appended": 0, "skipped": 0}

    async def _deliver_to_chat(self, session: Session) -> dict[str, Any]:
        """``_deliver_to_chat_sync`` off the loop (the ledger read + the
        append are SQLite work; the finalizers already run theirs there).
        A session carrying no chat never leaves the loop."""
        if not _chat_delivery.chat_thread_of(session):
            return {"ok": False, "why": "no chat", "appended": 0, "skipped": 0}
        try:
            return await asyncio.to_thread(self._deliver_to_chat_sync, session)
        except Exception:  # noqa: BLE001 — never block teardown on the chat
            log.exception("chat delivery failed for session %s", session.id)
            return {"ok": False, "why": "delivery failed", "appended": 0, "skipped": 0}

    def _reply_home(self, session: Session, *, from_boot: bool = False) -> None:
        """A TASK ANOTHER COMPUTER SENT gets its answer sent home (v1.357.0,
        G61; ``agents/task_door``). Every terminal path calls this beside the
        chat delivery: a run whose row carries a reply address (only the task
        door writes one) starts ONE bounded, masked reply to the computer that
        asked — on its own tracked task, so this run's teardown never waits on
        another machine (and the boot reconcile, which has no loop yet, on a
        daemon thread). A run with no reply address starts nothing. A failed
        reply is recorded on the row and the door, named — never retried.
        Never raises."""
        try:
            from . import task_door as _task_door

            if _task_door.reply_spec_of(session) is None:
                return
            if from_boot:
                _task_door.send_reply_in_thread(self.p, session)
            else:
                _task_door.send_reply_soon(self.p, session)
        except Exception:  # noqa: BLE001 — a reply miss must never break a finalize
            log.exception("could not start the reply for session %s", session.id)

    def _plain_failure(self, error: Exception, provider: str = "") -> tuple[str, str]:
        """``(reason, technical)`` for a failed run (v1.354.0, G28; readiness
        12A): ``providers.failure_words.run_failure_words`` — one plain
        sentence for the summary (a recognised failure's headline and fix, a
        failure already worded for a person, else WHAT failed and what to do
        next — never the raw text), and the failure's own masked text for the
        run page's folded Details. ``provider`` is the run's provider (it
        names a provider's failure that names none itself). Never raises,
        never an empty reason."""
        try:
            from ..providers.failure_words import run_failure_words

            return run_failure_words(
                error, manager=getattr(self.p, "providers", None), provider=provider
            )
        except Exception:  # noqa: BLE001 - wording only
            return "it stopped with an error", ""

    async def _finalize_failed(self, session: Session, error: Exception) -> None:
        """Mark a crashed run FAILED, persist, emit SESSION_COMPLETED(ok=False), GC
        its worktree — so an unexpected exception never leaves a zombie ACTIVE
        session the app can't see or recover.

        A row the USER already cancelled stays CANCELLED (v1.309.0): a stopped
        teammate whose run then raised (its provider call torn down, say) must
        not be re-recorded as a crash over the user's Stop."""
        try:
            stopped = await asyncio.to_thread(self._user_cancelled, session.id)
        except Exception:  # noqa: BLE001 — a failed read is not a Stop
            stopped = False
        if stopped:
            await self._finalize_cancelled(session)
            return
        session.status = SessionStatus.FAILED
        # v1.354.0 (G28): the summary a person reads says what happened in
        # plain words ("The run failed: OpenRouter refused your key. Update it
        # in ..."), never the exception's class; the class goes to the log
        # and to the run rows' own record (`error_text`, below).
        error_text = f"{type(error).__name__}: {error}"
        log.warning("session %s failed: %s", session.id, error_text[:500])
        # Readiness 12A (G28): the summary never carries the raw text; the
        # failure's own (masked) words ride the row apart, for the run page's
        # folded Details — the chat's title/fix/technical split, for a run.
        reason, technical = self._plain_failure(error, str(getattr(session, "provider", "") or ""))
        session.summary = _with_folder_note(
            session.summary,
            _prose(session.summary) or f"The run failed: {reason}",
        )
        session.failure_detail = technical
        session.finished_at = utcnow()

        def _settle_failed() -> tuple[list[str], str | None]:
            # v1.288.0 (agents-04): settle the session's AgentRun rows with it,
            # as the cancel finalizer and the boot reconcile already do — a
            # provider crash escapes AgentRuntime.run with its row RUNNING, and
            # nothing else ever revisits a FAILED session, so the team tree
            # showed a live agent inside a failed job forever.
            try:
                with session_scope(self.p.engine) as db:
                    for r in db.exec(
                        select(AgentRun).where(AgentRun.session_id == session.id)
                    ):
                        if r.state not in (
                            AgentState.COMPLETED,
                            AgentState.FAILED,
                            AgentState.CANCELLED,
                        ):
                            r.state = AgentState.FAILED
                            r.finished_at = utcnow()
                            if not r.result:
                                r.result = error_text
                            db.add(r)
                    db.commit()
            except Exception:  # noqa: BLE001 - never block teardown on bookkeeping
                log.exception("failed to settle the run rows of %s", session.id)
            return self._settle_finished_run(session, "", session.status)

        # v1.227.1 order: let a pause's shielded WAITING -> RUNNING restore
        # land BEFORE the settle, or it could resurrect RUNNING afterwards.
        drain = getattr(getattr(self, "runtime", None), "drain_inflight_state", None)
        if drain is not None:
            try:
                await asyncio.shield(drain())
            except asyncio.CancelledError:
                pass  # a cancel mid-teardown: the restore finishes on its own task
        try:
            # v1.227.0: a crashed run hands its worklist claims back (A8) and
            # still gets an honest verdict — ``needs_you`` when an ask timed
            # out before the crash. Off the loop; never fatal to the teardown.
            _tools, outcome = await asyncio.to_thread(_settle_failed)
            session.outcome = outcome
        except Exception:  # noqa: BLE001 - never block teardown on bookkeeping
            log.exception("failed to settle the ledger for %s", session.id)
        try:
            self._save(session)
        except Exception:  # noqa: BLE001 - never block teardown on persistence
            log.exception("failed to persist FAILED state for %s", session.id)
        # v1.345.0 (lane T): the failure sentence lands in the chat it was
        # asked in, before the event the page re-reads on.
        await self._deliver_to_chat(session)
        # v1.357.0 (G61): the computer that sent this task hears it failed.
        self._reply_home(session)
        try:
            await self.p.event_bus.publish(
                EventType.SESSION_COMPLETED,
                _completion_payload(session, ok=False),
                session_id=session.id,
            )
        except Exception:  # noqa: BLE001 - never block teardown on the event bus
            log.exception("failed to publish failure event for %s", session.id)
        await self._post_run_assignment(session)
        # FX-01: release any live SSE reader with a terminal frame -- a crash can
        # abort the run before its sink emits ``done``, otherwise leaving the
        # browser stream hanging. Sync + non-blocking; a no-op with no subscriber.
        hub = getattr(self.p, "streams", None)
        if hub is not None:
            hub.publish(
                session.id,
                {"event": "done", "data": {"ok": False, "reply": session.summary or ""}},
            )
        gs = self._git_sessions.pop(session.id, None)
        if gs is not None:
            try:
                gs.discard()
            except Exception:  # noqa: BLE001
                log.exception("worktree cleanup failed after failing %s", session.id)

    async def _finalize_cancelled(
        self, session: Session, *, reason: str | None = None
    ) -> None:
        """Mark a cancelled run CANCELLED, persist, notify, and GC its worktree.

        ``reason`` (v1.288.0) replaces the default words when the caller KNOWS
        the stop was not the user — a sub-agent stopped by a tool time limit
        must not be recorded as "cancelled by the user"."""
        session.status = SessionStatus.CANCELLED
        session.summary = _with_folder_note(
            session.summary,
            reason
            # v1.351.0 (G13): a stop Emergency stop made says so.
            or self._stop_reasons.pop(session.id, None)
            or _prose(session.summary)
            or STOPPED_SUMMARY,
        )
        session.finished_at = utcnow()

        def _persist_cancel() -> None:
            # v1.226.0: both writes off the loop — a cancel that lands while a
            # long writer holds the file must not park the daemon.
            self._save(session)
            # Settle any in-flight AgentRun rows so they don't linger in RUNNING.
            run_ids: list[str] = []
            with session_scope(self.p.engine) as db:
                for r in db.exec(select(AgentRun).where(AgentRun.session_id == session.id)):
                    run_ids.append(r.id)
                    if r.state not in (
                        AgentState.COMPLETED,
                        AgentState.FAILED,
                        AgentState.CANCELLED,
                    ):
                        r.state = AgentState.CANCELLED
                        r.finished_at = utcnow()
                        db.add(r)
                db.commit()
            # v1.227.0 (A8/RT4): a cancelled run's worklist claims go back on
            # the board NOW — a rerun used to wait the 15-minute stale window
            # for items "held" by the run the user had just stopped.
            self._release_worklist_claims(session.id, run_ids)

        # v1.227.1: let a pause's shielded WAITING -> RUNNING restore land
        # BEFORE the settle below, or it could land after and resurrect
        # RUNNING on a cancelled run (the RT4 race shape, seen on CI).
        drain = getattr(getattr(self, "runtime", None), "drain_inflight_state", None)
        if drain is not None:
            try:
                await asyncio.shield(drain())
            except asyncio.CancelledError:
                pass  # a second cancel: the restore finishes on its own task
        await asyncio.to_thread(_persist_cancel)
        # v1.345.0 (lane T): the stopped sentence lands in the chat it was
        # asked in, before the event the page re-reads on.
        await self._deliver_to_chat(session)
        # v1.357.0 (G61): the computer that sent this task hears it stopped.
        self._reply_home(session)
        try:
            await self.p.event_bus.publish(
                EventType.SESSION_COMPLETED,
                _completion_payload(session),
                session_id=session.id,
            )
        except Exception:  # noqa: BLE001 - never block teardown on the event bus
            log.exception("failed to publish cancel event for %s", session.id)
        await self._post_run_assignment(session)
        # FX-01: terminal frame so a cancel doesn't leave SSE readers hanging.
        hub = getattr(self.p, "streams", None)
        if hub is not None:
            hub.publish(
                session.id,
                {"event": "done", "data": {"ok": False, "reply": session.summary or ""}},
            )
        gs = self._git_sessions.pop(session.id, None)
        if gs is not None:
            try:
                gs.discard()
            except Exception:  # noqa: BLE001
                log.exception("worktree cleanup failed after cancelling %s", session.id)

    def cancel_session(self, session_id: str) -> Session:
        """Stop a running session. Raises KeyError if unknown, ValueError if
        already finished. Cancelling an in-flight background run unwinds it to
        CANCELLED via run_session's handler; a QUEUED (parked, never-started)
        run is removed from the queue and finalized directly; a session with no
        live task (e.g. a synchronous run that already settled) is marked
        CANCELLED directly."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session '{session_id}'")
        if session.status in (
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ):
            raise ValueError(f"session '{session_id}' is already {session.status.value}")
        task = self._running.get(session_id)
        if task is not None and not task.done():
            loop = task.get_loop()
            try:
                here = asyncio.get_running_loop()
            except RuntimeError:
                here = None
            if loop is here:
                task.cancel()  # -> CancelledError in run_session -> _finalize_cancelled
            else:
                # v1.231.0 (audit AE2): a schedule-fired session runs under
                # asyncio.run on the APScheduler thread, and this call arrives
                # from the daemon's loop (or FastAPI's threadpool — the cancel
                # route is sync). Task.cancel() from a foreign thread is not
                # thread-safe and does not wake the other loop's selector, so
                # the cancel landed only when the model call returned on its
                # own. Hand it to the task's OWN loop, and stamp the row now:
                # the response is the only place the request shows until the
                # run unwinds and _finalize_cancelled re-saves the same words.
                # STAMP FIRST, cancel second (v1.374.1): the finalizer POPS the
                # stop reason, so a cancel handed over before this read let the
                # run's loop finalize in between, and the stamp then saved the
                # plain words over Emergency stop's. Stamped first, the
                # finalizer's write is always the last one.
                session.status = SessionStatus.CANCELLED
                session.summary = _with_folder_note(
                    session.summary,
                    self._stop_reasons.get(session_id)
                    or _prose(session.summary)
                    or STOPPED_SUMMARY,
                )
                session.finished_at = utcnow()
                self._save(session)
                loop.call_soon_threadsafe(task.cancel)
        elif self._remove_queued(session_id):
            # Parked, never started (v1.166.0): the un-started coroutine was
            # closed by _remove_queued; finalize honestly — no agent ever ran.
            session.status = SessionStatus.CANCELLED
            session.summary = _with_folder_note(
                session.summary,
                self._stop_reasons.pop(session_id, None)
                or _prose(session.summary)
                or STOPPED_BEFORE_START,
            )
            session.finished_at = utcnow()
            self._save(session)
            # v1.357.0 (G61, review): a task another computer sent that a cap
            # parked is still owed its answer (none of the finalizers runs for
            # a run that never started). Never raises; one attempt per run.
            self._reply_home(session)
        else:
            # No task in ``_running``. A DELEGATED TEAMMATE lands here while
            # it is running (v1.309.0): ``delegate``/``spawn_agent`` keep it
            # out of ``_running`` on purpose (agents/team.py), so this branch
            # used to write CANCELLED and nothing else — the teammate kept
            # working, then ``settle_child`` overwrote the row COMPLETED. Now:
            # the row first (the delegating door reads it to tell "the user
            # stopped my teammate" from "I was cancelled"), then the child's
            # OWN task is cancelled through ``team.stop_child`` — on its loop,
            # mid-model-call if need be, so no further model call is made.
            # The runtime's step-boundary row check is the belt for a child
            # with no bound task, and ``settle_child`` keeps the CANCELLED.
            session.status = SessionStatus.CANCELLED
            session.summary = _with_folder_note(
                session.summary,
                self._stop_reasons.pop(session_id, None)
                or _prose(session.summary)
                or STOPPED_SUMMARY,
            )
            session.finished_at = utcnow()
            self._save(session)
            from . import team as _team

            _team.stop_child(session_id)
            # A teammate parked on an ask waits with no clock (an attended
            # origin): decline its open asks too, so nothing stays parked.
            approvals = getattr(self.p, "approvals", None)
            if approvals is not None:
                try:
                    for ask in approvals.pending_for(session_id):
                        approvals.resolve(ask["approval_id"], "deny")
                except Exception:  # noqa: BLE001 — the row write already stopped it
                    log.exception("could not decline the open asks of %s", session_id)
        return self.get_session(session_id) or session

    async def rerun_session(self, session_id: str) -> Session:
        """Clone a session's inputs (task/agent/provider/model/grants/folder)
        into a fresh run.

        A MAINTAINER (self-dev) session is re-run as self-dev so it still lands on
        an Iron Jarvis worktree (and fails closed if self-dev is now disabled).
        A session that ran DIRECTLY in a user folder (a project's in-folder task)
        re-runs THERE — cloning only task/model used to dump the rerun's
        deliverable into a throwaway scratch workspace — and its bundle-approved
        tool grant carries over so the pre-approved tools don't fail closed."""
        prev = self.get_session(session_id)
        if prev is None:
            raise KeyError(f"unknown session '{session_id}'")
        allow_tools = _stored_allow_tools(prev)
        options = _stored_options(prev)
        # v1.357.0 (G61, review): the answer address of a task another
        # computer sent belongs to THAT run — its key is single-use and spent
        # by the first answer — so a rerun never inherits it (it could only
        # record a failed answer the user did not cause).
        options.pop(_REMOTE_REPLY_OPTION, None)
        return await self.create_session(
            prev.task,
            prev.agent_type,
            provider=prev.provider,
            model=prev.model,
            self_dev=prev.agent_type is AgentType.MAINTAINER,
            project_id=prev.project_id,  # a rerun stays in its project (spine)
            allow_tools=allow_tools or None,
            workspace_root=self._rerun_direct_root(prev),
            # …and so does its ORIGIN. Provenance is not decoration: a rerun of a
            # chat escalation / Agents-page job / Projects task is still being
            # WATCHED by the same human, and ``runtime._pause_for_approval`` will
            # only pause on an ask-tier tool for an origin that asserts presence.
            # Dropping it made the rerun unattributed, so the ask it would have
            # raised became an instant headless denial.
            origin=getattr(prev, "origin", None),
            # The step budget is part of the session's INPUTS (v1.174.0): a big
            # job re-run on the default 12 steps would fail exactly the way the
            # raised budget was set to prevent, and the user never touched a
            # control to lose it.
            max_steps=getattr(prev, "max_steps", None),
            # …and the POSTURE (v1.232.0, A7) — the same inputs, the same
            # rule about which calls pause.
            approval_mode=getattr(prev, "approval_mode", "") or None,
            # …and the TRUST posture (v1.298.0): a rerun of a low-trust
            # session is the same inputs from the same door.
            trust=getattr(prev, "trust", "") or None,
            trust_reason=getattr(prev, "trust_reason", "") or "",
            # …and the RUN OPTIONS (v1.299.0): skip_memory / folder_rules are
            # inputs too — a rerun that re-grew the memory blocks or dropped
            # the folder's rules would not be the same run.
            options=options or None,
        )

    def _rerun_direct_root(self, prev: Session) -> str | None:
        """The folder a rerun should run DIRECTLY in, or None for a fresh
        scratch workspace.

        Honest signal (see ``is_direct_workspace``): a ``workspace_path``
        outside the managed ``workspaces_dir`` can ONLY mean the session was
        created with ``workspace_root=...``. When the session's project has
        since moved its folder, the project's CURRENT root wins so the rerun
        lands where the project lives now, not in the stale location."""
        if not is_direct_workspace(self.p.config, prev.workspace_path):
            return None
        if prev.project_id:
            with session_scope(self.p.engine) as db:
                project = db.get(Project, prev.project_id)
            current = (project.root or "").strip() if project is not None else ""
            if current:
                return current
        return prev.workspace_path

    async def continue_session(
        self,
        session_id: str,
        message: str,
        allow_tools: list[str] | None = None,
        approval_mode: str | None = None,
        resume: bool = False,
        prior_tool_results: list | None = None,
        chat_thread_id: str = "",
    ) -> Session:
        """Start a follow-up run that reuses the finished session's workspace and
        a compact recap of the prior task/result, enabling multi-turn work.

        ``chat_thread_id`` (v1.345.0, lane T): the chat THIS follow-up was
        asked in — its own, never the parent's (the parent's line already
        landed in the parent's chat; a follow-up asked from nowhere must not
        append to it).

        ``prior_tool_results`` (v1.344.0, lane P): the escalating chat turn's
        tool outcomes for THIS follow-up (the same shape ``create_session``
        takes). The parent's list is never inherited — it described the
        parent's turn, and the runtime would show it again as this run's.

        ``resume`` (v1.337.0): this is Continue on a job a RESTART cut off.
        When the cut-off run kept its conversation (``agents/transcript``),
        its id rides the new row as ``options.resume_run`` and the runtime
        starts from that conversation instead of the recap alone.

        A continuation is the SAME piece of work, so it inherits the same inputs
        a rerun does — including the user's up-front tool GRANT and the ORIGIN
        that says a human is watching. See the comments on the Session below:
        both used to drop here, which quietly fail-closed every turn after the
        first of an escalated chat.

        ``allow_tools`` (v1.232.0, audit A6) is UNIONED with the stored grant:
        the chat page sends its armed set on every turn now, and a grant the
        runtime persisted at resolve time ("Allow for this run") is already in
        the stored list — so a continue inherits both, and can widen but never
        narrow what run 1 was allowed. ``approval_mode`` (A7): a stated
        posture replaces the parent's; none = inherit it.
        """
        import json as _json

        prev = self.get_session(session_id)
        if prev is None:
            raise KeyError(f"unknown session '{session_id}'")
        grants = _stored_allow_tools(prev)
        grants += [str(t) for t in (allow_tools or []) if t and str(t) not in grants]
        recap = (
            f"{message}\n\n[Continuing an earlier session. Original task: "
            f"{prev.task!r}. Prior result: {prev.summary or '(none)'} "
            f"The earlier workspace files are available in your workspace.]"
        )
        options = _stored_options(prev)
        # v1.337.0: a resume carries the conversation of the run it resumes;
        # any other continuation never does (an inherited one is dropped).
        options.pop("resume_run", None)
        if resume:
            _from = self._resumable_run(prev.id)
            if _from:
                options["resume_run"] = _from
        # v1.344.0 (lane P): what the chat ran belongs to ONE turn; a
        # follow-up carries its own list or none, never the parent's.
        options.pop("prior_tool_results", None)
        _prior_clean = normalize_prior_tool_results(prior_tool_results)
        if _prior_clean:
            options["prior_tool_results"] = _prior_clean
        # v1.345.0 (lane T): the chat a run reports to belongs to ONE ask; a
        # follow-up carries its own chat or none, never the parent's.
        options.pop(_chat_delivery.CHAT_THREAD_OPTION, None)
        _chat_id = _chat_delivery.clean_chat_thread_id(chat_thread_id)
        if _chat_id:
            options[_chat_delivery.CHAT_THREAD_OPTION] = _chat_id
        # v1.357.0 (G61, review): likewise the answer address of a task
        # another computer sent — its key is single-use and spent by that
        # task's own answer — so a follow-up never inherits it.
        options.pop(_REMOTE_REPLY_OPTION, None)
        if getattr(prev, "origin", None) == MISSION_ORIGIN:
            # A MISSION'S FOLLOW-UP KEEPS THE USER'S OWN WORDS (v1.309.0). The
            # recap above is MODEL-facing (it must carry the earlier task and
            # result), and the mission screen used to show it verbatim as the
            # objective — "[Continuing an earlier session. Original task: …]"
            # where the user had typed one sentence. ``objective`` is the
            # display text; ``task`` stays what the model reads. Only a
            # mission's continuation is stamped: no other surface reads it.
            options = {**options, "objective": message}
        session = Session(
            task=recap,
            agent_type=prev.agent_type,
            provider=prev.provider,
            model=prev.model,
            status=SessionStatus.ACTIVE,
            project_id=prev.project_id,  # a chat stays in its project
            # …and so does its step budget (v1.174.0): a follow-up turn on a big
            # job is the SAME job, so silently dropping back to the configured
            # default would strand the continuation the user just asked for.
            max_steps=normalize_max_steps(getattr(prev, "max_steps", None)),
            # …and so does the GRANT. The tools the user bundle-approved for run 1
            # are part of the session's inputs (``rerun_session`` says the same):
            # starting the follow-up with an empty ``session_allow`` makes the very
            # command that ran minutes ago in this workspace fail closed.
            allow_tools_json=_json.dumps(grants),
            # …and so does the POSTURE (v1.232.0, A7), unless this turn
            # states one. Normalised at this door too: yolo never lands.
            approval_mode=(
                inherited_approval_mode(approval_mode)
                or getattr(prev, "approval_mode", "")
                or ""
            ),
            # …and so does the ORIGIN — the PARENT's, never "continuation".
            # ``runtime._pause_for_approval`` pauses only for an origin that
            # asserts a watching human (chat/job/project/user); the continuation
            # is being watched by exactly the person who watched run 1, so their
            # presence carries. Stamping the literal "continuation" here (a value
            # ``core.models`` lists) would be WORSE than leaving it None: it is
            # not in that allowlist, so it could not restore the pause, and the
            # dashboard chat page's agent lane posts /continue for every turn
            # after the first — turning an honest instant denial into a silent
            # 300s pause that ends in timeout-deny.
            origin=getattr(prev, "origin", None),
            # …and the TRUST posture (v1.298.0) — a follow-up turn on a run
            # that started from the phone, or read flagged content, is the
            # same run as far as what it may change.
            trust=_normalize_trust(getattr(prev, "trust", "")),
            trust_reason=(
                " ".join(str(getattr(prev, "trust_reason", "") or "").split())
                if _normalize_trust(getattr(prev, "trust", "")) == _TRUST_LOW
                else ""
            ),
            # …and the RUN OPTIONS (v1.299.0), the same inputs as a rerun's
            # (plus a mission's ``objective``, v1.309.0 — see above).
            options_json=_json.dumps(options, default=str),
        )
        # Reuse the prior workspace so the follow-up sees the earlier files — but
        # ONLY for non-git sessions. A git worktree can be discarded by the
        # parent's review/reject, which would yank the follow-up's files out from
        # under it, so a git-backed parent's continuation gets a fresh workspace
        # (the recap still carries the context).
        reuse_ws = bool(prev.workspace_path) and self._git_sessions.get(prev.id) is None
        # Serialize the busy-check + save when REUSING a workspace, so two
        # simultaneous continuations of the same parent can't both pass the check.
        async with self._continue_lock:
            if reuse_ws:
                ws = prev.workspace_path
                with session_scope(self.p.engine) as db:
                    # QUEUED counts as busy (v1.166.0): a continuation parked by
                    # the concurrency governor still owns this workspace — letting
                    # a second continue through creates two sessions sharing one
                    # workspace, the exact race _continue_lock defends against.
                    busy = db.exec(
                        select(Session).where(
                            Session.workspace_path == ws,
                            Session.status.in_(  # type: ignore[attr-defined]
                                (SessionStatus.ACTIVE, SessionStatus.QUEUED)
                            ),
                        )
                    ).first()
                if busy is not None:
                    raise ValueError(
                        "A follow-up is already running or queued in this folder. "
                        "Wait for it to finish before continuing again."
                    )
            else:
                ws = str(self.p.config.workspaces_dir / session.id)
            Path(ws).mkdir(parents=True, exist_ok=True)
            session.workspace_path = ws
            self._save(session)
        await self.p.event_bus.publish(
            EventType.SESSION_CREATED,
            {"task": message, "agent": session.agent_type.value, "workspace": ws},
            session_id=session.id,
        )
        return session

    def _resumable_run(self, session_id: str) -> str:
        """The newest top-level run of ``session_id`` that kept its
        conversation (v1.337.0) — or, for a planned-steps run, its plan
        (v1.339.0) — or "". One indexed read."""
        from .transcript import has_plan, has_transcript

        with session_scope(self.p.engine) as db:
            runs = list(
                db.exec(
                    select(AgentRun)
                    .where(AgentRun.session_id == session_id, AgentRun.parent_id.is_(None))  # type: ignore[union-attr]
                    .order_by(AgentRun.created_at.desc())  # type: ignore[union-attr]
                ).all()
            )
        for r in runs:
            if has_transcript(self.p.engine, r.id) or has_plan(self.p.engine, r.id):
                return r.id
        return ""

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its runs/tool rows; GC any worktree. Refuses a
        session that is still actively running (cancel it first)."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session '{session_id}'")
        task = self._running.get(session_id)
        if task is not None and not task.done():
            raise ValueError("session is still running; cancel it before deleting")
        # A parked (QUEUED, never-started) run goes with its session — leaving
        # the entry behind would start a deleted session's coroutine later.
        self._remove_queued(session_id)
        ws_path = session.workspace_path
        gs = self._git_sessions.pop(session_id, None)
        if gs is not None:
            try:
                gs.discard()  # removes the worktree dir + branch
            except Exception:  # noqa: BLE001
                log.exception("worktree cleanup failed while deleting %s", session_id)
        self._reviews.pop(session_id, None)
        with session_scope(self.p.engine) as db:
            # History search (v1.142.0): the session's own doc, and any doc
            # pointing at one of its runs, go with it — in THIS transaction.
            # Both delete routes (`DELETE /sessions/{id}` and the Kanban bulk
            # `POST /sessions/clear`) funnel through here, so wiring it at this
            # one point covers both; a doc left behind is a search result that
            # opens onto a session the app no longer has.
            #
            # It runs FIRST, before a single row is marked for deletion, and that
            # ORDER IS LOAD-BEARING: ``SearchIndex`` holds its own lock across
            # its statements, so a transaction that already owns SQLite's single
            # writer and THEN waits on that lock inverts against a thread holding
            # the lock and waiting on the writer — a real deadlock that resolves
            # only when ``busy_timeout`` (30s) fires, whereupon the failed flush
            # leaves this session unusable and ``db.commit()`` below raises
            # ``PendingRollbackError``. Measured at 66s and two LOST writes before
            # the seams were reordered. Take the index lock while still a reader.
            run_ids: list[str] = [
                r for r in db.exec(
                    select(AgentRun.id).where(AgentRun.session_id == session_id)
                ).all()
                if r
            ]
            try:
                from ..core.db import search_index

                index = search_index(self.p.engine)
                if index is not None:
                    # One bulk, chunked statement — never a query per id.
                    index.drop_refs([session_id, *run_ids], db=db)
            except Exception:  # noqa: BLE001 — a delete must always complete
                log.warning("history-search drop failed for session %s", session_id,
                            exc_info=True)
            obj = db.get(Session, session_id)
            if obj is not None:
                db.delete(obj)
            for r in db.exec(select(AgentRun).where(AgentRun.session_id == session_id)):
                db.delete(r)
            for t in db.exec(
                select(ToolInvocation).where(ToolInvocation.session_id == session_id)
            ):
                db.delete(t)
            # Cascade the other per-session tables so no rows are orphaned.
            for model_path, attr in (
                ("..core.models.EventRecord", "session_id"),
                ("..eval.models.Evaluation", "session_id"),
                ("..artifacts.models.ArtifactRecord", "session_id"),
                # Department blackboard rows are keyed by the root session id.
                ("..blackboard.models.BlackboardRecord", "board_id"),
                # The pending review row: an orphan can rehydrate as an approvable
                # review and merge a deleted session's branch (wrong behavior).
                ("..core.models.PendingReviewRecord", "session_id"),
                # Improvement/learning rows (harmless bloat, but keep it tidy).
                ("..improvement.models.OutcomeRecord", "session_id"),
                ("..learning.models.FeedbackRecord", "session_id"),
            ):
                try:
                    mod_name, cls_name = model_path.rsplit(".", 1)
                    import importlib

                    cls = getattr(importlib.import_module(mod_name, __package__), cls_name)
                    if hasattr(cls, attr):
                        for row in db.exec(select(cls).where(getattr(cls, attr) == session_id)):
                            db.delete(row)
                except Exception:  # noqa: BLE001 - best-effort; never block the delete
                    pass
            db.commit()
        # Remove a plain (non-git) workspace dir, unless another session (e.g. a
        # continuation) still reuses it. Git worktrees were already discarded above.
        #
        # ONLY a workspace THIS APP CREATED is ever deleted (``is_managed_workspace``).
        # A session created with ``workspace_root=`` — every Projects in-folder task,
        # every chat escalation carrying its folder, any ``POST /sessions`` with a
        # workspace_root — stores the USER'S REAL FOLDER as ``workspace_path``, and the
        # "is it shared?" query below cannot protect it: this session's own row was
        # already deleted in the transaction above, so the LAST session pointing at the
        # folder finds no sharer and the folder gets rmtree'd. Bulk "clear completed"
        # (``POST /sessions/clear``) walks every finished session through here one by
        # one, which guarantees that last delete eventually happens. Cleaning up after
        # ourselves must never mean deleting the user's documents.
        if gs is None and is_managed_workspace(self.p.config, ws_path):
            try:
                with session_scope(self.p.engine) as db:
                    shared = db.exec(
                        select(Session).where(Session.workspace_path == ws_path)
                    ).first()
                if shared is None:
                    import shutil

                    p = Path(ws_path)
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
            except Exception:  # noqa: BLE001
                log.exception("workspace cleanup failed while deleting %s", session_id)

    async def run(
        self,
        task: str,
        agent_type: AgentType = AgentType.BUILDER,
        provider: str | None = None,
    ) -> Session:
        session = await self.create_session(task, agent_type, provider)
        return await self.run_session(session.id)

    # --- queries (used by the daemon API) ---------------------------------

    def get_session(self, session_id: str) -> Session | None:
        with session_scope(self.p.engine) as db:
            return db.get(Session, session_id)

    def list_sessions(self, limit: int | None = 200) -> list[Session]:
        # Bounded by default: this feeds the dashboard's 4s-polled /sessions list, so
        # an unbounded SELECT would load + serialize every session ever, growing
        # without limit over weeks. 200 most-recent is the UI window; pass limit=None
        # for the full set.
        with session_scope(self.p.engine) as db:
            stmt = select(Session).order_by(Session.created_at.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(db.exec(stmt))

    def transcript(self, session_id: str) -> dict:
        with session_scope(self.p.engine) as db:
            runs = list(
                db.exec(select(AgentRun).where(AgentRun.session_id == session_id))
            )
            tools = list(
                db.exec(
                    select(ToolInvocation).where(
                        ToolInvocation.session_id == session_id
                    )
                )
            )
        return {
            "runs": [r.model_dump() for r in runs],
            "tools": [t.model_dump() for t in tools],
        }

    # --- review actions (§28) — agents never auto-merge -------------------

    def get_review(self, session_id: str) -> ReviewRequest | None:
        return self._reviews.get(session_id)

    def pending_reviews(self) -> dict[str, ReviewRequest]:
        """All pending reviews keyed by session id (for GET /reviews)."""
        return dict(self._reviews)

    def approve_review(self, session_id: str) -> str:
        """Merge the session branch into base (explicit human approval)."""
        gs = self._git_sessions[session_id]
        result = _approve_review(self._reviews[session_id], gs)
        # The merge landed on base; remove the worktree+branch so they don't
        # accumulate, and drop the in-memory review so it can't be re-approved.
        try:
            gs.cleanup_after_merge()
        except Exception:  # noqa: BLE001
            log.exception("worktree cleanup failed after approving %s", session_id)
        self._reviews.pop(session_id, None)
        self._git_sessions.pop(session_id, None)
        self._delete_pending_review(session_id)
        return result

    def reject_review(self, session_id: str) -> None:
        _reject_review(self._reviews[session_id], self._git_sessions[session_id])
        self._reviews.pop(session_id, None)
        self._git_sessions.pop(session_id, None)
        self._delete_pending_review(session_id)
        # Rejecting means the work was declined — reflect that on the session so
        # the Kanban card lands in the Failed lane (the lane the UI promised),
        # instead of bouncing to Completed as if the work shipped.
        session = self.get_session(session_id)
        if session is not None and session.status is SessionStatus.COMPLETED:
            session.status = SessionStatus.FAILED
            session.summary = (session.summary or "").strip() or "review rejected"
            if not (session.summary or "").endswith("(review rejected)"):
                session.summary = f"{session.summary} (review rejected)"
            self._save(session)

    # --- restart survival: persist + rehydrate review/session state -------

    def _persist_pending_review(self, session_id: str, gs: GitSession) -> None:
        try:
            with session_scope(self.p.engine) as db:
                db.merge(
                    PendingReviewRecord(
                        session_id=session_id,
                        repo=str(gs.repo),
                        branch=gs.branch,
                        base=gs.base,
                    )
                )
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("failed to persist pending review for %s", session_id)

    def _delete_pending_review(self, session_id: str) -> None:
        try:
            with session_scope(self.p.engine) as db:
                rec = db.get(PendingReviewRecord, session_id)
                if rec is not None:
                    db.delete(rec)
                    db.commit()
        except Exception:  # noqa: BLE001
            log.exception("failed to delete pending review for %s", session_id)

    def reconcile_interrupted_sessions(self) -> int:
        """On boot, mark sessions left ACTIVE by a crash/restart as FAILED (none
        are actually running on a fresh process) so they don't linger forever.
        Stranded QUEUED rows get the same treatment (v1.166.0): the queue is
        in-memory, so a restart discards their un-started coroutines."""
        active_ids = set(self._running.keys())
        marked = 0
        interrupted: list[str] = []
        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(
                    select(Session).where(
                        Session.status.in_(  # type: ignore[attr-defined]
                            (SessionStatus.ACTIVE, SessionStatus.QUEUED)
                        )
                    )
                )
            )
            for s in rows:
                if s.id in active_ids:
                    continue
                s.status = SessionStatus.FAILED
                s.finished_at = utcnow()
                # v1.249.0 (R-02): TAG the cause, independent of the summary
                # (a run that already had a summary kept it, so the words
                # alone could not say it was cut off) — the bell and the
                # Overview offer Continue while this is set.
                s.interrupted_at = utcnow()
                if not s.summary:
                    s.summary = RESTART_SUMMARY
                db.add(s)
                marked += 1
                interrupted.append(s.id)
                # THE RUN ROWS ARE SETTLED WITH THE SESSION (v1.227.0, audit
                # RT2/A8). This used to touch ``session`` only, so every
                # AgentRun of an interrupted session stayed RUNNING forever:
                # the team tree showed a live agent inside a FAILED session,
                # and the worklist's dead-run reclaim (which keys on
                # ``AgentRun.state``) treated the ghost's claims as live for
                # the full 15-minute stale window.
                for r in db.exec(select(AgentRun).where(AgentRun.session_id == s.id)):
                    if r.state in (
                        AgentState.COMPLETED,
                        AgentState.FAILED,
                        AgentState.CANCELLED,
                    ):
                        continue
                    r.state = AgentState.FAILED
                    r.finished_at = utcnow()
                    if not r.result:
                        r.result = RESTART_SUMMARY
                    db.add(r)
            # v1.288.0 (agents-04, the one-off repair): rows left RUNNING or
            # WAITING under a session that is ALREADY terminal — the ghosts the
            # pre-v1.288.0 failure finalizer made, which no later path revisits
            # because it only looks at ACTIVE/QUEUED sessions.
            terminal_ids = {
                s.id
                for s in db.exec(
                    select(Session).where(
                        Session.status.in_(  # type: ignore[attr-defined]
                            (SessionStatus.FAILED, SessionStatus.CANCELLED, SessionStatus.COMPLETED)
                        )
                    )
                )
            }
            ghosts = 0
            for r in db.exec(
                select(AgentRun).where(
                    AgentRun.state.in_(  # type: ignore[attr-defined]
                        (AgentState.RUNNING, AgentState.WAITING)
                    )
                )
            ):
                if r.session_id not in terminal_ids:
                    continue
                r.state = AgentState.FAILED
                r.finished_at = utcnow()
                if not r.result:
                    r.result = "its session had already ended"
                db.add(r)
                ghosts += 1
            if marked or ghosts:
                db.commit()
            if ghosts:
                log.info("settled %d run row(s) left running under ended sessions", ghosts)
        # …and their worklist claims go back on the board, so a resumed job
        # is handed the work instead of being told to wait for a run that no
        # longer exists. Sync on purpose: this runs once at boot, before the
        # loop serves requests.
        for sid in interrupted:
            self._release_worklist_claims(sid)
            # v1.345.0 (lane T): an interrupted run asked from a chat tells
            # that chat so (the row's own words, "interrupted by a daemon
            # restart" or the summary it kept). Sync at boot, like the rest.
            row = self.get_session(sid)
            if row is not None:
                self._deliver_to_chat_sync(row)
                # v1.357.0 (G61): a task another computer sent hears that a
                # restart cut it off — on a daemon thread, so boot never
                # waits on another machine.
                self._reply_home(row, from_boot=True)
        # ONE event per boot, never one per session (v1.249.0, R-02): the bell
        # and the Overview poll `/sessions/interrupted`, and this is what makes
        # them show the offer without waiting out a poll interval. Fifty
        # interrupted jobs must not be fifty events on the live feed.
        if interrupted:
            self._publish_bg(
                EventType.SESSIONS_INTERRUPTED,
                {"count": len(interrupted), "session_ids": interrupted},
                "",
            )
        return marked

    def rehydrate_reviews(self) -> int:
        """On boot, rebuild in-memory review state for pending-review sessions
        whose worktree still exists, so they stay approvable after a restart.
        Run BEFORE prune_orphan_worktrees so their worktrees aren't reaped."""
        with session_scope(self.p.engine) as db:
            recs = list(db.exec(select(PendingReviewRecord)))
        rehydrated = 0
        for rec in recs:
            try:
                workspace = self.p.config.workspaces_dir / rec.session_id
                if not (workspace / ".git").exists():  # worktree gone -> stale
                    self._delete_pending_review(rec.session_id)
                    continue
                session = self.get_session(rec.session_id)
                gs = GitSession(
                    repo=Path(rec.repo),
                    workspace=workspace,
                    branch=rec.branch,
                    base=rec.base,
                )
                review = build_review(
                    gs,
                    rec.session_id,
                    summary=session.summary if session else "",
                    tool_history=self.transcript(rec.session_id)["tools"],
                )
                self._git_sessions[rec.session_id] = gs
                self._reviews[rec.session_id] = review
                rehydrated += 1
            except Exception:  # noqa: BLE001
                log.exception("failed to rehydrate review for %s", rec.session_id)
        return rehydrated

    # --- maintenance: garbage-collect orphaned worktrees ------------------

    def _candidate_repos(self) -> list[Path]:
        """Repos whose session worktrees this orchestrator may have created."""
        repos: list[Path] = []
        pr = Path(self.p.config.project_root)
        if (pr / ".git").exists():
            repos.append(pr)
        # Only scan the Iron Jarvis self-dev repo when self-dev is enabled, so we
        # never touch the real project's worktrees from an unrelated daemon.
        if getattr(self.p.config, "self_dev_enabled", False):
            from ..core.self_dev import iron_jarvis_repo_root

            sd = iron_jarvis_repo_root(self.p.config)
            if sd is not None and sd not in repos:
                repos.append(sd)
        return repos

    def prune_orphan_worktrees(self, include_completed: bool = False) -> list[str]:
        """Remove ``ironjarvis/session-*`` worktrees with no live session.

        Review state is in memory, so a daemon restart strands the worktrees of
        any pending review. This bounds the leak: by default it prunes only
        worktrees whose session is FAILED/CANCELLED/missing (never destroying a
        COMPLETED session's pending-review work); ``include_completed=True``
        prunes every orphan. Worktrees of sessions still tracked in memory (live)
        are always preserved.
        """
        from ..git.integration import list_session_worktrees, prune_worktree

        # Snapshot via list(...) (atomic under the GIL) so a concurrent
        # create/approve/reject on another thread can't raise "dict changed size
        # during iteration" — the per-element Path.resolve() widens that window.
        active = {
            str(Path(gs.workspace).resolve()) for gs in list(self._git_sessions.values())
        }
        pruned: list[str] = []
        for repo in self._candidate_repos():
            try:
                worktrees = list_session_worktrees(repo)
            except Exception:  # noqa: BLE001
                continue
            for ws, branch in worktrees:
                if str(ws.resolve()) in active:
                    continue  # in use by a live session
                session = self.get_session(ws.name)
                status = session.status if session else None
                if include_completed:
                    should = True
                elif status in (SessionStatus.FAILED, SessionStatus.CANCELLED):
                    should = True
                elif session is None:
                    # No DB row could mean a just-created worktree (the window
                    # between GitSession.start and the DB save) — only treat it
                    # as a true orphan once it has settled on disk.
                    try:
                        age = time.time() - ws.stat().st_mtime
                    except OSError:
                        age = 1e9
                    should = age > 60
                else:
                    should = False
                if not should:
                    continue
                try:
                    prune_worktree(repo, ws, branch)
                    pruned.append(branch)
                except Exception:  # noqa: BLE001
                    log.exception("failed to prune orphan worktree %s", ws)
        return pruned
