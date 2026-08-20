"""Delegation as TEAMWORK (v1.193.0) — the `delegate-coop` unit.

Three separable capabilities, one per section:

1. THE FOLDER RIDES THE DELEGATION. A parent running directly in the user's
   real folder (a Projects in-folder task) hands that folder to its children,
   so a delegated deliverable lands where the parent and the user look. A
   parent on a managed/disposable workspace keeps today's isolation.
2. FAN-OUT IS BOUNDED. One coordinator turn emitting N delegate calls no longer
   starts N concurrent child runs — without routing children through the
   session governor, which would deadlock a blocked parent.
3. DELEGATION IS ANNOUNCED. `delegation.started` / `delegation.completed` carry
   the edge (parent run, child run, child session, agent name, ok, result), and
   the child's ADDRESS reaches the model in `ToolResult.output`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.orchestrator import (
    Orchestrator,
    child_fanout_key,
    inherited_workspace_root,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    Session,
    SessionStatus,
)
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(DelegateTool(p))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "delegate": "allow", "write_file": "allow"}
    )
    return p


def _ctx(platform, workspace, session_id: str, agent_run_id: str = ""):
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _events(platform, type_: str):
    return [e for e in platform.event_bus.history if e.type == type_]


# --------------------------------------------------------------------------- #
# 1. the folder rides the delegation
# --------------------------------------------------------------------------- #
async def test_delegated_child_works_in_the_parents_real_folder(platform, tmp_path):
    """END TO END: the child's deliverable lands in the USER's folder.

    Before this change the child got an empty scratch dir under
    ``workspaces_dir``: it could not read the files it was asked to work on and
    its output was written where nobody looks.
    """
    folder = tmp_path / "client-2026"
    folder.mkdir()
    (folder / "SOURCE.txt").write_text("the parent's material", encoding="utf-8")

    orch = Orchestrator(platform)
    parent = await orch.create_session(
        "organize the folder", AgentType.SUPERVISOR, workspace_root=str(folder)
    )

    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "write up the material"},
        _ctx(platform, folder, parent.id),
    )

    assert res.ok is True
    child = orch.get_session(res.data["child_session_id"])
    assert child is not None
    assert child.workspace_path == str(folder)
    assert res.data["workspace"] == str(folder)
    # The offline mock subagent writes RESULT.md — it must land in the USER's
    # folder, not in a disposable workspace.
    assert (folder / "RESULT.md").exists()
    assert not (platform.config.workspaces_dir / child.id).exists()


async def test_a_managed_parent_still_isolates_its_children(platform, tmp_path):
    """The isolation path is UNCHANGED: only a DIRECT workspace is forwarded."""
    orch = Orchestrator(platform)
    parent = await orch.create_session("scratch work", AgentType.SUPERVISOR)
    assert str(platform.config.workspaces_dir) in parent.workspace_path

    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "do a thing"},
        _ctx(platform, tmp_path, parent.id),
    )

    child = orch.get_session(res.data["child_session_id"])
    assert child.workspace_path != parent.workspace_path
    assert child.workspace_path == str(platform.config.workspaces_dir / child.id)


def test_inherited_workspace_root_predicate(platform, tmp_path):
    """The one predicate both doors share, in isolation."""
    cfg = platform.config
    assert inherited_workspace_root(cfg, None) is None
    managed = Session(task="t", workspace_path=str(cfg.workspaces_dir / "sess-1"))
    assert inherited_workspace_root(cfg, managed) is None
    empty = Session(task="t", workspace_path="")
    assert inherited_workspace_root(cfg, empty) is None
    direct = Session(task="t", workspace_path=str(tmp_path / "real"))
    assert inherited_workspace_root(cfg, direct) == str(tmp_path / "real")


# --------------------------------------------------------------------------- #
# 2. bounded fan-out (no governor, no deadlock)
# --------------------------------------------------------------------------- #
async def test_one_turns_fan_out_is_capped(platform, tmp_path, monkeypatch):
    """Eight delegate calls from ONE parent must not run eight children at once."""
    import iron_jarvis.agents.orchestrator as orch_mod
    import iron_jarvis.agents.runtime as runtime_mod

    monkeypatch.setattr(orch_mod, "max_concurrent_children", lambda _cfg: 2)

    live = 0
    peak = 0

    async def fake_run(self, session, agent_def, parent_id=None):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.02)
        finally:
            live -= 1
        return AgentRun(
            session_id=session.id,
            parent_id=parent_id,
            agent_type=agent_def.type,
            provider=session.provider,
            model=session.model,
            state=AgentState.COMPLETED,
            result="done",
        )

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", fake_run)

    tool = DelegateTool(platform)
    ctx = _ctx(platform, tmp_path, "parent-session", agent_run_id="parent-run")
    results = await asyncio.gather(
        *(
            tool.execute({"agent_type": "builder", "task": f"chunk {i}"}, ctx)
            for i in range(8)
        )
    )

    assert all(r.ok for r in results)
    assert len({r.data["child_session_id"] for r in results}) == 8  # all ran
    assert peak <= 2, f"fan-out was not bounded: {peak} children ran at once"


def test_fanout_key_is_per_caller_not_per_department():
    """The anti-deadlock property: a child never waits on its own parent's key.

    Keys are minted per RUN, so a delegating child's own children queue on a
    different key than the one its parent's siblings hold — no cycle.
    """
    assert child_fanout_key("run-a", "s1") != child_fanout_key("run-b", "s1")
    assert child_fanout_key("", "s1") == child_fanout_key(None, "s1")
    assert child_fanout_key(None, None)  # never an empty key


# --------------------------------------------------------------------------- #
# 3. the team announces itself
# --------------------------------------------------------------------------- #
async def test_delegation_start_and_completion_are_published(platform, tmp_path):
    orch = Orchestrator(platform)
    parent = await orch.create_session("coordinate", AgentType.SUPERVISOR)

    res = await DelegateTool(platform).execute(
        {"agent_type": "researcher", "task": "look it up"},
        _ctx(platform, tmp_path, parent.id, agent_run_id="parent-run-1"),
    )
    assert res.ok

    started = _events(platform, EventType.DELEGATION_STARTED)
    completed = _events(platform, EventType.DELEGATION_COMPLETED)
    assert len(started) == 1 and len(completed) == 1

    s = started[0]
    assert s.session_id == parent.id  # tagged on the session the user watches
    assert s.payload["parent_run_id"] == "parent-run-1"
    assert s.payload["child_session_id"] == res.data["child_session_id"]
    assert s.payload["agent"] == "researcher"
    assert s.payload["task"] == "look it up"

    c = completed[0]
    assert c.session_id == parent.id
    assert c.payload["parent_run_id"] == "parent-run-1"
    assert c.payload["child_run_id"] == res.data["child_run_id"]
    assert c.payload["child_session_id"] == res.data["child_session_id"]
    assert c.payload["agent"] == "researcher"
    assert c.payload["ok"] is True
    assert c.payload["result"]  # a short summary, never empty on a real result
    assert len(c.payload["result"]) <= 240

    # The edge the event carries is the same one persistence records.
    with session_scope(platform.engine) as db:
        child_run = db.get(AgentRun, c.payload["child_run_id"])
    assert child_run is not None and child_run.parent_id == "parent-run-1"


async def test_delegation_completed_fires_when_the_child_crashes(
    platform, tmp_path, monkeypatch
):
    import iron_jarvis.agents.runtime as runtime_mod

    async def boom(self, session, agent_def, parent_id=None):
        raise RuntimeError("provider refused")

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", boom)

    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "x"},
        _ctx(platform, tmp_path, "no-such-session", agent_run_id="p1"),
    )
    assert res.ok is False
    completed = _events(platform, EventType.DELEGATION_COMPLETED)
    assert len(completed) == 1
    assert completed[0].payload["ok"] is False
    assert completed[0].payload["child_run_id"] is None  # never minted
    assert "provider refused" in completed[0].payload["result"]


async def test_output_carries_the_child_handle_the_model_can_address(
    platform, tmp_path
):
    """`data` never reaches the model — the handle must be in `output`."""
    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "x"},
        _ctx(platform, tmp_path, "no-such-session"),
    )
    assert res.ok
    assert res.data["child_run_id"] in res.output  # message_agent addresses this
    assert res.data["child_session_id"] in res.output
    assert "builder" in res.output
    # …and the child's own report is still carried, under the handle line.
    child = Orchestrator(platform).get_session(res.data["child_session_id"])
    assert child.summary and child.summary.strip() in res.output


async def test_the_handle_survives_the_runtimes_tool_output_truncation(
    platform, tmp_path, monkeypatch
):
    """A LONG child report must not cost the parent the child's address.

    ``agents/runtime`` caps a tool result fed to the model and keeps the HEAD
    (``content[:_MAX_TOOL_CONTEXT_CHARS]`` + a note). A handle appended after
    the summary is therefore exactly what gets cut on a big report — the case
    where a follow-up question matters most. So the handle leads.
    """
    import inspect

    import iron_jarvis.agents.runtime as runtime_mod

    # Pin the shape we are defending against: the runtime keeps the head. If it
    # ever switches to tail-truncation this mirror must be revisited.
    assert "content[:_MAX_TOOL_CONTEXT_CHARS]" in inspect.getsource(runtime_mod)

    huge = "z" * (runtime_mod._MAX_TOOL_CONTEXT_CHARS * 3)

    async def fat_run(self, session, agent_def, parent_id=None):
        return AgentRun(
            session_id=session.id,
            parent_id=parent_id,
            agent_type=agent_def.type,
            state=AgentState.COMPLETED,
            result=huge,
        )

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", fat_run)

    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "write at length"},
        _ctx(platform, tmp_path, "no-such-session"),
    )
    assert res.ok
    # Exactly what runtime.py does before handing the result to the model.
    model_sees = res.output[: runtime_mod._MAX_TOOL_CONTEXT_CHARS]
    assert res.data["child_run_id"] in model_sees
    assert res.data["child_session_id"] in model_sees


async def test_a_stopped_delegation_still_closes_its_announced_edge(
    platform, tmp_path, monkeypatch
):
    """Stop on a supervisor: the child is CANCELLED, and the edge is CLOSED.

    `delegation.started` is already published by the time the parent is
    cancelled, so without a completion the event stream carries a handoff that
    never settles — the announce half of "the child must ALWAYS settle".
    """
    import iron_jarvis.agents.runtime as runtime_mod

    async def stopped(self, session, agent_def, parent_id=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", stopped)

    with pytest.raises(asyncio.CancelledError):
        await DelegateTool(platform).execute(
            {"agent_type": "builder", "task": "x"},
            _ctx(platform, tmp_path, "no-such-session", agent_run_id="p-stop"),
        )

    assert len(_events(platform, EventType.DELEGATION_STARTED)) == 1
    completed = _events(platform, EventType.DELEGATION_COMPLETED)
    assert len(completed) == 1, "a Stop left the delegation edge permanently open"
    assert completed[0].payload["ok"] is False
    assert completed[0].payload["child_run_id"] is None
    assert completed[0].payload["result"] == "cancelled"
    assert (
        completed[0].payload["child_session_id"]
        == _events(platform, EventType.DELEGATION_STARTED)[0].payload[
            "child_session_id"
        ]
    )


# --------------------------------------------------------------------------- #
# the same three, through the OTHER door (spawn_agent mirrors delegate)
# --------------------------------------------------------------------------- #
async def test_spawn_agent_mirrors_folder_events_and_handle(platform, tmp_path):
    from iron_jarvis.agents.agent_tools import SpawnAgentTool

    folder = tmp_path / "spawned-in"
    folder.mkdir()
    orch = Orchestrator(platform)
    parent = await orch.create_session(
        "run the job", AgentType.AUTOMATION, workspace_root=str(folder)
    )

    res = await SpawnAgentTool(platform, platform.agents_registry).execute(
        {"agent": "builder", "task": "produce the file"},
        _ctx(platform, folder, parent.id, agent_run_id="auto-run"),
    )

    assert res.ok is True
    child = orch.get_session(res.data["child_session_id"])
    assert child.workspace_path == str(folder)
    assert (folder / "RESULT.md").exists()
    assert res.data["child_run_id"] in res.output
    assert len(_events(platform, EventType.DELEGATION_STARTED)) == 1
    assert len(_events(platform, EventType.DELEGATION_COMPLETED)) == 1


async def test_a_crashed_spawn_never_strands_an_active_child_in_the_users_folder(
    platform, tmp_path, monkeypatch
):
    """THE WEDGE inheritance would otherwise create.

    `spawn_agent` had no settle guard. That was benign while a crashed child
    held a disposable scratch dir; now the child holds the USER's REAL FOLDER,
    and `continue_session` refuses every later turn in a workspace that has an
    ACTIVE session — so one provider refusal (the v1.162.0 case) wedges the
    user's own chat/continue lane there until a daemon restart reconciles. And
    it is silent: `registry.invoke` traps the exception, so the parent run just
    carries on.
    """
    from iron_jarvis.agents.agent_tools import SpawnAgentTool
    import iron_jarvis.agents.runtime as runtime_mod

    async def boom(self, session, agent_def, parent_id=None):
        raise RuntimeError("provider refused")

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", boom)

    folder = tmp_path / "live-folder"
    folder.mkdir()
    orch = Orchestrator(platform)
    parent = await orch.create_session(
        "work the folder", AgentType.AUTOMATION, workspace_root=str(folder)
    )

    res = await SpawnAgentTool(platform, platform.agents_registry).execute(
        {"agent": "builder", "task": "produce the file"},
        _ctx(platform, folder, parent.id, agent_run_id="auto-run"),
    )

    # The tool reports the failure honestly instead of returning nothing.
    assert res.ok is False
    assert "provider refused" in (res.error or "")

    child = orch.get_session(res.data["child_session_id"])
    assert child is not None
    assert child.status is SessionStatus.FAILED, "a crashed child stayed ACTIVE"
    assert child.workspace_path == str(folder)

    # …and nothing is left holding the folder, so a continuation still works.
    with session_scope(platform.engine) as db:
        still_busy = db.exec(
            select(Session).where(
                Session.workspace_path == str(folder),
                Session.status.in_((SessionStatus.ACTIVE, SessionStatus.QUEUED)),
            )
        ).all()
    assert [s.id for s in still_busy] == [parent.id]

    completed = _events(platform, EventType.DELEGATION_COMPLETED)
    assert len(completed) == 1, "delegation.started was left unpaired"
    assert completed[0].payload["ok"] is False
    assert completed[0].payload["child_run_id"] is None
