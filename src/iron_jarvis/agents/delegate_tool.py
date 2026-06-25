"""Delegate tool (§12 Multi-Agent Orchestration).

The Supervisor uses this tool to hand a subtask to a freshly-spawned subagent.
Each delegation gets its *own* session with an isolated, disposable workspace
(§15) and runs the agent runtime to completion. The subagent operates
independently, never contacts the user, and returns only a SUMMARIZED result
back to the supervisor — everything flows through the supervisor.

The child ``AgentRun`` is linked to the caller via ``parent_id`` so the
supervisor → subagent hierarchy is reconstructable from persistence.
"""

from __future__ import annotations

from typing import Any

from ..core.ids import utcnow
from ..core.models import AgentState, AgentType, SessionStatus
from ..tools.base import Tool, ToolContext, ToolResult


class DelegateTool(Tool):
    name = "delegate"
    description = (
        "Delegate a subtask to a subagent. The subagent runs independently in "
        "its own isolated workspace and returns a summarized result. Use one "
        "delegate call per subtask. Args: agent_type (e.g. 'builder', "
        "'researcher', 'reviewer'; defaults to 'builder') and task (the "
        "self-contained instruction for the subagent)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent_type": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["task"],
    }
    permission_key = "delegate"

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Lazy imports: avoid an agents-package import cycle at module load.
        from .orchestrator import Orchestrator
        from .runtime import AgentRuntime
        from .types import get_agent_definition

        task = args.get("task") or ""
        raw_type = args.get("agent_type") or "builder"
        try:
            agent_type = AgentType(raw_type)
        except ValueError:
            agent_type = AgentType.BUILDER

        orch = Orchestrator(self.platform)
        # Subagents run offline on the default mock provider; the supervisor's
        # own (possibly scripted) provider is intentionally NOT inherited.
        child_session = await orch.create_session(task, agent_type, provider="mock")

        run = await AgentRuntime(self.platform).run(
            child_session,
            get_agent_definition(child_session.agent_type),
            parent_id=ctx.agent_run_id,
        )

        # Reflect the run's outcome onto the child session and persist it.
        child_session.status = (
            SessionStatus.COMPLETED
            if run.state is AgentState.COMPLETED
            else SessionStatus.FAILED
        )
        child_session.provider, child_session.model = run.provider, run.model
        child_session.summary = run.result
        child_session.finished_at = utcnow()
        orch._save(child_session)

        ok = run.state is AgentState.COMPLETED
        return ToolResult(
            ok=ok,
            output=run.result,
            error=None if ok else (run.result or "subagent failed"),
            data={
                "child_run_id": run.id,
                "child_session_id": child_session.id,
                "agent_type": agent_type.value,
                "state": run.state.value,
            },
        )
