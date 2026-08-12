"""v1.166.0 P4 — multiagent core: the planner can delegate.

Four changes under test, all offline and deterministic:

1. The PLANNER definition carries the ``delegate`` tool, and ``AgentRuntime
   .run`` registers ``DelegateTool`` on demand for ANY delegate-carrying
   definition (mirrors ``run_supervised`` — previously the only wiring site).
2. Anti-recursion is GENERALIZED: the delegate tool and the roster both refuse
   any target whose definition itself carries ``delegate`` (supervisor AND
   planner, dynamic agents included), with honest refusal messages. The
   supervisor's pinned message is unchanged.
3. ``config.decompose_all_tasks`` (default False) extends decomposition to
   every provider when set — regardless of the resolved ``tool_use_mode`` —
   while the default keeps today's behavior byte-for-byte.
4. ``config.max_concurrent_sessions`` (default 0 = unlimited) exists for the
   P5 session queue.
"""

from __future__ import annotations

import sys
import types as _types_mod
from types import SimpleNamespace

from sqlmodel import select

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.decompose import should_decompose
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.roster import build_roster, resolve_target, roster_block
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.config import Config, load_config
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse
from iron_jarvis.tools.base import ToolContext


# --------------------------------------------------------------------- helpers
def _ctx(platform, tmp_path) -> ToolContext:
    ws = tmp_path / "delegate-ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="parent-session",
        agent_run_id="parent-run",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


class _Adapter(LLMAdapter):
    """Minimal scripted adapter: ``tool_use`` decides whether the router wraps
    it in the prompted scaffold (False → "prompted") or serves it native."""

    def __init__(self, provider, tool_use, model="m1"):
        self.provider = provider
        self.model = model
        self._tool_use = tool_use

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": self._tool_use,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text="ok", usage={})


#: Two imperative clauses → plausibly multi-step for the engage heuristic.
_MULTI = "Read notes.txt then write a summary to out.md"


def _session(task=_MULTI, provider="native-x") -> SimpleNamespace:
    return SimpleNamespace(id="sess-test", task=task, provider=provider, model=None)


# ------------------------------------------------- (1) definitions + registration
def test_planner_definition_carries_delegate_and_specialists_do_not():
    planner = get_agent_definition(AgentType.PLANNER)
    assert "delegate" in planner.tools
    # the supervisor is unchanged; the specialists never gained it
    assert "delegate" in get_agent_definition(AgentType.SUPERVISOR).tools
    for t in (AgentType.BUILDER, AgentType.REVIEWER, AgentType.RESEARCHER):
        assert "delegate" not in get_agent_definition(t).tools


def test_types_docstring_no_longer_claims_stubs():
    import iron_jarvis.agents.types as types_mod

    doc = types_mod.__doc__ or ""
    assert "stub" not in doc.lower()
    assert "Phase 6" not in doc


async def test_runtime_registers_delegate_on_demand_for_planner(platform):
    # platform boot wires DelegateTool; strip it to prove the runtime mirrors
    # run_supervised's on-demand registration for a delegate-carrying def.
    assert platform.registry.unregister("delegate") is True
    assert platform.registry.get("delegate") is None
    sess = await Orchestrator(platform).create_session(
        "plan the work", AgentType.PLANNER
    )
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.PLANNER)
    )
    assert run.state is AgentState.COMPLETED
    tool = platform.registry.get("delegate")
    assert tool is not None and tool.name == "delegate"
    # ...and the registered tool actually reaches the advertised spec set.
    specs = platform.registry.specs(get_agent_definition(AgentType.PLANNER).tools)
    assert "delegate" in {s["name"] for s in specs}


async def test_runtime_does_not_register_delegate_for_non_carriers(platform):
    assert platform.registry.unregister("delegate") is True
    sess = await Orchestrator(platform).create_session("do it", AgentType.BUILDER)
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.BUILDER)
    )
    assert run.state is AgentState.COMPLETED
    assert platform.registry.get("delegate") is None  # condition, not side effect


async def test_runtime_keeps_an_already_registered_delegate_instance(platform):
    existing = platform.registry.get("delegate")
    assert existing is not None  # wired at platform boot
    sess = await Orchestrator(platform).create_session("plan", AgentType.PLANNER)
    await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.PLANNER))
    assert platform.registry.get("delegate") is existing  # never re-wrapped


