"""North-star plan 4.6: a run cut off by a restart keeps its branch and folder.

With ``git_native`` (and self-development) a run works in its own copy of the
repository on its own branch. ``prune_orphan_worktrees`` ran at every start
right after ``reconcile_interrupted_sessions`` had marked a cut-off run FAILED
and offered Continue, and it deleted the folder (``worktree remove --force``)
AND the branch (``branch -D``): everything the run wrote, committed or not.
Now a cut-off or resumable run is never touched, and for any other ended run
the folder moves to the trash and the branch is kept.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, RunTranscript, Session, SessionStatus
from iron_jarvis.git.integration import list_session_worktrees
from iron_jarvis.platform import build_platform


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _ok(args, cwd) -> str:
    proc = _git(args, cwd)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _ok(["init"], path)
    _ok(["config", "user.email", "t@t"], path)
    _ok(["config", "user.name", "t"], path)
    (path / "pyproject.toml").write_text('[project]\nname = "iron-jarvis"\n')
    (path / "src").mkdir()
    (path / "src" / "thing.py").write_text("VALUE = 1\n")
    _ok(["add", "-A"], path)
    _ok(["commit", "-m", "base"], path)
    return path.resolve()


async def _run_with_work(tmp_path: Path):
    repo = _repo(tmp_path / "ijrepo")
    platform = build_platform(str(tmp_path / "proj"))
    platform.config.self_dev_enabled = True
    platform.config.self_dev_root = str(repo)
    orch = Orchestrator(platform)
    session = await orch.create_session("keep my work", self_dev=True)
    gs = orch._git_sessions[session.id]
    ws = Path(gs.workspace)
    (ws / "src" / "thing.py").write_text("VALUE = 2\n")
    _ok(["add", "-A"], ws)
    _ok(["commit", "-m", "the run's commit"], ws)
    (ws / "notes.txt").write_text("not committed yet", encoding="utf-8")
    # A restart: the in-memory state is gone.
    orch._git_sessions.clear()
    orch._reviews.clear()
    return platform, orch, repo, session.id, gs.branch, ws


def _set_status(platform, sid: str, status: SessionStatus) -> None:
    with session_scope(platform.engine) as db:
        s = db.get(Session, sid)
        s.status = status
        db.add(s)
        db.commit()


def _branch_exists(repo: Path, branch: str) -> bool:
    return _git(["rev-parse", "--verify", branch], repo).returncode == 0


async def test_a_run_a_restart_cut_off_keeps_its_folder_and_branch(tmp_path):
    platform, orch, repo, sid, branch, ws = await _run_with_work(tmp_path)
    _set_status(platform, sid, SessionStatus.ACTIVE)

    # The boot order: the reconcile marks it FAILED (and offers Continue)…
    assert orch.reconcile_interrupted_sessions() >= 1
    assert orch.get_session(sid).status == SessionStatus.FAILED
    # …then the prune runs.
    pruned = orch.prune_orphan_worktrees()

    assert branch not in pruned
    assert any(b == branch for _, b in list_session_worktrees(repo))
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "not committed yet"
    assert _branch_exists(repo, branch)


async def test_a_run_continue_can_resume_is_never_pruned(tmp_path):
    platform, orch, repo, sid, branch, ws = await _run_with_work(tmp_path)
    _set_status(platform, sid, SessionStatus.FAILED)
    with session_scope(platform.engine) as db:
        run = AgentRun(session_id=sid)
        db.add(run)
        db.add(RunTranscript(run_id=run.id, messages_json=json.dumps([{"role": "user", "content": "go"}])))
        db.commit()

    pruned = orch.prune_orphan_worktrees()

    assert branch not in pruned
    assert (ws / "notes.txt").is_file()


async def test_another_ended_run_keeps_its_branch_and_its_folder_moves_to_the_trash(tmp_path):
    platform, orch, repo, sid, branch, ws = await _run_with_work(tmp_path)
    _set_status(platform, sid, SessionStatus.FAILED)

    pruned = orch.prune_orphan_worktrees()

    assert branch in pruned
    assert not any(b == branch for _, b in list_session_worktrees(repo))
    # The BRANCH and its commit survive.
    assert _branch_exists(repo, branch)
    assert "the run's commit" in _ok(["log", "--format=%s", branch], repo)
    # The folder (with what was not committed) is in the trash, not deleted.
    home = Path(platform.config.home)
    moved = list((home / "trash").rglob("notes.txt"))
    assert [p.read_text(encoding="utf-8") for p in moved] == ["not committed yet"]
    # …and what was moved is recorded beside it.
    note = json.loads((moved[0].parent.parent / "removed.json").read_text(encoding="utf-8"))
    assert note["session_id"] == sid and note["branch_kept"] == branch
    assert Path(note["folder"]) == ws


async def test_a_coding_runs_delete_keeps_its_branch(tmp_path):
    platform, orch, repo, sid, branch, ws = await _run_with_work(tmp_path)
    # Put the live state back: the run is open, then deleted.
    from iron_jarvis.git.integration import GitSession

    orch._git_sessions[sid] = GitSession(repo=repo, workspace=ws, branch=branch, base="master")
    _set_status(platform, sid, SessionStatus.COMPLETED)

    out = orch.delete_session(sid)

    assert out["branch"] == branch
    assert _branch_exists(repo, branch)
    assert (Path(out["files"]) / "notes.txt").read_text(encoding="utf-8") == "not committed yet"
    assert not ws.exists()
