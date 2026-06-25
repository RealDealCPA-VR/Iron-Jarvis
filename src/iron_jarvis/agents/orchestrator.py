"""Orchestrator (§12 host; §14 sessions; §15 workspaces).

Creates sessions with isolated, disposable workspaces and drives the agent
runtime. For the slice this is single-agent; the supervisor → subagent hierarchy
(§12) plugs in at Phase 6 via ``AgentRuntime.run(parent_id=...)``.
"""

from __future__ import annotations

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

    def _save(self, session: Session) -> None:
        with session_scope(self.p.engine) as db:
            db.merge(session)
            db.commit()

    def _git_enabled(self) -> bool:
        cfg = self.p.config
        return bool(getattr(cfg, "git_native", False)) and (
            Path(cfg.project_root) / ".git"
        ).exists()

    async def create_session(
        self,
        task: str,
        agent_type: AgentType = AgentType.BUILDER,
        provider: str | None = None,
    ) -> Session:
        session = Session(
            task=task,
            agent_type=agent_type,
            provider=provider or self.p.config.default_provider,
            model=self.p.config.default_model,
            status=SessionStatus.ACTIVE,
        )
        workspace = self.p.config.workspaces_dir / session.id
        if self._git_enabled():
            try:  # git-native: a worktree on a session branch (§27)
                gs = GitSession.start(
                    Path(self.p.config.project_root), workspace, slug=task[:40] or "session"
                )
                self._git_sessions[session.id] = gs
            except Exception:  # fall back to a plain disposable workspace
                workspace.mkdir(parents=True, exist_ok=True)
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
        session.finished_at = utcnow()
        self._save(session)
        await self.p.event_bus.publish(
            EventType.SESSION_COMPLETED,
            {"status": session.status.value, "summary": session.summary},
            session_id=session.id,
        )

        # Phase 9: score the run (never fatal to the session).
        try:
            self.p.evaluator.evaluate(session.id)
        except Exception:  # noqa: BLE001
            log.exception("evaluation failed for session %s", session.id)

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
        return result

    def reject_review(self, session_id: str) -> None:
        _reject_review(self._reviews[session_id], self._git_sessions[session_id])
        self._reviews.pop(session_id, None)
        self._git_sessions.pop(session_id, None)
