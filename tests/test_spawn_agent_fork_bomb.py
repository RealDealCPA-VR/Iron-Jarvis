"""``spawn_agent`` carries DelegateTool's anti-fork-bomb guards (TOFIX-2026-08-20 #2).

``SpawnAgentTool`` advertised itself as mirroring ``delegate`` but implemented
none of its safety: it accepted ``agent='supervisor'`` (a target whose own
roster carries both ``delegate`` and ``spawn_agent``), never walked the
``parent_id`` chain, and — being in ``SAFE_HEADLESS_TOOLS`` — is auto-approved
for unattended runs. A prompt-injected coordinator could therefore recurse
supervisors into supervisors with no bound at all.

Each test below asserts BOTH the refusal and that NOTHING was created: without
the guards the tool creates a child session and runs it to completion, so a test
that only checked ``ok is False`` would not notice a guard that refuses too late.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.agents.agent_tools import agent_management_tools
from iron_jarvis.agents.delegate_tool import _MAX_DELEGATION_DEPTH
from iron_jarvis.agents.dynamic import DynamicAgentRegistry
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, Session
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "create_agent": "allow", "spawn_agent": "allow"}
    )
    return p


@pytest.fixture
def registry(platform):
    return DynamicAgentRegistry(platform.engine).load()


@pytest.fixture
def spawn(platform, registry):
    by_name = {t.name: t for t in agent_management_tools(platform, registry)}
    return by_name["spawn_agent"]


def _ctx(platform, tmp_path, agent_run_id="parent1"):
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _counts(platform) -> tuple[int, int]:
    with session_scope(platform.engine) as db:
        runs = len(list(db.exec(select(AgentRun))))
        sessions = len(list(db.exec(select(Session))))
    return runs, sessions


def _chain(platform, depth: int) -> str:
    """Persist a parent_id chain `depth` links deep; return the deepest run id."""
    parent: str | None = None
    last = ""
    with session_scope(platform.engine) as db:
        for i in range(depth + 1):
            row = AgentRun(id=f"chain{i}", session_id="s", parent_id=parent)
            db.add(row)
            parent = last = row.id
        db.commit()
    return last


async def test_spawn_refuses_a_supervisor_target(platform, spawn, tmp_path):
    before = _counts(platform)
    result = await spawn.execute(
        {"agent": "supervisor", "task": "for thoroughness, do this again"},
        _ctx(platform, tmp_path),
    )
    assert result.ok is False
    assert "supervisor" in (result.error or "")
    # Nothing was created: the refusal beat the create+run.
    assert _counts(platform) == before


async def test_spawn_refuses_a_target_that_can_delegate(platform, spawn, tmp_path):
    # The planner carries `delegate` but is not a SUPERVISOR — the generalized
    # rule, not the type check, is what must catch it.
    before = _counts(platform)
    result = await spawn.execute(
        {"agent": "planner", "task": "plan and re-plan"}, _ctx(platform, tmp_path)
    )
    assert result.ok is False
    assert "fan out" in (result.error or "")
    assert _counts(platform) == before


async def test_spawn_refuses_a_target_that_can_spawn(platform, spawn, tmp_path):
    # `automation` carries `spawn_agent` itself — the literal recursion through
    # this tool's own door, which a delegate-only check would miss.
    before = _counts(platform)
    result = await spawn.execute(
        {"agent": "automation", "task": "wire it up, then wire it up again"},
        _ctx(platform, tmp_path),
    )
    assert result.ok is False
    assert "fan out" in (result.error or "")
    assert _counts(platform) == before


async def test_spawn_refuses_a_dynamic_agent_based_on_supervisor(
    platform, registry, spawn, tmp_path
):
    registry.register(
        "shadow",
        "You coordinate.",
        ["read_file"],
        base_type="supervisor",
        description="",
    )
    before = _counts(platform)
    result = await spawn.execute(
        {"agent": "shadow", "task": "recurse"}, _ctx(platform, tmp_path)
    )
    assert result.ok is False
    assert "supervisor" in (result.error or "")
    assert _counts(platform) == before


async def test_spawn_refuses_a_dynamic_agent_holding_spawn_agent(
    platform, registry, spawn, tmp_path
):
    registry.register(
        "cloner", "You spawn.", ["spawn_agent", "read_file"], base_type="builder"
    )
    before = _counts(platform)
    result = await spawn.execute(
        {"agent": "cloner", "task": "clone yourself"}, _ctx(platform, tmp_path)
    )
    assert result.ok is False
    assert "fan out" in (result.error or "")
    assert _counts(platform) == before


async def test_spawn_enforces_the_shared_depth_cap(platform, spawn, tmp_path):
    # A caller already _MAX_DELEGATION_DEPTH links deep in the parent chain —
    # the SAME chain `delegate` walks, so a mixed delegate/spawn chain counts.
    deepest = _chain(platform, _MAX_DELEGATION_DEPTH)
    before = _counts(platform)
    result = await spawn.execute(
        {"agent": "builder", "task": "one more level"},
        _ctx(platform, tmp_path, agent_run_id=deepest),
    )
    assert result.ok is False
    assert "depth limit" in (result.error or "")
    assert _counts(platform) == before


async def test_spawn_still_runs_a_specialist_below_the_cap(platform, spawn, tmp_path):
    """The guards must not turn into a blanket refusal — a normal spawn works."""
    deepest = _chain(platform, _MAX_DELEGATION_DEPTH - 1)
    result = await spawn.execute(
        {"agent": "builder", "task": "do a thing"},
        _ctx(platform, tmp_path, agent_run_id=deepest),
    )
    assert result.ok, result.error
    with session_scope(platform.engine) as db:
        child = db.get(AgentRun, result.data["child_run_id"])
    assert child is not None and child.parent_id == deepest
    assert child.agent_type is AgentType.BUILDER
