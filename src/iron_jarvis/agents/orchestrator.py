"""Orchestrator (§12 host; §14 sessions; §15 workspaces).

Creates sessions with isolated, disposable workspaces and drives the agent
runtime. The supervisor → subagent hierarchy (§12) is LIVE: ``run_session``
dispatches SUPERVISOR sessions to ``run_supervised``, whose ``delegate`` tool
spawns child sessions through ``AgentRuntime.run(parent_id=...)`` (depth-capped,
parallel siblings via the runtime's gathered tool calls). Since v1.166.0 the
orchestrator is also the background-run concurrency governor: ``spawn_managed``
runs or parks (QUEUED) session work under ``config.max_concurrent_sessions``.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
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
from .runtime import AgentRuntime, is_direct_workspace
from .decompose import is_bulk_task
from .supervisor import run_supervised, with_worklist
from .types import AgentDefinition, get_agent_definition

log = get_logger("orchestrator")


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
        limit = int(getattr(self.p.config, "max_concurrent_sessions", 0) or 0)
        if limit <= 0:
            return self._start_managed(session_id, coro)
        session = self.get_session(session_id)
        if session is None:  # non-session background work is never governed
            return self._start_managed(session_id, coro)
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
        self._publish_bg(
            EventType.SESSION_QUEUED,
            {
                "task": session.task,
                "position": len(self._queued),
            },
            session_id,
        )
        return None

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
        brand-new agent run mid-shutdown is exactly wrong."""
        if self._draining:
            return
        limit = int(getattr(self.p.config, "max_concurrent_sessions", 0) or 0)
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
            try:
                self._governed.add(session_id)  # promoted into a governed slot
                self._start_managed(session_id, coro)
            except RuntimeError:  # event loop gone (shutdown) — never started
                self._governed.discard(session_id)
                coro.close()  # reconcile marks the stranded row on next boot
                log.warning("could not start queued session %s (no loop)", session_id)
            return  # one dequeue per freed slot

    def _remove_queued(self, session_id: str) -> bool:
        """Drop a parked entry, closing its un-started coroutine. True if found."""
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
        while self._queued:
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
    ) -> Session:
        import json as _json

        repo_for_worktree: Path | None = None
        # A project-folder task runs DIRECTLY in the user's folder (full
        # read/write there, confined to it) — not a disposable worktree — so
        # its deliverables land where the user expects. workspace_root wins
        # over git-native for exactly this reason.
        direct_root = None
        if workspace_root:
            direct_root = Path(workspace_root)
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
            # TX-01 provenance: WHO/WHAT started this (user_chat, autonomy,
            # schedule, comm, reflex, …). self_dev is inferable; everything else
            # is passed by the caller. Defaults None = unattributed (the audit
            # timeline falls back to inferring from the spawning event).
            origin=origin or ("self_dev" if self_dev else None),
            # Contract 4 (v1.174.0): per-session step budget. None = the
            # configured ``max_agent_steps`` (today's behavior).
            max_steps=normalize_max_steps(max_steps),
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

    async def run_session(
        self, session_id: str, definition: "AgentDefinition | None" = None
    ) -> Session:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session '{session_id}'")
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
            # A wait:true / schedule-fired run is a REAL concurrent session —
            # it occupies a governed slot too (v1.167.0), so parked background
            # runs don't over-promote past the limit while it works.
            self._governed.add(session_id)
            self_registered = True
        try:
            try:
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
                session.summary = run.result
                session.input_tokens = run.input_tokens
                session.output_tokens = run.output_tokens
                session.finished_at = utcnow()
                self._save(session)
                await self.p.event_bus.publish(
                    EventType.SESSION_COMPLETED,
                    {"status": session.status.value, "summary": session.summary},
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

            return session
        finally:
            if self_registered and self._running.get(session_id) is current:
                self._running.pop(session_id, None)
                self._governed.discard(session_id)
                self._dequeue_next()  # the governed slot frees NOW, not at task end

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

    async def _finalize_failed(self, session: Session, error: Exception) -> None:
        """Mark a crashed run FAILED, persist, emit SESSION_COMPLETED(ok=False), GC
        its worktree — so an unexpected exception never leaves a zombie ACTIVE
        session the app can't see or recover."""
        session.status = SessionStatus.FAILED
        session.summary = session.summary or f"Session failed: {type(error).__name__}: {error}"
        session.finished_at = utcnow()
        try:
            self._save(session)
        except Exception:  # noqa: BLE001 - never block teardown on persistence
            log.exception("failed to persist FAILED state for %s", session.id)
        try:
            await self.p.event_bus.publish(
                EventType.SESSION_COMPLETED,
                {"status": session.status.value, "summary": session.summary, "ok": False},
                session_id=session.id,
            )
        except Exception:  # noqa: BLE001 - never block teardown on the event bus
            log.exception("failed to publish failure event for %s", session.id)
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

    async def _finalize_cancelled(self, session: Session) -> None:
        """Mark a cancelled run CANCELLED, persist, notify, and GC its worktree."""
        session.status = SessionStatus.CANCELLED
        session.summary = session.summary or "Session cancelled by the user."
        session.finished_at = utcnow()
        self._save(session)
        # Settle any in-flight AgentRun rows so they don't linger in RUNNING.
        with session_scope(self.p.engine) as db:
            for r in db.exec(select(AgentRun).where(AgentRun.session_id == session.id)):
                if r.state not in (
                    AgentState.COMPLETED,
                    AgentState.FAILED,
                    AgentState.CANCELLED,
                ):
                    r.state = AgentState.CANCELLED
                    r.finished_at = utcnow()
                    db.add(r)
            db.commit()
        try:
            await self.p.event_bus.publish(
                EventType.SESSION_COMPLETED,
                {"status": session.status.value, "summary": session.summary},
                session_id=session.id,
            )
        except Exception:  # noqa: BLE001 - never block teardown on the event bus
            log.exception("failed to publish cancel event for %s", session.id)
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
            task.cancel()  # -> CancelledError in run_session -> _finalize_cancelled
        elif self._remove_queued(session_id):
            # Parked, never started (v1.166.0): the un-started coroutine was
            # closed by _remove_queued; finalize honestly — no agent ever ran.
            session.status = SessionStatus.CANCELLED
            session.summary = session.summary or "Cancelled while queued (never started)."
            session.finished_at = utcnow()
            self._save(session)
        else:
            session.status = SessionStatus.CANCELLED
            session.finished_at = utcnow()
            self._save(session)
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

    async def continue_session(self, session_id: str, message: str) -> Session:
        """Start a follow-up run that reuses the finished session's workspace and
        a compact recap of the prior task/result, enabling multi-turn work.

        A continuation is the SAME piece of work, so it inherits the same inputs
        a rerun does — including the user's up-front tool GRANT and the ORIGIN
        that says a human is watching. See the comments on the Session below:
        both used to drop here, which quietly fail-closed every turn after the
        first of an escalated chat.
        """
        import json as _json

        prev = self.get_session(session_id)
        if prev is None:
            raise KeyError(f"unknown session '{session_id}'")
        recap = (
            f"{message}\n\n[Continuing an earlier session. Original task: "
            f"{prev.task!r}. Prior result: {prev.summary or '(none)'} "
            f"The earlier workspace files are available in your workspace.]"
        )
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
            allow_tools_json=_json.dumps(_stored_allow_tools(prev)),
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
                        "a continuation is already running or queued in this "
                        "workspace — wait for it to finish before continuing again"
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
                if not s.summary:
                    s.summary = "interrupted by a daemon restart"
                db.add(s)
                marked += 1
            if marked:
                db.commit()
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