# ------------------------------------------------------ (2) generalized refusals
async def test_delegate_refuses_planner_target_honestly(platform, tmp_path):
    res = await DelegateTool(platform).execute(
        {"agent_type": "planner", "task": "x"}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    err = res.error or ""
    assert "'planner'" in err
    assert "delegate" in err  # names the reason: the target can itself delegate
    assert "(builder/researcher/reviewer)" in err  # honest suggestion, no planner
    # No child run was spawned by the refusal.
    with session_scope(platform.engine) as db:
        children = list(
            db.exec(select(AgentRun).where(AgentRun.parent_id == "parent-run"))
        )
    assert children == []


async def test_delegate_supervisor_refusal_message_unchanged(platform, tmp_path):
    res = await DelegateTool(platform).execute(
        {"agent_type": "supervisor", "task": "loop"}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    assert "cannot delegate to a 'supervisor'" in (res.error or "")


async def test_delegate_capitalized_coordinator_names_still_refused(
    platform, tmp_path
):
    """Case fold at the AgentType fallback (v1.166.0 fix): 'Planner'/'SUPERVISOR'
    are non-delegable so the roster returns None, and without the fold
    ``AgentType('Planner')`` raises → silent BUILDER coercion — a capitalized
    coordinator name would dodge the honest refusal entirely."""
    tool = DelegateTool(platform)
    res = await tool.execute(
        {"agent_type": "Planner", "task": "x"}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    err = res.error or ""
    assert "'planner'" in err and "delegate" in err
    res = await tool.execute(
        {"agent_type": "SUPERVISOR", "task": "x"}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    assert "cannot delegate to a 'supervisor'" in (res.error or "")
    # Neither refusal spawned a child run.
    with session_scope(platform.engine) as db:
        children = list(
            db.exec(select(AgentRun).where(AgentRun.parent_id == "parent-run"))
        )
    assert children == []


async def test_delegate_capitalized_specialist_spawns_that_specialist(
    platform, tmp_path, monkeypatch
):
    """v1.165.0 regression pin: even with the roster unavailable (fallback
    path), 'REVIEWER' resolves case-insensitively to the reviewer — it must
    never be coerced to a builder by a ValueError on the uppercase name."""
    fake = _types_mod.ModuleType("iron_jarvis.agents.roster")
    fake.resolve_target = lambda p, n: None
    monkeypatch.setitem(sys.modules, "iron_jarvis.agents.roster", fake)
    res = await DelegateTool(platform).execute(
        {"agent_type": "REVIEWER", "task": "look at it"}, _ctx(platform, tmp_path)
    )
    assert res.ok is True
    assert res.data["agent_type"] == "reviewer"  # the VALUE, not just ok


async def test_delegate_refuses_dynamic_delegate_carrier_via_roster(
    platform, tmp_path
):
    # Through the REAL roster: a delegate-carrying dynamic agent is no longer
    # delegable, so the prefixed target gets the honest not-delegable refusal.
    platform.agents_registry.register(
        "coord", "you coordinate", ["delegate", "read_file"], description="coord"
    )
    res = await DelegateTool(platform).execute(
        {"agent_type": "custom:coord", "task": "x"}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    assert "'custom:coord' is not delegable right now" in (res.error or "")


async def test_delegate_tool_itself_refuses_delegate_carrier_defense_in_depth(
    platform, tmp_path, monkeypatch
):
    """Even when a (stale/broken) roster resolves the target as delegable, the
    tool checks the stored definition's own tool list."""
    platform.agents_registry.register(
        "coord2", "you coordinate", ["delegate"], description="coord"
    )
    fake = _types_mod.ModuleType("iron_jarvis.agents.roster")
    fake.resolve_target = lambda p, n: SimpleNamespace(
        name="custom:coord2", kind="dynamic"
    )
    monkeypatch.setitem(sys.modules, "iron_jarvis.agents.roster", fake)
    res = await DelegateTool(platform).execute(
        {"agent_type": "custom:coord2", "task": "x"}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    err = res.error or ""
    assert "'custom:coord2'" in err
    assert "(builder/researcher/reviewer)" in err


async def test_delegate_builder_still_works_end_to_end(platform, tmp_path):
    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "small task"}, _ctx(platform, tmp_path)
    )
    assert res.ok is True
    assert res.data["agent_type"] == "builder"
    assert res.data["state"] == "completed"
    with session_scope(platform.engine) as db:
        children = list(
            db.exec(select(AgentRun).where(AgentRun.parent_id == "parent-run"))
        )
    assert len(children) == 1 and children[0].state is AgentState.COMPLETED


def test_delegate_description_no_longer_advertises_planner():
    assert "planner" not in DelegateTool.description


# ------------------------------------------------------------ (2b) roster rules
def test_roster_planner_and_supervisor_not_delegable_specialists_are(platform):
    entries = {e.name: e for e in build_roster(platform)}
    assert entries["planner"].delegable is False
    assert entries["supervisor"].delegable is False
    assert entries["builder"].delegable is True
    assert entries["researcher"].delegable is True
    assert entries["reviewer"].delegable is True


def test_roster_block_never_offers_planner(platform):
    block = roster_block(platform)
    assert "\n- builder" in block  # specialists still offered
    assert "\n- planner" not in block
    assert "\n- supervisor" not in block


def test_resolve_target_planner_is_none_builder_is_not(platform):
    assert resolve_target(platform, "planner") is None
    assert resolve_target(platform, "builder") is not None


def test_roster_dynamic_delegate_carrier_and_supervisor_base_not_delegable(
    platform,
):
    platform.agents_registry.register("coord", "p", ["delegate"])
    platform.agents_registry.register("boss", "p", [], base_type="supervisor")
    platform.agents_registry.register("helper", "p", ["read_file"])
    entries = {e.name: e for e in build_roster(platform)}
    assert entries["custom:coord"].delegable is False
    assert entries["custom:boss"].delegable is False
    assert entries["custom:helper"].delegable is True
    assert resolve_target(platform, "custom:coord") is None
    assert resolve_target(platform, "custom:helper") is not None


# --------------------------------------------- (3) decompose_all_tasks engage
def test_decompose_all_tasks_default_off_keeps_todays_behavior(platform):
    platform.providers.register(
        "native-x", lambda model=None: _Adapter("native-x", tool_use=True)
    )
    assert platform.config.decompose_all_tasks is False  # the shipped default
    # Native adapter + multi-step task → flat loop, exactly as today.
    assert should_decompose(platform, _session()) is False


def test_decompose_all_tasks_engages_regardless_of_tool_mode(platform):
    platform.providers.register(
        "native-x", lambda model=None: _Adapter("native-x", tool_use=True)
    )
    platform.config.decompose_all_tasks = True
    # Natively tool-capable adapter now engages...
    assert should_decompose(platform, _session()) is True
    # ...and so does the mock (tool mode is genuinely irrelevant when set).
    assert should_decompose(platform, _session(provider="mock")) is True
    # A simple task still never decomposes — the flag widens WHO, not WHAT.
    assert should_decompose(platform, _session(task="Say hello")) is False


def test_decompose_all_tasks_wins_even_with_local_flag_off(platform):
    platform.config.decompose_all_tasks = True
    platform.config.decompose_local_tasks = False
    assert should_decompose(platform, _session(provider="mock")) is True


def test_prompted_local_path_unchanged_by_the_new_flag(platform):
    platform.providers.register(
        "local-x", lambda model=None: _Adapter("local-x", tool_use=False)
    )
    # decompose_all_tasks stays False: the v1.132.0 conditions still govern.
    assert should_decompose(platform, _session(provider="local-x")) is True
    platform.config.decompose_local_tasks = False
    assert should_decompose(platform, _session(provider="local-x")) is False


# ---------------------------------------------------------- (4) config keys
def test_config_new_keys_defaults(tmp_path):
    cfg = Config(project_root=tmp_path, home=tmp_path / ".ironjarvis")
    assert cfg.decompose_all_tasks is False
    assert cfg.max_concurrent_sessions == 0


def test_config_new_keys_load_from_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    root = tmp_path / "proj"
    home = root / ".ironjarvis"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        "decompose_all_tasks = true\nmax_concurrent_sessions = 3\n",
        encoding="utf-8",
    )
    loaded = load_config(root)
    assert loaded.decompose_all_tasks is True
    assert loaded.max_concurrent_sessions == 3
    # A persisted config WITHOUT the keys loads cleanly to the defaults.
    (home / "config.toml").write_text('default_provider = "mock"\n', encoding="utf-8")
    clean = load_config(root)
    assert clean.decompose_all_tasks is False
    assert clean.max_concurrent_sessions == 0
