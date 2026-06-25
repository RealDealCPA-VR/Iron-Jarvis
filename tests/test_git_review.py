"""Tests for the git-native session + review system (§27, §28).

Offline: drives the real ``git`` binary inside ``tmp_path``. No network, no
provider, no daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from iron_jarvis.git.integration import GitSession, branch_name
from iron_jarvis.git.review import (
    ReviewRequest,
    approve,
    build_review,
    export_patch,
    reject,
    risk_assess,
)


def _run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


def make_repo(path: Path) -> str:
    """Create a project repo with a committed README.md; return the base branch."""
    path.mkdir(parents=True, exist_ok=True)
    _run(["init"], path)
    _run(["config", "user.email", "test@example.com"], path)
    _run(["config", "user.name", "Test User"], path)
    (path / "README.md").write_text("hello")
    _run(["add", "-A"], path)
    _run(["commit", "-m", "base"], path)
    return _run(["rev-parse", "--abbrev-ref", "HEAD"], path).strip()


def test_branch_name_format() -> None:
    assert (
        branch_name("Add Feature!!", ts="20260625-000000")
        == "ironjarvis/session-20260625-000000-add-feature"
    )
    # auto timestamp still produces the canonical prefix
    assert branch_name("x").startswith("ironjarvis/session-")


def test_start_creates_branch_and_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    base = make_repo(repo)

    gs = GitSession.start(repo, tmp_path / "ws", "add-feature")

    assert gs.branch.startswith("ironjarvis/session-")
    assert gs.base == base
    ws = tmp_path / "ws"
    assert ws.is_dir()
    assert (ws / "README.md").read_text() == "hello"


def test_changed_files_and_diff(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    make_repo(repo)
    gs = GitSession.start(repo, tmp_path / "ws", "edit")

    (gs.workspace / "README.md").write_text("hello world")
    (gs.workspace / "feature.txt").write_text("brand new file\n")

    changed = gs.changed_files()
    assert "README.md" in changed
    assert "feature.txt" in changed

    diff = gs.diff()
    assert "world" in diff
    assert "feature.txt" in diff


def test_build_review_does_not_touch_base(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    base = make_repo(repo)
    gs = GitSession.start(repo, tmp_path / "ws", "feat")

    (gs.workspace / "README.md").write_text("hello world")
    (gs.workspace / "feature.txt").write_text("data")

    review = build_review(gs, session_id="session_abc", summary="did stuff")

    assert isinstance(review, ReviewRequest)
    assert review.changed_files  # non-empty
    assert review.diff  # non-empty
    assert review.risk in {"low", "medium", "high"}
    assert review.branch == gs.branch
    assert review.base == base
    assert export_patch(review) == review.diff

    # Building the review must NOT have merged anything: the base branch and the
    # main checkout still hold the original content.
    assert _run(["show", f"{base}:README.md"], repo) == "hello"
    assert (repo / "README.md").read_text() == "hello"


def test_risk_assess_levels() -> None:
    assert risk_assess([], "") == "low"
    assert risk_assess([f"f{i}.txt" for i in range(4)], "") == "medium"
    assert risk_assess([f"f{i}.txt" for i in range(11)], "") == "high"
    big_delete = "\n".join("-gone" for _ in range(60))
    assert risk_assess(["one.txt"], big_delete) == "high"


def test_reject_removes_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    make_repo(repo)
    gs = GitSession.start(repo, tmp_path / "ws", "scrap")

    (gs.workspace / "junk.txt").write_text("junk")
    review = build_review(gs, session_id="session_x", summary="scrap work")

    reject(review, gs)

    assert not gs.workspace.exists()
    branches = _run(["branch", "--list", gs.branch], repo)
    assert gs.branch not in branches
    # base untouched
    assert (repo / "README.md").read_text() == "hello"


def test_approve_merges_only_after_approve(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    base = make_repo(repo)
    gs = GitSession.start(repo, tmp_path / "ws", "feat")

    (gs.workspace / "README.md").write_text("hello world")
    review = build_review(gs, session_id="session_y", summary="add world")

    # Pre-approval: the change has NOT landed on base.
    assert (repo / "README.md").read_text() == "hello"
    assert _run(["show", f"{base}:README.md"], repo) == "hello"

    out = approve(review, gs)
    assert isinstance(out, str)

    # Post-approval: the change is now on the base branch / main checkout.
    assert "world" in _run(["show", f"{base}:README.md"], repo)
    assert (repo / "README.md").read_text() == "hello world"
