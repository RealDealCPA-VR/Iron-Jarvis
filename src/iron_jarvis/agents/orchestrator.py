"""Orchestrator (§12 host; §14 sessions; §15 workspaces).

Creates sessions with isolated, disposable workspaces and drives the agent
runtime. For the slice this is single-agent; the supervisor → subagent hierarchy
(§12) plugs in at Phase 6 via ``AgentRuntime.run(parent_id=...)``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import (
    AgentRun,
    AgentState,
    AgentType,
    PendingReviewRecord,
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
from .runtime import AgentRuntime
from .supervisor import run_supervised
from .types import get_agent_definition

log = get_logger("orchestrator")


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

    def register_running(self, session_id: str, task: asyncio.Task) -> None:
        """Track a background run so it can be cancelled (called by the daemon).

        Always attaches a self-removing done-callback so a finished/failed run can't
        leak its ``_running`` entry — the autonomy non-wait path registers here
        directly (not via the daemon's _spawn_bg), and previously leaked an entry
        per auto-executed/approved session, inflating running_sessions forever."""
        self._running[session_id] = task
        task.add_done_callback(lambda t, sid=session_id: self._running.pop(sid, None))

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
    ) -> Session:
        repo_for_worktree: Path | None = None
        if self_dev:
            # Gated self-development: edit Iron Jarvis itself on a worktree of its
            # OWN repo, as the Maintainer, still review-gated (never auto-merge).
            repo_for_worktree = self._self_dev_repo()
            agent_type = AgentType.MAINTAINER
        elif self._git_enabled():
            repo_for_worktree = Path(self.p.config.project_root)

        session = Session(
            task=task,
            agent_type=agent_type,
            provider=provider or self.p.config.default_provider,
            model=model or self.p.config.default_model,
            status=SessionStatus.ACTIVE,
        )
        workspace = self.p.config.workspaces_dir / session.id
        if repo_for_worktree is not None:
            try:  # git-native: a worktree on a session branch (§27)
                gs = GitSession.start(
                    repo_for_worktree, workspace, slug=task[:40] or "session"
                )
                self._git_sessions[session.id] = gs
            except Exception:
                if self_dev:
                    raise  # self-dev MUST run on a worktree; never silently fall back
                workspace.mkdir(parents=True, exist_ok=True)  # plain disposable ws
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

    async def run_session(self, session_id: str) -> Session:
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
        try:
            if session.agent_type is AgentType.SUPERVISOR:
                run = await run_supervised(self.p, session)  # §12 delegate to subagents
            else:
                agent_def = get_agent_definition(session.agent_type)
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

        # Phase 7: if this ran on a git worktree, build a review — never auto-merge.
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
        gs = self._git_sessions.pop(session.id, None)
        if gs is not None:
            try:
                gs.discard()
            except Exception:  # noqa: BLE001
                log.exception("worktree cleanup failed after cancelling %s", session.id)

    def cancel_session(self, session_id: str) -> Session:
        """Stop a running session. Raises KeyError if unknown, ValueError if
        already finished. Cancelling an in-flight background run unwinds it to
        CANCELLED via run_session's handler; a session with no live task (e.g. a
        synchronous run that already settled) is marked CANCELLED directly."""
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
        else:
            session.status = SessionStatus.CANCELLED
            session.finished_at = utcnow()
            self._save(session)
        return self.get_session(session_id) or session

    async def rerun_session(self, session_id: str) -> Session:
        """Clone a session's inputs (task/agent/provider/model) into a fresh run.

        A MAINTAINER (self-dev) session is re-run as self-dev so it still lands on
        an Iron Jarvis worktree (and fails closed if self-dev is now disabled)."""
        prev = self.get_session(session_id)
        if prev is None:
            raise KeyError(f"unknown session '{session_id}'")
        return await self.create_session(
            prev.task,
            prev.agent_type,
            provider=prev.provider,
            model=prev.model,
            self_dev=prev.agent_type is AgentType.MAINTAINER,
        )

    async def continue_session(self, session_id: str, message: str) -> Session:
        """Start a follow-up run that reuses the finished session's workspace and
        a compact recap of the prior task/result, enabling multi-turn work."""
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
                    busy = db.exec(
                        select(Session).where(
                            Session.workspace_path == ws,
                            Session.status == SessionStatus.ACTIVE,
                        )
                    ).first()
                if busy is not None:
                    raise ValueError(
                        "a continuation is already running in this workspace — wait "
                        "for it to finish before continuing again"
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
        ws_path = session.workspace_path
        gs = self._git_sessions.pop(session_id, None)
        if gs is not None:
            try:
                gs.discard()  # removes the worktree dir + branch
            except Exception:  # noqa: BLE001
                log.exception("worktree cleanup failed while deleting %s", session_id)
        self._reviews.pop(session_id, None)
        with session_scope(self.p.engine) as db:
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
        if gs is None and ws_path:
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

    def list_sessions(self) -> list[Session]:
        with session_scope(self.p.engine) as db:
            return list(db.exec(select(Session).order_by(Session.created_at.desc())))

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
        are actually running on a fresh process) so they don't linger forever."""
        active_ids = set(self._running.keys())
        marked = 0
        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(select(Session).where(Session.status == SessionStatus.ACTIVE))
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
