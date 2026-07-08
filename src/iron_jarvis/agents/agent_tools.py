"""Agent-management tools — "agents that add more agents" (§11/§12 extension).

Three tools, backed by a :class:`~iron_jarvis.agents.dynamic.DynamicAgentRegistry`,
let a user *or* an agent extend the platform at runtime:

* ``create_agent`` — register a new dynamic agent (name + prompt + tool allowlist).
* ``list_agents``  — enumerate built-in agent types and dynamic agents.
* ``spawn_agent``  — run a built-in OR dynamic agent as a child subagent.

``spawn_agent`` mirrors the ``delegate`` tool: it creates a child session with an
isolated, disposable workspace, runs the agent runtime to completion, links the
child ``AgentRun`` to the caller via ``parent_id``, and returns the summarized
result. Orchestrator / runtime / definition lookups are imported lazily inside
``execute`` to avoid an agents-package import cycle at module load.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..core.ids import utcnow
from ..core.models import AgentState, AgentType, SessionStatus
from ..tools.base import Tool, ToolContext, ToolResult
from .types import _DEFINITIONS

if TYPE_CHECKING:  # type-only; avoids importing at module load
    from .dynamic import DynamicAgentRegistry


class CreateAgentTool(Tool):
    name = "create_agent"
    description = (
        "Define a new agent at runtime and persist it. The agent reuses a base "
        "agent type but carries its own system prompt and tool allowlist, so it "
        "can later be launched with `spawn_agent`. Args: name (unique), "
        "system_prompt, tools (list of tool names it may use), and an optional "
        "description and base_type (defaults to 'builder')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "system_prompt": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "base_type": {"type": "string"},
        },
        "required": ["name", "system_prompt", "tools"],
    }
    permission_key = "create_agent"

    def __init__(self, platform, registry: "DynamicAgentRegistry") -> None:
        self.platform = platform
        self.registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(ok=False, error="`name` is required")
        system_prompt = args.get("system_prompt") or ""
        tools = args.get("tools") or []
        if not isinstance(tools, list):
            return ToolResult(ok=False, error="`tools` must be a list of tool names")
        description = args.get("description") or ""
        base_type = args.get("base_type") or "builder"

        record = self.registry.register(
            name,
            system_prompt,
            [str(t) for t in tools],
            base_type=base_type,
            description=description,
        )
        return ToolResult(
            ok=True,
            output=(
                f"Created dynamic agent '{record.name}' (base={record.base_type}) "
                f"with tools: {', '.join(json.loads(record.tools_json)) or '(none)'}"
            ),
            data={
                "name": record.name,
                "base_type": record.base_type,
                "tools": json.loads(record.tools_json),
                "description": record.description,
            },
        )


class ListAgentsTool(Tool):
    name = "list_agents"
    description = (
        "List all agents available to launch: the built-in agent types plus any "
        "dynamic agents created at runtime with `create_agent`."
    )
    input_schema = {"type": "object", "properties": {}}
    permission_key = "list_agents"

    def __init__(self, platform, registry: "DynamicAgentRegistry") -> None:
        self.platform = platform
        self.registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        builtin = sorted(t.value for t in _DEFINITIONS)
        dynamic = [
            {
                "name": r.name,
                "base_type": r.base_type,
                "description": r.description,
            }
            for r in self.registry.list()
        ]
        lines = ["Built-in agents: " + ", ".join(builtin)]
        if dynamic:
            lines.append(
                "Dynamic agents: "
                + ", ".join(
                    f"{d['name']} (base={d['base_type']})" for d in dynamic
                )
            )
        else:
            lines.append("Dynamic agents: (none)")
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"builtin": builtin, "dynamic": dynamic},
        )


class SpawnAgentTool(Tool):
    name = "spawn_agent"
    description = (
        "Launch a built-in OR dynamic agent as a subagent. The subagent runs "
        "independently in its own isolated workspace and returns a summarized "
        "result. Args: agent (a built-in type like 'builder' or the name of a "
        "dynamic agent created with `create_agent`) and task (the self-contained "
        "instruction)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["agent", "task"],
    }
    permission_key = "spawn_agent"

    def __init__(self, platform, registry: "DynamicAgentRegistry") -> None:
        self.platform = platform
        self.registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Lazy imports: avoid an agents-package import cycle at module load.
        from .orchestrator import Orchestrator
        from .runtime import AgentRuntime
        from .types import get_agent_definition

        agent_name = (args.get("agent") or "builder").strip()
        task = args.get("task") or ""

        # Prefer a dynamic agent of this name; otherwise treat the name as a
        # built-in AgentType.
        definition = self.registry.definition(agent_name)
        if definition is not None:
            base_type = definition.type
        else:
            try:
                base_type = AgentType(agent_name)
            except ValueError:
                return ToolResult(
                    ok=False, error=f"unknown agent '{agent_name}'"
                )
            definition = get_agent_definition(base_type)

        orch = Orchestrator(self.platform)
        # Subagents INHERIT the parent session's provider/model (like `delegate`)
        # so the user's chosen model is used end-to-end, not the offline mock —
        # and its PROJECT, so a spawned child grounds in the parent's workspace
        # (not whatever project is globally active now).
        parent = orch.get_session(ctx.session_id)
        provider = parent.provider if parent else None
        model = parent.model if parent else None
        project_id = parent.project_id if parent else None
        child_session = await orch.create_session(
            task, base_type, provider=provider, model=model, project_id=project_id
        )
        run = await AgentRuntime(self.platform).run(
            child_session, definition, parent_id=ctx.agent_run_id
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

        # Close the learning loop for the child: spawned work teaches the system
        # too (evaluate -> record outcome -> reflect). Best-effort so a learning
        # failure never breaks the spawn.
        try:
            orch._post_run_learning(child_session)
        except Exception:  # noqa: BLE001
            pass

        ok = run.state is AgentState.COMPLETED
        return ToolResult(
            ok=ok,
            output=run.result,
            error=None if ok else (run.result or "subagent failed"),
            data={
                "agent": agent_name,
                "dynamic": self.registry.get(agent_name) is not None,
                "child_run_id": run.id,
                "child_session_id": child_session.id,
                "state": run.state.value,
            },
        )


def agent_management_tools(
    platform, registry: "DynamicAgentRegistry"
) -> list[Tool]:
    """Build the agent-management tool set bound to ``platform`` + ``registry``."""
    return [
        CreateAgentTool(platform, registry),
        ListAgentsTool(platform, registry),
        SpawnAgentTool(platform, registry),
    ]
