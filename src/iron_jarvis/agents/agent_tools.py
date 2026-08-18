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
        if not tools:
            # AN EMPTY LIST FROM A MODEL IS NOT THE SAME AS AN EMPTY FORM FIELD
            # (v1.178.0). Since this release a stored empty roster means "not
            # specified" and resolves to the BASE TYPE's full roster — which is
            # right for the dashboard, where the field does not exist and the
            # user plainly did not mean "no tools". Reached from HERE the same
            # semantics would let a model mint an agent holding everything its
            # base type holds, `shell` included, by simply omitting the list.
            # So this path demands the list it already declares as required:
            # naming the tools is the authoring decision.
            return ToolResult(
                ok=False,
                error=(
                    "`tools` must name at least one tool — an agent's tool list "
                    "is its capability contract, so it has to be stated here "
                    "rather than inherited. Call tool_list to see what exists."
                ),
            )
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
                f"with tools: {', '.join(json.loads(record.tools_json))}"
            ),
            data={
                "name": record.name,
                "base_type": record.base_type,
                "tools": json.loads(record.tools_json),
                "description": record.description,
            },
        )


#: Tool names shown per agent before the listing switches to "… +N more".
#: A supervisor deciding who to delegate to needs the SHAPE of a roster, not a
#: transcript of it: spelling out ~50 names per agent would cost more context
#: than the decision is worth, and the full list still rides in ``data``.
_ROSTER_PREVIEW = 12


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

    def _effective_tools(self, name: str) -> list[str] | None:
        """What this agent ACTUALLY holds, inheritance resolved (v1.185.0).

        The HTTP route has reported this since v1.178.0 and this tool did not,
        so an AGENT asking what a teammate holds saw only the base type — while
        the human looking at the same agent in the dashboard saw the resolved
        roster. Whoever is deciding whether to delegate needs the same answer,
        and here that decision is made by the caller most likely to act on it.

        `None` means UNKNOWN and `[]` means genuinely none, the same dialect the
        route speaks: an unknown must never be relayed to a model as "this agent
        can do nothing", because that reads as a reason not to delegate.
        """
        try:
            definition = self.registry.definition(name)
        except Exception:  # noqa: BLE001 — a listing never fails on one row
            return None
        return list(definition.tools) if definition is not None else None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        builtin = sorted(t.value for t in _DEFINITIONS)
        dynamic = []
        for r in self.registry.list():
            row: dict[str, Any] = {
                "name": r.name,
                "base_type": r.base_type,
                "description": r.description,
            }
            tools = self._effective_tools(r.name)
            if tools is not None:
                row["effective_tools"] = tools
            dynamic.append(row)
        lines = ["Built-in agents: " + ", ".join(builtin)]
        if dynamic:
            # THE ROSTER GOES IN THE TEXT, not only in `data` (v1.185.0). The
            # runtime hands the model `result.output` and nothing else, so a
            # field added to `data` alone would satisfy the letter of "the tool
            # reports effective tools" while the model — the only caller that
            # acts on it — still could not see it.
            lines.append("Dynamic agents:")
            for row in dynamic:
                head = f"  - {row['name']} (base={row['base_type']})"
                tools = row.get("effective_tools")
                if tools is None:
                    # UNKNOWN, said as unknown. "no tools" here would read as a
                    # reason not to delegate to a perfectly capable agent.
                    lines.append(f"{head} — roster unavailable")
                elif not tools:
                    lines.append(f"{head} — holds no tools")
                else:
                    shown = tools[: _ROSTER_PREVIEW]
                    tail = (
                        f" … +{len(tools) - len(shown)} more"
                        if len(tools) > len(shown)
                        else ""
                    )
                    # Cap, then SAY SO — a silently short roster reads as
                    # complete and the model concludes a tool is absent.
                    lines.append(
                        f"{head} — {len(tools)} tools: " + ", ".join(shown) + tail
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
