"""Agent Runtime + lifecycle (§11, §13).

Runs a single agent's perceive→act loop: ask the model (via the router) for the
next action, execute any tool calls (gated by the permission engine), feed
results back, and repeat until the model finalizes or the step budget is spent.
Lifecycle state transitions are persisted and emitted on the event bus.
"""

from __future__ import annotations

from pathlib import Path

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, Session
from ..providers.adapters.base import LLMMessage
from ..tools.base import ToolContext
from .types import AgentDefinition

_TERMINAL = {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}


class AgentRuntime:
    def __init__(self, platform) -> None:
        self.p = platform

    def _save(self, run: AgentRun) -> None:
        with session_scope(self.p.engine) as db:
            db.merge(run)
            db.commit()

    async def _set_state(
        self, run: AgentRun, state: AgentState, session_id: str
    ) -> None:
        prev = run.state
        run.state = state
        if state in _TERMINAL:
            run.finished_at = utcnow()
        self._save(run)
        await self.p.event_bus.publish(
            EventType.AGENT_STATE_CHANGED,
            {"run_id": run.id, "from": prev.value, "to": state.value},
            session_id=session_id,
        )

    async def run(
        self,
        session: Session,
        agent_def: AgentDefinition,
        parent_id: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            session_id=session.id,
            parent_id=parent_id,
            agent_type=agent_def.type,
            provider=session.provider,
            model=session.model,
            state=AgentState.CREATED,
        )
        self._save(run)

        await self._set_state(run, AgentState.INITIALIZING, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_STARTED,
            {"agent": agent_def.type.value, "run_id": run.id, "task": session.task},
            session_id=session.id,
        )
        await self._set_state(run, AgentState.RUNNING, session.id)

        workspace = Path(session.workspace_path)
        messages: list[LLMMessage] = [LLMMessage(role="user", content=session.task)]
        tool_specs = self.p.registry.specs(agent_def.tools)
        final_text = ""

        for step in range(self.p.config.max_agent_steps):
            route = await self.p.router.complete(
                provider=session.provider,
                system=agent_def.system_prompt,
                messages=messages,
                tools=tool_specs,
                session_id=session.id,
            )
            resp = route.response
            run.steps = step + 1
            run.provider, run.model = route.provider, route.model

            if not resp.wants_tools:
                final_text = resp.text
                self._save(run)
                break

            messages.append(
                LLMMessage(
                    role="assistant", content=resp.text, tool_calls=resp.tool_calls
                )
            )
            ctx = ToolContext(
                workspace=workspace,
                session_id=session.id,
                agent_run_id=run.id,
                config=self.p.config,
                event_bus=self.p.event_bus,
                engine=self.p.engine,
            )
            for tc in resp.tool_calls:
                result = await self.p.registry.invoke(
                    tc.name,
                    tc.arguments,
                    ctx,
                    self.p.permissions,
                    agent_def.permission_overrides,
                )
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result.output if result.ok else (result.error or "error"),
                    )
                )
            self._save(run)
        else:
            run.result = "stopped: reached max steps before completion"
            await self._set_state(run, AgentState.FAILED, session.id)
            await self.p.event_bus.publish(
                EventType.AGENT_COMPLETED,
                {"run_id": run.id, "ok": False, "result": run.result},
                session_id=session.id,
            )
            return run

        run.result = final_text or "(no final message)"
        await self._set_state(run, AgentState.COMPLETED, session.id)
        await self.p.event_bus.publish(
            EventType.AGENT_COMPLETED,
            {"run_id": run.id, "ok": True, "result": run.result},
            session_id=session.id,
        )
        return run
