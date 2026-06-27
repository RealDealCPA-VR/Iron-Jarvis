"""Repo-based self-update (git) — offline, fully dependency-injected.

A fake ``runner`` stands in for the real git/uv/pnpm commands, so these tests
exercise the whole update surface with NO real git, subprocess, or network.
"""

from __future__ import annotations

from iron_jarvis.core.updates import RunResult, apply_update, update_status


def make_runner(porcelain: str = "", behind: str = "3", fail: set[str] | None = None):
    """A fake command runner. Records every argv it sees in ``runner.calls``.

    *porcelain* is what ``git status --porcelain`` returns (non-empty = dirty),
    *behind* is the ``git rev-list --count`` output, and *fail* is a set of
    command prefixes (joined argv) that should return a non-zero exit code.
    """
    fail = fail or set()
    calls: list[list[str]] = []

    def runner(cmd: list[str], cwd) -> RunResult:
        calls.append(list(cmd))
        j = " ".join(cmd)
        for prefix in fail:
            if j.startswith(prefix):
                return RunResult(1, "", f"boom: {prefix}")
        if j.startswith("git fetch"):
            return RunResult(0, "", "")
        if "rev-list" in j:
            return RunResult(0, behind + "\n", "")
        if "rev-parse --abbrev-ref" in j:
            return RunResult(0, "main\n", "")
        if "rev-parse --short HEAD" in j:
            return RunResult(0, "aaaaaaa\n", "")
        if "rev-parse --short @{u}" in j:
            return RunResult(0, "bbbbbbb\n", "")
        if j.startswith("git status"):
            return RunResult(0, porcelain, "")
        if j.startswith("git pull"):
            return RunResult(0, "Updating aaaaaaa..bbbbbbb\n", "")
        if j.startswith("uv sync"):
            return RunResult(0, "Resolved deps\n", "")
        return RunResult(0, "", "")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_update_status_available_when_behind(tmp_path):
    runner = make_runner(porcelain="", behind="3")
    st = update_status(tmp_path, runner=runner)
    assert st["behind"] == 3
    assert st["available"] is True
    assert st["clean"] is True
    assert st["branch"] == "main"
    assert st["current"] == "aaaaaaa"
    assert st["remote"] == "bbbbbbb"


def test_update_status_up_to_date(tmp_path):
    st = update_status(tmp_path, runner=make_runner(behind="0"))
    assert st["behind"] == 0
    assert st["available"] is False
    assert st["reason"] == "up to date"


def test_update_status_dirty_not_available(tmp_path):
    # Upstream HAS commits (behind=3) but the tree is dirty -> not applyable.
    runner = make_runner(porcelain=" M src/thing.py\n", behind="3")
    st = update_status(tmp_path, runner=runner)
    assert st["clean"] is False
    assert st["available"] is False
    assert "uncommitted" in st["reason"]


def test_update_status_never_raises_on_no_upstream(tmp_path):
    # No upstream tracking branch -> rev-list fails; status must degrade, not raise.
    runner = make_runner(fail={"git rev-list"})
    st = update_status(tmp_path, runner=runner)
    assert st["available"] is False
    assert "upstream" in st["reason"]


def test_apply_refuses_on_dirty_tree(tmp_path):
    runner = make_runner(porcelain=" M src/thing.py\n")
    res = apply_update(tmp_path, runner=runner)
    assert res["ok"] is False
    assert "uncommitted" in res["reason"]
    # It must NOT have pulled over the dirty tree.
    assert not any(c[:2] == ["git", "pull"] for c in runner.calls)


def test_apply_runs_pull_and_sync_on_clean_tree(tmp_path):
    runner = make_runner(porcelain="")  # clean
    # No dashboard/ dir in tmp_path, so the pnpm build step is skipped.
    res = apply_update(tmp_path, runner=runner)
    assert res["ok"] is True
    assert res["restart_required"] is True
    # The exact commands were invoked, in order.
    assert ["git", "pull", "--ff-only"] in runner.calls
    assert ["uv", "sync", "--extra", "dev"] in runner.calls
    steps = [e["step"] for e in res["log"]]
    assert "git pull --ff-only" in steps
    assert "uv sync --extra dev" in steps


def test_apply_stops_and_reports_when_pull_fails(tmp_path):
    runner = make_runner(porcelain="", fail={"git pull"})
    res = apply_update(tmp_path, runner=runner)
    assert res["ok"] is False
    assert res["restart_required"] is True
    assert "pull" in res["reason"]
    # uv sync must not run after a failed pull.
    assert not any(c[:2] == ["uv", "sync"] for c in runner.calls)
