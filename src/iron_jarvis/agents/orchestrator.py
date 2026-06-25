"""Orchestrator (§12 host; §14 sessions; §15 workspaces).

Creates sessions with isolated, disposable workspaces and drives the agent
runtime. For the slice this is single-agent; the supervisor → subagent hierarchy
(§12) plugs in at Phase 6 via ``AgentRuntime.run(parent_id=...)``.
"""

from __future__ import annotations

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import (
    AgentRun,
    AgentState,
    AgentType,
    Session,
    SessionStatus,
    ToolInvocation,
)
from .runtime import AgentRuntime
from .types import get_agent_definition


class Orchestrator:
    def __init__(self, platform) -> None:
        self.p = platform
        self.runtime = AgentRuntime(platform)

    def _save(self, session: Session) -> None:
        with session_scope(self.p.engine) as db:
            db.merge(session)
            db.commit()

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
