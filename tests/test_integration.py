"""Cross-subsystem integration — proves the central wiring is connected.

These exercise the WIRED platform (not the modules in isolation): the auto
evaluation hook, supervised delegation through the Orchestrator, git-native
review flow, registered tools, and the workflow engine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlmodel import select

import iron_jarvis.workflows.models  # noqa: F401  (register table for build_platform)
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine


async def test_orchestrator_auto_evaluates_each_session(tmp_path):
    p = build_platform(str(tmp_path))
    session = await Orchestrator(p).run("create a summary file", AgentType.BUILDER)
    ev = p.evaluator.latest(session.id)
    assert ev is not None  # eval hook ran inside run_session
    assert ev.completion == 1.0
    assert ev.tool_calls >= 1
    assert 0.0 <= ev.tool_success_rate <= 1.0


async def test_supervised_session_delegates_to_subagent(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine({**p.config.permissions, "delegate": "allow"})
    p.providers.register(
        "super",
        lambda: MockLLMAdapter(
            script=[
                LLMResponse(
                    tool_calls=[
                        ToolCall("d1", "delegate", {"agent_type": "builder", "task": "write a file"})
                    ],
                    finish_reason="tool_use",
                ),
                LLMResponse(text="All subtasks complete.", finish_reason="stop"),
            ]
        ),
    )
    session = await Orchestrator(p).run("Coordinate the build", AgentType.SUPERVISOR, provider="super")
    assert session.status is SessionStatus.COMPLETED

    with session_scope(p.engine) as db:
        runs = list(db.exec(select(AgentRun)))
    supervisor = [r for r in runs if r.session_id == session.id]
    assert supervisor and supervisor[0].state is AgentState.COMPLETED
    children = [r for r in runs if r.parent_id == supervisor[0].id]
    assert children, "supervisor should have spawned a child subagent run"
    assert children[0].state is AgentState.COMPLETED


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)

    def g(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    g("init", "-q")
    (repo / ".gitignore").write_text(".ironjarvis/\n", encoding="utf-8")
    (repo / "README.md").write_text("hello", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "base")


async def test_git_native_review_flow_no_auto_merge(tmp_path):
    repo = tmp_path / "proj"
    _init_git_repo(repo)
    p = build_platform(str(repo))
    p.config.git_native = True
    orch = Orchestrator(p)

    session = await orch.run("create a summary file", AgentType.BUILDER)
    review = orch.get_review(session.id)
    assert review is not None, "git-native session must produce a review"
    assert any("RESULT.md" in f for f in review.changed_files)
    assert review.branch.startswith("ironjarvis/session-")
    assert review.risk in {"low", "medium", "high"}

    # No auto-merge: base is untouched until explicit approval.
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello"
    assert not (repo / "RESULT.md").exists()

    orch.approve_review(session.id)
    assert (repo / "RESULT.md").exists()  # merged on approval


async def test_memory_and_skill_tools_are_wired(tmp_path):
    p = build_platform(str(tmp_path))
    names = set(p.registry.names())
    assert {"memory_search", "memory_write", "skill_search", "skill_load", "delegate", "shell"} <= names

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws,
        session_id="s",
        agent_run_id="r",
        config=p.config,
        event_bus=p.event_bus,
        engine=p.engine,
    )
    await p.registry.invoke(
        "memory_write",
        {"layer": "project", "key": "k1", "text": "docker containers and images"},
        ctx,
        p.permissions,
    )
    hit = await p.registry.invoke("memory_search", {"query": "docker"}, ctx, p.permissions)
    assert hit.ok and "docker" in hit.output.lower()

    sk = await p.registry.invoke("skill_search", {"query": "research"}, ctx, p.permissions)
    assert sk.ok and "research" in sk.output.lower()


async def test_workflow_engine_runs_on_wired_platform(tmp_path):
    p = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="bookkeeping",
        steps=[
            Step(name="s1", agent="builder", task="create a file"),
            Step(name="s2", agent="builder", task="create another file"),
        ],
    )
    rec = await WorkflowEngine(p).run(wf)
    assert rec.status == "completed"
