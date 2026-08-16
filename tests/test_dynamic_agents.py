"""Dynamic agents — "agents that add more agents" (offline, deterministic).

Covers the runtime registry of user/agent-defined agents and the three tools
that drive it:

* ``create_agent`` registers + persists a ``DynamicAgentRecord``;
* a fresh ``DynamicAgentRegistry(engine).load()`` recovers it (persistence);
* ``registry.definition(name)`` rebuilds the stored prompt/tools into an
  ``AgentDefinition``;
* ``spawn_agent`` runs the dynamic agent end-to-end on the offline MockLLM and
  the child ``AgentRun`` completes with ``parent_id`` set;
* ``list_agents`` enumerates both a built-in and the new dynamic agent.

Importing ``dynamic_models`` at the top registers the ``DynamicAgentRecord`` table
on the shared SQLModel metadata BEFORE ``build_platform`` -> ``init_db`` runs, so
the table is created for the test database.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.agents.agent_tools import (
    ListAgentsTool,
    agent_management_tools,
)
from iron_jarvis.agents.dynamic import DynamicAgentRegistry, available_models
from iron_jarvis.agents.dynamic_models import DynamicAgentRecord
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

PROMPT = "You are Scout, a focused research helper. Be concise."
TOOLS = ["read_file", "write_file", "list_files"]


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    # Allow the management tools (and write_file, used by the spawned subagent).
    p.permissions = PermissionEngine(
        {
            **p.config.permissions,
            "create_agent": "allow",
            "list_agents": "allow",
            "spawn_agent": "allow",
        }
    )
    return p


@pytest.fixture
def registry(platform):
    return DynamicAgentRegistry(platform.engine).load()


@pytest.fixture
def tools(platform, registry):
    by_name = {t.name: t for t in agent_management_tools(platform, registry)}
    for tool in by_name.values():
        platform.registry.register(tool)
    return by_name


def _ctx(platform, tmp_path, agent_run_id="parent1"):
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def test_create_agent_persists_record(platform, registry, tools, tmp_path):
    ctx = _ctx(platform, tmp_path)
    result = await platform.registry.invoke(
        "create_agent",
        {
            "name": "scout",
            "system_prompt": PROMPT,
            "tools": TOOLS,
            "description": "research helper",
        },
        ctx,
        platform.permissions,
    )
    assert result.ok, result.error
    assert result.data["name"] == "scout"

    # Row landed in the DB.
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(select(DynamicAgentRecord).where(DynamicAgentRecord.name == "scout"))
        )
    assert len(rows) == 1
    assert rows[0].system_prompt == PROMPT
    assert json.loads(rows[0].tools_json) == TOOLS
    assert rows[0].base_type == "builder"


async def test_fresh_registry_recovers_persisted_agent(platform, registry, tools, tmp_path):
    await platform.registry.invoke(
        "create_agent",
        {"name": "scout", "system_prompt": PROMPT, "tools": TOOLS},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    # A brand-new registry must rebuild the agent purely from persistence.
    recovered = DynamicAgentRegistry(platform.engine).load()
    record = recovered.get("scout")
    assert record is not None
    assert record.system_prompt == PROMPT
    assert json.loads(record.tools_json) == TOOLS


async def test_definition_rebuilds_agent_definition(platform, registry, tools, tmp_path):
    await platform.registry.invoke(
        "create_agent",
        {"name": "scout", "system_prompt": PROMPT, "tools": TOOLS},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    definition = registry.definition("scout")
    assert isinstance(definition, AgentDefinition)
    # v1.171.0: a dynamic agent's COMPOSED prompt leads with the identity
    # anchor; the STORED prompt (rows/records above) stays byte-identical.
    from iron_jarvis.agents.dynamic import identity_anchor

    assert definition.system_prompt == identity_anchor("scout") + "\n\n" + PROMPT
    assert definition.tools == TOOLS
    # Dynamic agents reuse a base AgentType for lifecycle/persistence.
    assert definition.type is AgentType.BUILDER
    assert registry.definition("does-not-exist") is None


async def test_upsert_updates_in_place(platform, registry):
    registry.register("scout", PROMPT, TOOLS, description="v1")
    registry.register("scout", "new prompt", ["read_file"], description="v2")
    record = registry.get("scout")
    assert record.system_prompt == "new prompt"
    assert json.loads(record.tools_json) == ["read_file"]
    assert record.description == "v2"
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(select(DynamicAgentRecord).where(DynamicAgentRecord.name == "scout"))
        )
    assert len(rows) == 1  # upsert, not a duplicate insert


# -- model / provider selection (UI: choose the LLM for a dynamic agent) ----


def test_register_persists_provider_and_model(platform):
    registry = DynamicAgentRegistry(platform.engine).load()
    registry.register(
        "opus_agent",
        PROMPT,
        TOOLS,
        provider="anthropic",
        model="claude-opus-4-8",
    )

    # A brand-new registry recovers the provider/model purely from persistence.
    recovered = DynamicAgentRegistry(platform.engine).load()
    record = recovered.get("opus_agent")
    assert record is not None
    assert record.provider == "anthropic"
    assert record.model == "claude-opus-4-8"


def test_register_provider_model_default_empty(platform):
    registry = DynamicAgentRegistry(platform.engine).load()
    # Backward compatible: omitting provider/model leaves them blank (= default).
    record = registry.register("plain", PROMPT, TOOLS)
    assert record.provider == ""
    assert record.model == ""


def test_available_models_includes_mock_and_anthropic():
    models = available_models()
    pairs = {(m["provider"], m["model"]) for m in models}
    assert ("mock", "mock-1") in pairs
    assert any(provider == "anthropic" for provider, _ in pairs)


async def test_spawn_agent_runs_dynamic_agent_end_to_end(platform, registry, tools, tmp_path):
    # Register a dynamic agent whose tools let the offline MockLLM do real work.
    await platform.registry.invoke(
        "create_agent",
        {"name": "scout", "system_prompt": PROMPT, "tools": TOOLS},
        _ctx(platform, tmp_path),
        platform.permissions,
    )

    result = await platform.registry.invoke(
        "spawn_agent",
        {"agent": "scout", "task": "summarize the project"},
        _ctx(platform, tmp_path, agent_run_id="parent1"),
        platform.permissions,
    )
    assert result.ok, result.error
    assert result.data["dynamic"] is True

    # A child AgentRun exists, linked by parent_id, and completed offline.
    with session_scope(platform.engine) as db:
        children = list(
            db.exec(select(AgentRun).where(AgentRun.parent_id == "parent1"))
        )
    assert children, "spawn_agent should create a child AgentRun with parent_id=parent1"
    child = children[0]
    assert child.id == result.data["child_run_id"]
    assert child.state == AgentState.COMPLETED
    assert child.provider == "mock"

    # The dynamic agent worked in its own isolated workspace.
    workspace = platform.config.workspaces_dir / result.data["child_session_id"]
    assert (workspace / "RESULT.md").exists()


async def test_spawn_agent_runs_builtin_agent(platform, registry, tools, tmp_path):
    # No dynamic agent named "builder" -> falls back to the built-in type.
    result = await platform.registry.invoke(
        "spawn_agent",
        {"agent": "builder", "task": "do a thing"},
        _ctx(platform, tmp_path, agent_run_id="parent2"),
        platform.permissions,
    )
    assert result.ok, result.error
    assert result.data["dynamic"] is False
    assert result.data["state"] == AgentState.COMPLETED.value


async def test_list_agents_includes_builtin_and_dynamic(platform, registry, tools, tmp_path):
    await platform.registry.invoke(
        "create_agent",
        {"name": "scout", "system_prompt": PROMPT, "tools": TOOLS},
        _ctx(platform, tmp_path),
        platform.permissions,
    )
    list_tool: ListAgentsTool = tools["list_agents"]
    result = await list_tool.execute({}, _ctx(platform, tmp_path))
    assert result.ok
    assert "builder" in result.data["builtin"]  # a built-in agent type
    assert any(d["name"] == "scout" for d in result.data["dynamic"])  # the new one
    assert "builder" in result.output and "scout" in result.output


# -- an EMPTY roster inherits the base type's tools -------------------------
#
# THE LIVE BUG: `dashboard/components/agents/SetupCard.tsx` has no tool picker
# and posts `tools: []` hardcoded, so every agent the user created from the
# Agents page rebuilt into `AgentDefinition(tools=[])` and could do NOTHING —
# the runtime advertises exactly `registry.specs(agent_def.tools)`, and an
# empty roster reads as a dumb model rather than as a disarmed agent. An empty
# stored list means "not specified", never "no tools". Imports stay local to
# each test (the file's existing style) so this section is a pure append.


def test_empty_roster_inherits_base_type_tools(platform):
    from iron_jarvis.agents.dynamic import identity_anchor
    from iron_jarvis.agents.types import get_agent_definition

    registry = DynamicAgentRegistry(platform.engine).load()
    # Exactly what the dashboard posts today: a prompt, and tools=[].
    registry.register("dash_agent", PROMPT, [], base_type="builder")

    definition = registry.definition("dash_agent")
    assert definition is not None
    assert definition.tools, "an inherited roster must be non-empty"
    assert definition.tools == list(get_agent_definition(AgentType.BUILDER).tools)
    # The stored record is untouched — the edit dialog still shows what the user
    # typed, and inheritance stays a COMPOSITION-time decision (as v1.171.0's
    # identity anchor is), which this asserts is unchanged by the new branch.
    assert json.loads(registry.get("dash_agent").tools_json) == []
    assert definition.system_prompt == identity_anchor("dash_agent") + "\n\n" + PROMPT


def test_empty_roster_inherits_the_declared_base_type_not_builder(platform):
    from iron_jarvis.agents.types import get_agent_definition

    # Inheritance follows the record's OWN base type, so a researcher-based
    # agent gets the researcher roster, not the builder fallback.
    registry = DynamicAgentRegistry(platform.engine).load()
    registry.register("digger", PROMPT, [], base_type="researcher")

    definition = registry.definition("digger")
    assert definition.type is AgentType.RESEARCHER
    assert definition.tools == list(get_agent_definition(AgentType.RESEARCHER).tools)


def test_non_empty_roster_is_honored_verbatim(platform):
    from iron_jarvis.agents.types import get_agent_definition

    # An explicit allowlist stays an allowlist: no base-type tools bleed in.
    registry = DynamicAgentRegistry(platform.engine).load()
    registry.register("narrow", PROMPT, TOOLS, base_type="builder")

    definition = registry.definition("narrow")
    assert definition.tools == TOOLS
    builtin_only = set(get_agent_definition(AgentType.BUILDER).tools) - set(TOOLS)
    assert builtin_only, "fixture guard: the builder roster must be wider than TOOLS"
    assert not builtin_only & set(definition.tools)


def test_inheritance_never_mutates_the_shared_builtin_definition(platform):
    from iron_jarvis.agents.types import get_agent_definition

    # `get_agent_definition` returns the SHARED module-level object, and callers
    # append to `definition.tools`; handing that same list out would leak one
    # dynamic agent's roster into every builtin session for the process's life.
    registry = DynamicAgentRegistry(platform.engine).load()
    registry.register("leaky", PROMPT, [], base_type="builder")
    builtin = get_agent_definition(AgentType.BUILDER)
    before = list(builtin.tools)

    first = registry.definition("leaky")
    first.tools.append("tool_from_a_dynamic_agent")
    second = registry.definition("leaky")

    assert builtin.tools == before  # the shared object survived the append
    assert first.tools is not builtin.tools
    assert second.tools is not first.tools
    assert "tool_from_a_dynamic_agent" not in second.tools
    assert second.tools == before


def test_malformed_tools_json_inherits_rather_than_disarming(platform):
    from iron_jarvis.agents.types import get_agent_definition

    # The except-branch produced `[]` too, which is the same silent disarm.
    registry = DynamicAgentRegistry(platform.engine).load()
    registry.register("broken", PROMPT, TOOLS, base_type="builder")
    with session_scope(platform.engine) as db:
        row = db.exec(
            select(DynamicAgentRecord).where(DynamicAgentRecord.name == "broken")
        ).first()
        row.tools_json = "{not json"
        db.add(row)
        db.commit()
    registry._records.pop("broken", None)  # force a re-read from the DB

    definition = registry.definition("broken")
    assert definition.tools == list(get_agent_definition(AgentType.BUILDER).tools)
