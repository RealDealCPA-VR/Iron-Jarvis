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

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, AgentType, SessionStatus
from ..tools.base import Tool, ToolContext, ToolResult

#: Hardest cap on the supervisor→subagent delegation chain. Combined with
#: "no delegating to a SUPERVISOR" (only supervisors carry the delegate tool, so a
#: specialist child can't recurse), this bounds a prompt-injected fork-bomb.
_MAX_DELEGATION_DEPTH = 3


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

    def _delegation_depth(self, agent_run_id: str | None) -> int:
        """How deep the CALLER already is in the delegation chain (root = 0), by
        walking AgentRun.parent_id. Bounds the exponential fan-out of a
        prompt-injected 'delegate to a supervisor' loop."""
        depth = 0
        current = agent_run_id
        seen: set[str] = set()
        with session_scope(self.platform.engine) as db:
            while current and current not in seen and depth < 100:
                seen.add(current)
                row = db.get(AgentRun, current)
                parent = getattr(row, "parent_id", None) if row is not None else None
                if not parent:
                    break
                current = parent
                depth += 1
        return depth

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

        # Anti-fork-bomb: never delegate to another SUPERVISOR (only supervisors
        # carry the delegate tool, so a specialist child can't recurse), and cap the
        # chain depth. A prompt-injected 'delegate this to a supervisor' loop would
        # otherwise fan out exponentially into real LLM sessions.
        if agent_type is AgentType.SUPERVISOR:
            return ToolResult(
                ok=False,
                output="",
                error="cannot delegate to a 'supervisor' — delegate to a specialist "
                "agent (builder/researcher/reviewer/planner) instead",
            )
        if self._delegation_depth(ctx.agent_run_id) >= _MAX_DELEGATION_DEPTH:
            return ToolResult(
                ok=False,
                output="",
                error=f"delegation depth limit ({_MAX_DELEGATION_DEPTH}) reached — "
                "do this subtask directly instead of delegating further",
            )

        orch = Orchestrator(self.platform)
        # Subagents INHERIT the parent session's provider/model so a real
        # multi-agent run uses the user's chosen model end-to-end (a Claude
        # supervisor delegates to Claude subagents — not the offline mock).
        # Fall back to the configured defaults when the parent is unknown.
        parent = orch.get_session(ctx.session_id)
        provider = parent.provider if parent else None
        model = parent.model if parent else None
        child_session = await orch.create_session(
            task, agent_type, provider=provider, model=model
        )

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
