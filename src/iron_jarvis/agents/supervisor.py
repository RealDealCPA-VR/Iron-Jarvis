"""Supervisor agent (§12 Multi-Agent Orchestration).

The Supervisor decomposes a task into subtasks and delegates each to a subagent
via the ``delegate`` tool. Subagents run with isolated context and return only
SUMMARIZED results; the supervisor is the single point of contact and produces
the final summary. Subagents never talk to the user directly.
"""

from __future__ import annotations

from ..core.models import AgentRun, AgentType
from .delegate_tool import DelegateTool
from .runtime import AgentRuntime
from .types import AgentDefinition

SUPERVISOR_DEFINITION = AgentDefinition(
    type=AgentType.SUPERVISOR,
    system_prompt=(
        "You are the Supervisor agent in Iron Jarvis. Break the user's task into "
        "small, self-contained subtasks. For each subtask, call the `delegate` "
        "tool with an appropriate `agent_type` (e.g. 'builder', 'researcher', "
        "'reviewer') and a precise `task` describing exactly what the subagent "
        "must accomplish. Subagents run independently in isolated workspaces and "
        "return only a summary — they never contact the user, so all coordination "
        "flows through you. When every subtask is complete, reply with a single "
        "consolidated summary and no further tool calls."
    ),
    tools=["delegate"],
)


async def run_supervised(platform, session) -> AgentRun:
    """Run a supervisor session, wiring the ``delegate`` tool on demand.

    Ensures ``DelegateTool`` is registered in the platform's tool registry (so
    the supervisor can spawn subagents) and then drives the standard agent
    runtime against :data:`SUPERVISOR_DEFINITION`.
    """
    if platform.registry.get("delegate") is None:
        platform.registry.register(DelegateTool(platform))
    return await AgentRuntime(platform).run(session, SUPERVISOR_DEFINITION)
