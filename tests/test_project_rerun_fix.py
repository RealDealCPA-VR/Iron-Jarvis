"""Rerun of a project in-folder task: preserve the direct root + tool grant,
and keep the environment prompt honest about WHERE file tools operate.

Two confirmed bugs in repetitive project work:
  1. ``rerun_session`` cloned only task/agent/provider/model — dropping the
     ``workspace_root`` and ``allow_tools`` a project file-task was created
     with, so the rerun's deliverable landed in a throwaway scratch workspace
     and its pre-approved tools failed closed.
  2. The runtime unconditionally injected "file tools operate in a SCRATCH
     workspace" — contradicting in-folder tasks whose task text says "you are
     working directly inside the project folder".
"""

from __future__ import annotations

import json
from pathlib import Path

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import is_direct_workspace
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Project
from iron_jarvis.providers.adapters.mock import MockLLMAdapter


class CapturingMock(MockLLMAdapter):
    """A mock that records the system prompt it was called with."""

    def __init__(self, box: dict):
        super().__init__()
        self._box = box

    async def complete(self, **kw):
        self._box["system"] = kw.get("system", "")
        return await super().complete(**kw)


def _make_project(platform, root: Path) -> str:
    proj = Project(name="Recurring", root=str(root))
    pid = proj.id
    with session_scope(platform.engine) as db:
        db.add(proj)
        db.commit()
    return pid


# --- fix 1: rerun preserves the direct root + the up-front tool grant --------


async def test_rerun_preserves_direct_root_and_tool_grant(platform, tmp_path):
    folder = tmp_path / "client-folder"
    folder.mkdir()
    pid = _make_project(platform, folder)
    orch = Orchestrator(platform)
    s1 = await orch.create_session(
        "weekly report",
        AgentType.BUILDER,
        project_id=pid,
        allow_tools=["shell.exec", "write_document"],
        workspace_root=str(folder),
    )
    await orch.run_session(s1.id)

    s2 = await orch.rerun_session(s1.id)
    assert s2.id != s1.id
    # The rerun runs IN the project folder again — not a scratch workspace.
    assert Path(s2.workspace_path).resolve() == folder.resolve()
    assert not (platform.config.workspaces_dir / s2.id).exists()
    # The bundle-approved tool grant carries over (would fail closed otherwise).
    assert json.loads(s2.allow_tools_json) == ["shell.exec", "write_document"]
    assert s2.project_id == pid


async def test_rerun_follows_a_moved_project_root(platform, tmp_path):
    """The project's CURRENT root wins over the stale stored workspace."""
    old = tmp_path / "old-root"
    old.mkdir()
    pid = _make_project(platform, old)
    orch = Orchestrator(platform)
    s1 = await orch.create_session(
        "task", AgentType.BUILDER, project_id=pid, workspace_root=str(old)
    )
    new = tmp_path / "new-root"
    new.mkdir()
    with session_scope(platform.engine) as db:
        proj = db.get(Project, pid)
        proj.root = str(new)
        db.add(proj)
        db.commit()

    s2 = await orch.rerun_session(s1.id)
    assert Path(s2.workspace_path).resolve() == new.resolve()


async def test_rerun_direct_root_without_project_row_falls_back(platform, tmp_path):
    """A direct-root session whose project row is gone (or that never had one)
    still reruns in its original folder — the stored path IS the honest signal."""
    folder = tmp_path / "loose-folder"
    folder.mkdir()
    orch = Orchestrator(platform)
    s1 = await orch.create_session("task", AgentType.BUILDER, workspace_root=str(folder))
    s2 = await orch.rerun_session(s1.id)
    assert Path(s2.workspace_path).resolve() == folder.resolve()


async def test_rerun_of_scratch_session_stays_scratch(platform):
    orch = Orchestrator(platform)
    s1 = await orch.run("plain task", AgentType.BUILDER)
    s2 = await orch.rerun_session(s1.id)
    assert s2.workspace_path != s1.workspace_path  # a FRESH scratch dir
    assert Path(s2.workspace_path).resolve().parent == platform.config.workspaces_dir.resolve()
    assert json.loads(s2.allow_tools_json) == []  # no grant invented


# --- fix 2: the environment prompt tells the truth about the workspace -------


async def test_prompt_says_project_folder_for_direct_session(platform, tmp_path):
    box: dict = {}
    platform.providers.register("cap", lambda: CapturingMock(box))
    folder = tmp_path / "proj-root"
    folder.mkdir()
    orch = Orchestrator(platform)
    sess = await orch.create_session(
        "in-folder task", AgentType.BUILDER, provider="cap", workspace_root=str(folder)
    )
    await orch.run_session(sess.id)
    assert "directly in the project folder" in box["system"]
    assert str(folder) in box["system"]  # the REAL path, spelled out
    assert "SCRATCH" not in box["system"]  # no contradiction with the task text


async def test_prompt_says_scratch_for_plain_session(platform):
    box: dict = {}
    platform.providers.register("cap2", lambda: CapturingMock(box))
    orch = Orchestrator(platform)
    sess = await orch.create_session("plain task", AgentType.BUILDER, provider="cap2")
    await orch.run_session(sess.id)
    assert "SCRATCH workspace" in box["system"]
    assert "directly in the project folder" not in box["system"]


# --- the shared signal itself ------------------------------------------------


def test_is_direct_workspace_signal(platform, tmp_path):
    managed = platform.config.workspaces_dir
    assert is_direct_workspace(platform.config, str(tmp_path / "user-folder"))
    assert not is_direct_workspace(platform.config, str(managed / "session-abc"))
    assert not is_direct_workspace(platform.config, str(managed))  # the dir itself
    assert not is_direct_workspace(platform.config, "")
    assert not is_direct_workspace(platform.config, None)
