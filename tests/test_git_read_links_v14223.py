"""git_read never reads THROUGH a link that replaces a tracked folder (v1.422.3).

The v1.422.2 review measured it: a JUNCTION that replaces a folder git tracks
is followed by a plain ``git grep``, ``diff``, ``blame`` and ``status`` (git
sees a directory), so text from outside the working folder came back through
the team reviewer's read-only git. ``git_read._linked_folder`` now lists the
tracked paths and refuses the call when any folder holding one is itself a
link — checked shallowest first, so nothing deeper is ever reached THROUGH a
link (an ``lstat`` follows every part but the last). Junctions in folders git
does not track (``node_modules`` made by pnpm) never matter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.harness import git_read as G
from iron_jarvis.tools.base import ToolContext

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, timeout=60,
    )


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


async def _read(repo: Path, command: str, args: list[str]):
    return await G.GitReadTool().execute({"command": command, "args": args}, _ctx(repo))


def _junction(link: Path, target: Path) -> None:
    got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, timeout=30)
    assert got.returncode == 0, got.stderr


@pytest.fixture()
def world(tmp_path, monkeypatch):
    empty = tmp_path / "global.cfg"
    empty.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_bytes(b"OUTSIDE-SECRET-TEXT\n")
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "notes.txt").write_bytes(b"inside text\n")
    (repo / "app.py").write_bytes(b"X = 1\n")
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    yield repo, outside
    for link in (repo / "docs", repo / "node_modules" / "pkg"):
        if link.exists() and G._is_link(str(link)):
            os.rmdir(link)  # remove the junction itself, never its target


CALLS = [("grep", ["-n", "OUTSIDE"]), ("diff", []), ("status", ["--short"]), ("blame", ["docs/notes.txt"]),
         ("log", ["--oneline", "-n", "1"])]


@win
@pytest.mark.parametrize("command,args", CALLS)
async def test_a_junction_replacing_a_tracked_folder_runs_nothing(world, command, args):
    repo, outside = world
    shutil.rmtree(repo / "docs")
    _junction(repo / "docs", outside)
    got = await _read(repo, command, args)
    assert not got.ok and "is a link to another place" in got.error and "docs" in got.error, got
    assert "OUTSIDE-SECRET-TEXT" not in (got.output or "") + (got.error or "")


@win
async def test_control_the_same_folder_as_a_real_folder_reads(world):
    repo, _outside = world
    got = await _read(repo, "grep", ["-n", "inside"])
    assert got.ok and "docs/notes.txt" in got.output, got


@win
async def test_a_junction_in_an_untracked_folder_is_not_a_reason_to_refuse(world):
    """pnpm's node_modules is full of links; git never reads an untracked one."""
    repo, outside = world
    (repo / "node_modules").mkdir()
    _junction(repo / "node_modules" / "pkg", outside)
    got = await _read(repo, "log", ["--oneline", "-n", "1"])
    assert got.ok, got


@win
def test_the_check_never_reaches_a_path_through_a_link(world, monkeypatch):
    """Shallowest first, stop at the first link: nothing under the linked
    folder is ever looked up (an lstat there would follow the junction)."""
    repo, outside = world
    (outside / "deep").mkdir()
    (repo / "docs" / "deep").mkdir()
    (repo / "docs" / "deep" / "x.txt").write_bytes(b"x\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "deep").returncode == 0
    shutil.rmtree(repo / "docs")
    _junction(repo / "docs", outside)
    seen: list[str] = []
    real = os.lstat

    def spy(p, *a, **k):
        seen.append(os.path.normcase(str(p)))
        return real(p, *a, **k)

    monkeypatch.setattr(G.os, "lstat", spy)
    sentence = G._linked_folder(repo, [], G.git_read_env())
    assert "docs" in sentence
    under = os.path.normcase(str(repo / "docs")) + os.sep
    assert not [p for p in seen if p.startswith(under)], seen


def _stub_git(monkeypatch, listing: str, *, timed_out: bool = False, top: str = ""):
    from types import SimpleNamespace

    from iron_jarvis.git import integration as I

    def fake(argv, cwd, **kw):
        if "--show-toplevel" in argv:
            return SimpleNamespace(code=0, out=(top or str(cwd)) + "\n", err="", timed_out=False)
        return SimpleNamespace(code=0, out=listing, err="", timed_out=timed_out)

    monkeypatch.setattr(I, "run_git", fake)


def test_an_entry_git_tracks_as_a_symlink_is_not_a_folder_to_check(tmp_path, monkeypatch):
    """mode 120000: git does not follow it; its PARENTS are still checked."""
    (tmp_path / "real").mkdir()
    looked: list[str] = []
    monkeypatch.setattr(G, "_is_link", lambda p: looked.append(p.replace(os.sep, "/")) or False)
    _stub_git(monkeypatch, "120000 abc 0\tlinkdir/inner\0" + "100644 abc 0\treal/a.txt\0")
    assert G._linked_folder(tmp_path, [], {}) == ""
    assert [p for p in looked if p.endswith("/real")] and not [p for p in looked if "linkdir" in p]


def test_a_listing_that_does_not_finish_runs_nothing(tmp_path, monkeypatch):
    _stub_git(monkeypatch, "", timed_out=True)
    assert G._linked_folder(tmp_path, [], {}) == G.LINK_CHECK_UNSURE


def test_outside_a_repository_the_check_leaves_it_to_git(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.git import integration as I

    monkeypatch.setattr(I, "run_git", lambda argv, cwd, **kw: SimpleNamespace(
        code=128, out="", err="fatal: not a git repository", timed_out=False))
    assert G._linked_folder(tmp_path, [], {}) == ""
