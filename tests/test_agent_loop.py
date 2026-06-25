from __future__ import annotations

from pathlib import Path

from iron_jarvis.core.models import AgentState, AgentType, SessionStatus


async def test_single_agent_loop_end_to_end(orchestrator):
    session = await orchestrator.run(
        "Create a file summarizing the task.", AgentType.BUILDER
    )

    # Session completed and an artifact landed in the isolated workspace.
    assert session.status is SessionStatus.COMPLETED
    result = Path(session.workspace_path) / "RESULT.md"
    assert result.exists()
    assert "Iron Jarvis" in result.read_text(encoding="utf-8")

    transcript = orchestrator.transcript(session.id)
    assert any(t["tool"] == "write_file" and t["ok"] for t in transcript["tools"])
    assert transcript["runs"][0]["state"] == AgentState.COMPLETED
    assert transcript["runs"][0]["steps"] >= 1


async def test_workspaces_are_isolated(orchestrator):
    s1 = await orchestrator.run("task one", AgentType.BUILDER)
    s2 = await orchestrator.run("task two", AgentType.BUILDER)
    assert s1.workspace_path != s2.workspace_path
