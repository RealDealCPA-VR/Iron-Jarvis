"""Multi-agent orchestration tests (§12).

A Supervisor decomposes a task and delegates to subagents which run with
isolated context and return summarized results. These tests are fully offline
and deterministic.

Pitfall avoided: ``ProviderManager`` caches ONE adapter per provider name, so a
single scripted "mock" provider would be shared (and consumed) by BOTH the
supervisor and its subagent. We register a SEPARATE scripted provider ("super")
for the supervisor and let subagents use the default scriptless "mock".
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.supervisor import run_supervised
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    # Wire the delegate tool and allow it (+ write_file) for the test run.
    p.registry.register(DelegateTool(p))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "delegate": "allow", "write_file": "allow"}
    )
    return p


async def test_supervisor_delegates_to_subagent(platform):
    # A scripted supervisor: delegate one subtask, then summarize and stop.
    platform.providers.register(
        "super",
        lambda: MockLLMAdapter(
            script=[
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            "d1",
                            "delegate",
                            {"agent_type": "builder", "task": "write a summary file"},
                        )
                    ],
                    finish_reason="tool_use",
                ),
                LLMResponse(
                    text="Delegated and completed all subtasks.",
                    finish_reason="stop",
                ),
            ]
        ),
    )

    sess = await Orchestrator(platform).create_session(
        "Build a thing", AgentType.SUPERVISOR, provider="super"
    )
    sup_run = await run_supervised(platform, sess)

    # Supervisor completed.
    assert sup_run.state == AgentState.COMPLETED
    assert sup_run.agent_type is AgentType.SUPERVISOR
    assert "subtask" in sup_run.result.lower() or "completed" in sup_run.result.lower()

    # A CHILD run exists (session-independent query), linked by parent_id, and
    # it completed on the default offline "mock" provider.
    with session_scope(platform.engine) as db:
        all_runs = list(db.exec(select(AgentRun)))
    children = [r for r in all_runs if r.parent_id == sup_run.id]
    assert children, "expected at least one delegated child run"
    child = children[0]
    assert child.state == AgentState.COMPLETED
    assert child.provider == "mock"
    assert child.session_id != sess.id  # subagent ran in its own session


async def test_delegate_tool_runs_subagent_directly(platform, tmp_path):
    # Invoke the delegate tool directly through the registry (the path the
    # runtime uses), with a parent agent_run_id, and assert a linked child run
    # plus the artifact the default mock subagent produces.
    ctx = ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id="parent1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )

    result = await platform.registry.invoke(
        "delegate",
        {"agent_type": "builder", "task": "x"},
        ctx,
        platform.permissions,
    )

    assert result.ok
    child_session_id = result.data["child_session_id"]

    with session_scope(platform.engine) as db:
        children = list(
            db.exec(select(AgentRun).where(AgentRun.parent_id == "parent1"))
        )
    assert children, "delegate should create a child AgentRun with parent_id=parent1"
    assert children[0].state == AgentState.COMPLETED

    # The subagent worked in its own isolated workspace and left RESULT.md.
    workspace = platform.config.workspaces_dir / child_session_id
    result_md = workspace / "RESULT.md"
    assert result_md.exists()
    assert "Iron Jarvis" in result_md.read_text(encoding="utf-8")
