"""Agent definitions (§11).

An agent = identity + capabilities + provider + tools + permissions + policies.
The slice ships a working Builder; the other types (§11) are defined as stubs so
multi-agent orchestration (§12, Phase 6) can flesh them out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import AgentType

_FILE_TOOLS = ["read_file", "write_file", "edit_file", "list_files", "grep"]
# Memory + skills are registered on the platform (§21, §23); advertise them to
# worker agents so they're actually reachable from the agent loop, not just the
# HTTP/registry surface. All default to ``allow`` (low-risk reads/writes).
_KNOWLEDGE_TOOLS = [
    "memory_search",
    "memory_read",
    "memory_write",
    "skill_search",
    "skill_load",
]
# Self-service: agents can search drives, write long-term memory, and create
# their own schedules / webhooks / workflows (the last appears on the user's
# visual workflow canvas). All low-risk + user-visible.
_SELF_SERVICE_TOOLS = [
    "file_search",
    "ltm_search",
    "ltm_append",
    "schedule_create",
    "webhook_add",
    "workflow_create",
]


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
        tools=_FILE_TOOLS + ["shell"] + _KNOWLEDGE_TOOLS + _SELF_SERVICE_TOOLS,
    ),
    AgentType.PLANNER: AgentDefinition(
        type=AgentType.PLANNER,
        system_prompt=(
            "You are the Planner agent. Decompose the task into steps and may "
            "delegate, schedule, or author workflows the user will see."
        ),
        tools=_FILE_TOOLS + _KNOWLEDGE_TOOLS + _SELF_SERVICE_TOOLS,
    ),
    AgentType.REVIEWER: AgentDefinition(
        type=AgentType.REVIEWER,
        system_prompt=(
            "You are the Reviewer agent. Validate work, assess risk, and report."
        ),
        tools=["read_file", "list_files", "grep"] + ["memory_search", "skill_search"],
    ),
    AgentType.SUPERVISOR: AgentDefinition(
        type=AgentType.SUPERVISOR,
        system_prompt=(
            "You are the Supervisor. Decompose the task into subtasks and call the "
            "`delegate` tool to assign each to a subagent. Subagents work in isolation "
            "and return summaries; you never contact the user directly. When all "
            "subtasks are done, reply with a consolidated summary and no further tool calls."
        ),
        tools=["delegate", "read_file", "list_files"],
    ),
}


def get_agent_definition(agent_type: AgentType) -> AgentDefinition:
    if agent_type in _DEFINITIONS:
        return _DEFINITIONS[agent_type]
    # Fall back to a generic builder-like definition.
    return _DEFINITIONS[AgentType.BUILDER]
