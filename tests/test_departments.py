"""Departments substrate tests (concurrent tool loop + shared blackboard).

Fully offline + deterministic. Two moves are exercised:

1. The agent runtime runs a turn's tool calls CONCURRENTLY (asyncio.gather) but
   appends the results in the ORIGINAL call order; one failing tool does not
   abort its siblings; the transcript is deterministic.
2. A session/department-scoped BLACKBOARD lets sibling sub-agents post findings
   and message each other, scoped so one team's notes never leak into another's.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.blackboard import BlackboardStore, resolve_board_id
from iron_jarvis.blackboard.models import BlackboardKind
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine


# --- test doubles ---------------------------------------------------------


class CaptureMock(MockLLMAdapter):
    """A scripted adapter that records the messages it is handed each turn, so a
    test can inspect the tool-result transcript the model actually sees."""

    provider = "dept"
    model = "dept-1"

    def __init__(self, script: list[LLMResponse]) -> None:
        super().__init__(script)
        self.seen: list[list[Any]] = []

    async def complete(self, *, system, messages, tools):  # type: ignore[override]
        self.seen.append(list(messages))
        return self._script.pop(0)


class _OrderTool(Tool):
    """A tool that sleeps then records its completion order, so concurrency is
    observable: with descending delays, completion order REVERSES call order."""

    def __init__(self, name: str, delay: float, completions: list[str]) -> None:
        self.name = name
        self.permission_key = name
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}
        self._delay = delay
        self._completions = completions

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(self._delay)
        self._completions.append(self.name)
        return ToolResult(ok=True, output=f"done:{self.name}")


class _BoomTool(Tool):
    name = "boom"
    permission_key = "boom"
    description = "always raises"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise ValueError("kaboom")


def _allow(platform, names: list[str]) -> None:
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, **{n: "allow" for n in names}}
    )


async def _run_three_calls(platform, completions: list[str]) -> CaptureMock:
    """Register tA/tB/tC + script a turn that calls all three, then run it."""
    platform.registry.register(_OrderTool("tA", 0.03, completions))
    platform.registry.register(_OrderTool("tB", 0.02, completions))
    platform.registry.register(_OrderTool("tC", 0.01, completions))
    _allow(platform, ["tA", "tB", "tC"])

    adapter = CaptureMock(
        script=[
            LLMResponse(
                tool_calls=[
                    ToolCall("c-a", "tA", {}),
                    ToolCall("c-b", "tB", {}),
                    ToolCall("c-c", "tC", {}),
                ],
                finish_reason="tool_use",
            ),
            LLMResponse(text="all done", finish_reason="stop"),
        ]
    )
    platform.providers.register("dept", lambda: adapter)

    from iron_jarvis.agents.orchestrator import Orchestrator

    session = await Orchestrator(platform).create_session(
        "concurrent turn", AgentType.BUILDER, provider="dept"
    )
    agent_def = AgentDefinition(
        type=AgentType.BUILDER, system_prompt="x", tools=["tA", "tB", "tC"]
    )
    await AgentRuntime(platform).run(session, agent_def)
    return adapter


# --- 1. concurrent tool loop ---------------------------------------------


async def test_tool_calls_run_concurrently_and_results_stay_in_call_order(platform):
    completions: list[str] = []
    adapter = await _run_three_calls(platform, completions)

    # Concurrency: with descending delays the FAST call finishes first, so the
    # completion order is the REVERSE of the call order — only possible if the
    # three ran together rather than serially.
    assert completions == ["tC", "tB", "tA"]

    # ...yet the tool RESULTS handed to the model on the next turn are in the
    # ORIGINAL call order (the model maps results to calls positionally).
    turn2 = adapter.seen[1]
    tool_msgs = [m for m in turn2 if m.role == "tool"]
    assert [m.name for m in tool_msgs] == ["tA", "tB", "tC"]
    assert [m.tool_call_id for m in tool_msgs] == ["c-a", "c-b", "c-c"]
    assert [m.content for m in tool_msgs] == ["done:tA", "done:tB", "done:tC"]


async def test_failing_tool_does_not_abort_siblings(platform):
    completions: list[str] = []
    platform.registry.register(_OrderTool("tA", 0.0, completions))
    platform.registry.register(_BoomTool())
    platform.registry.register(_OrderTool("tC", 0.0, completions))
    _allow(platform, ["tA", "boom", "tC"])

    adapter = CaptureMock(
        script=[
            LLMResponse(
                tool_calls=[
                    ToolCall("c-a", "tA", {}),
                    ToolCall("c-boom", "boom", {}),
                    ToolCall("c-c", "tC", {}),
                ],
                finish_reason="tool_use",
            ),
            LLMResponse(text="done", finish_reason="stop"),
        ]
    )
    platform.providers.register("dept", lambda: adapter)

    from iron_jarvis.agents.orchestrator import Orchestrator

    session = await Orchestrator(platform).create_session(
        "one fails", AgentType.BUILDER, provider="dept"
    )
    agent_def = AgentDefinition(
        type=AgentType.BUILDER, system_prompt="x", tools=["tA", "boom", "tC"]
    )
    run = await AgentRuntime(platform).run(session, agent_def)

    # Both healthy siblings still executed despite the middle one blowing up.
    assert set(completions) == {"tA", "tC"}
    assert run.state is AgentState.COMPLETED

    tool_msgs = [m for m in adapter.seen[1] if m.role == "tool"]
    assert [m.name for m in tool_msgs] == ["tA", "boom", "tC"]
    assert tool_msgs[0].content == "done:tA"
    assert "kaboom" in tool_msgs[1].content  # the failure surfaced as its result
    assert tool_msgs[2].content == "done:tC"


async def test_concurrent_transcript_is_deterministic(platform_factory):
    """Same scripted input -> identical ordered tool-result transcript."""

    async def run_once() -> list[tuple[str, str]]:
        p = platform_factory()
        adapter = await _run_three_calls(p, [])
        tool_msgs = [m for m in adapter.seen[1] if m.role == "tool"]
        return [(m.name, m.content) for m in tool_msgs]

    assert await run_once() == await run_once()


# --- 2. blackboard -------------------------------------------------------


def test_blackboard_post_read_roundtrip_and_scoping(platform):
    store = BlackboardStore(platform.engine)
    store.post("boardA", "agent1", "finding one")
    store.post("boardA", "agent2", "finding two")
    store.post("boardB", "agent9", "other team note")

    a = store.list("boardA")
    assert [r.text for r in a] == ["finding one", "finding two"]
    # Scoping: board B's note never appears on board A and vice-versa.
    assert all(r.board_id == "boardA" for r in a)
    b = store.list("boardB")
    assert [r.text for r in b] == ["other team note"]


def test_resolve_board_id_walks_to_root_session(platform):
    """A supervisor (root) and its child sub-agents resolve to ONE board id."""
    with session_scope(platform.engine) as db:
        root = AgentRun(session_id="sess-root", parent_id=None)
        db.add(root)
        db.commit()
        db.refresh(root)
        child = AgentRun(session_id="sess-child", parent_id=root.id)
        grandchild = AgentRun(session_id="sess-gc", parent_id=None)  # set below
        db.add(child)
        db.commit()
        db.refresh(child)
        grandchild.parent_id = child.id
        db.add(grandchild)
        db.commit()
        db.refresh(grandchild)
        root_id, child_id, gc_id = root.id, child.id, grandchild.id

    # Every descendant resolves to the ROOT run's session id (the department).
    assert resolve_board_id(platform.engine, "sess-root", root_id) == "sess-root"
    assert resolve_board_id(platform.engine, "sess-child", child_id) == "sess-root"
    assert resolve_board_id(platform.engine, "sess-gc", gc_id) == "sess-root"
    # Unknown run id falls back to its own session (never a shared/global board).
    assert resolve_board_id(platform.engine, "sess-x", "nope") == "sess-x"


async def test_message_agent_targets_a_sibling(platform):
    """Two sibling sub-agents of one supervisor share a board; a directed message
    is readable by the addressed sibling (to_me) and isolated from other boards."""
    _allow(platform, ["message_agent", "blackboard_read"])
    with session_scope(platform.engine) as db:
        root = AgentRun(session_id="dept-root", parent_id=None)
        db.add(root)
        db.commit()
        db.refresh(root)
        sib1 = AgentRun(session_id="sib1-sess", parent_id=root.id)
        sib2 = AgentRun(session_id="sib2-sess", parent_id=root.id)
        db.add(sib1)
        db.add(sib2)
        db.commit()
        db.refresh(sib1)
        db.refresh(sib2)
        sib1_id, sib2_id = sib1.id, sib2.id

    def ctx_for(run_id: str, session_id: str) -> ToolContext:
        return ToolContext(
            workspace=platform.config.workspaces_dir,
            session_id=session_id,
            agent_run_id=run_id,
            config=platform.config,
            event_bus=platform.event_bus,
            engine=platform.engine,
        )

    # sib1 messages sib2 (addressing it by its run id).
    res = await platform.registry.invoke(
        "message_agent",
        {"to_agent": sib2_id, "text": "please double-check the totals"},
        ctx_for(sib1_id, "sib1-sess"),
        platform.permissions,
    )
    assert res.ok and res.data["board_id"] == "dept-root"

    # sib2 reads only messages addressed to it and sees sib1's message.
    read = await platform.registry.invoke(
        "blackboard_read",
        {"to_me": True},
        ctx_for(sib2_id, "sib2-sess"),
        platform.permissions,
    )
    recs = read.data["records"]
    assert len(recs) == 1
    assert recs[0]["text"] == "please double-check the totals"
    assert recs[0]["to_agent"] == sib2_id
    assert recs[0]["kind"] == BlackboardKind.MESSAGE.value

    # Scoping: a record on the department board never appears on another board.
    assert BlackboardStore(platform.engine).list("some-other-dept") == []


@pytest.fixture
def platform_factory(tmp_path):
    from iron_jarvis.platform import build_platform

    counter = {"n": 0}

    def make():
        counter["n"] += 1
        return build_platform(str(tmp_path / f"p{counter['n']}"))

    return make


# --- swarm-review fixes: roster discovery + delete cascade ------------------
def test_blackboard_roster_lists_distinct_authors(tmp_path):
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    p.blackboard.post("board1", "runA", "found X")
    p.blackboard.post("board1", "runA", "more")
    p.blackboard.post("board1", "runB", "found Y")
    roster = {r["agent_run_id"]: r["posts"] for r in p.blackboard.roster("board1")}
    assert roster == {"runA": 2, "runB": 1}


async def test_delete_session_cascades_blackboard(tmp_path):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)
    sess = await orch.run("hello", AgentType.BUILDER)
    p.blackboard.post(sess.id, "run1", "team note")
    assert len(p.blackboard.list(sess.id)) == 1
    orch.delete_session(sess.id)
    assert p.blackboard.list(sess.id) == []
