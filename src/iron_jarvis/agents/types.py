"""Agent definitions (§11).

An agent = identity + capabilities + provider + tools + permissions + policies.
The slice ships a working Builder; the other types (§11) are defined as stubs so
multi-agent orchestration (§12, Phase 6) can flesh them out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import AgentType

_FILE_TOOLS = ["read_file", "write_file", "edit_file", "list_files", "grep"]


@dataclass
class AgentDefinition:
    type: AgentType
    system_prompt: str
    tools: list[str]
    permission_overrides: dict[str, str] = field(default_factory=dict)


_DEFINITIONS: dict[AgentType, AgentDefinition] = {
    AgentType.BUILDER: AgentDefinition(
        type=AgentType.BUILDER,
        system_prompt=(
            "You are the Builder agent in Iron Jarvis. Accomplish the user's task by "
            "using the available tools inside your isolated workspace. Take one "
            "concrete action at a time. When the task is complete, reply with a short "
            "summary and no further tool calls."
        ),
        tools=_FILE_TOOLS + ["shell"],
    ),
    AgentType.PLANNER: AgentDefinition(
        type=AgentType.PLANNER,
        system_prompt=(
            "You are the Planner agent. Decompose the task into steps and delegate. "
            "(Delegation lands in Phase 6.)"
        ),
        tools=_FILE_TOOLS,
    ),
    AgentType.REVIEWER: AgentDefinition(
        type=AgentType.REVIEWER,
        system_prompt=(
            "You are the Reviewer agent. Validate work, assess risk, and report. "
            "(Review engine lands in Phase 7.)"
        ),
        tools=["read_file", "list_files", "grep"],
    ),
}


def get_agent_definition(agent_type: AgentType) -> AgentDefinition:
    if agent_type in _DEFINITIONS:
        return _DEFINITIONS[agent_type]
    # Fall back to a generic builder-like definition.
    return _DEFINITIONS[AgentType.BUILDER]
