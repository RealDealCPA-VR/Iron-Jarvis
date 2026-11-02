"""v1.422.3 REVIEW of git_read's link check (``git_read._linked_folder``).

Each pin was RED on the first cut (or on the mutant named), measured with real
junctions and git 2.50 on Windows:

1. a junction DEEPER than the top level (``src/pkg``), with the call made at
   the top and from below it — the first cut's pins only ever linked a
   top-level folder, so a check of top-level folders alone stayed green;
2. a tracked name no Windows folder can hold (``what?/x.txt`` — a repository
   from Linux under ``core.protectNTFS=false``, or a sparse checkout, keeps
   such entries in its index): ``lstat`` raised ``[WinError 123]`` and EVERY
   git_read call in that repository failed with the raw ``OSError`` (a plain
   ``git log`` worked in v1.422.2) — nothing can be on disk there, so it is
   not a link;
3. the check had a bound for LISTING the tracked files only: the ``lstat``
   per tracked folder (~35 µs each locally; an SMB round trip each on a
   mapped network drive) ran with no limit on every call. Now one deadline
   covers the whole check and an unfinished check runs nothing;
4. the refusal told the model to "read the files with read_file or grep
   instead" — and the reviewer's own ``grep`` follows the SAME junction and
   printed the outside file's text (measured). It names read_file only, and
   this pin runs every review tool the sentence names on the linked file;
5. a reparse point that is NOT a name surrogate (OneDrive's Files On-Demand
   placeholder folders, ProjFS) is the folder's own content, not a link to
   another place: refusing it made git_read refuse every call in a OneDrive
   repository (where the user keeps repositories). Junctions and symlinks
   (name surrogates) still count;
6. an ``lstat`` that fails for any other reason, a listing git refused, and a
   ``rev-parse`` that did not finish: the check runs nothing (fail-closed),
   never raises and never runs git anyway.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import git_read as G
from iron_jarvis.harness import helpers as H
from iron_jarvis.tools.base import ToolContext

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")

SECRET = "OUTSIDE-SECRET-TEXT"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, timeout=60,
    )


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


def _junction(link: Path, target: Path, made: list[Path]) -> None:
    got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, timeout=30)
    assert got.returncode == 0, got.stderr
    made.append(link)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    empty = tmp_path / "global.cfg"
    empty.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_bytes(SECRET.encode() + b"\n")
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "x.txt").write_bytes(b"inside pkg\n")
    (repo / "src" / "a.txt").write_bytes(b"inside src\n")
    (repo / "app.py").write_bytes(b"X = 1\n")
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    made: list[Path] = []
    yield repo, outside, made
    for link in reversed(made):
        if os.path.lexists(link):
            os.rmdir(link)  # the junction itself, never its target
    assert (outside / "x.txt").exists()


# --------------------------------------------------------------------------- #
# 1. a junction below the top level, the call made at and below the top
# --------------------------------------------------------------------------- #

@win
@pytest.mark.parametrize("command,args", [("grep", ["-n", "OUTSIDE"]), ("diff", []), ("blame", ["src/pkg/x.txt"]),
                                          ("status", ["--short"])])
@pytest.mark.parametrize("below", [False, True])
def test_a_junction_deeper_than_the_top_level_runs_nothing(world, command, args, below):
    repo, outside, made = world
    shutil.rmtree(repo / "src" / "pkg")
    _junction(repo / "src" / "pkg", outside, made)
    cwd = repo / "src" if below else repo
    if below and command == "blame":
        args = ["pkg/x.txt"]
    got = asyncio.run(G.GitReadTool().execute({"command": command, "args": args}, _ctx(cwd)))
    assert not got.ok and "src/pkg" in (got.error or ""), got
    assert SECRET not in (got.output or "") + (got.error or "")


# --------------------------------------------------------------------------- #
# 2. a tracked name Windows cannot hold
# --------------------------------------------------------------------------- #

@win
def test_a_tracked_name_windows_cannot_hold_does_not_break_every_call(world):
    repo, _outside, _made = world
    blob = _git(repo, "hash-object", "-w", "app.py").stdout.decode().strip()
    added = _git(repo, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo",
                 f"100644,{blob},what?/x.txt")
    assert added.returncode == 0, added.stderr  # (protectNTFS on refuses it: the control)
    assert _git(repo, "-c", "core.protectNTFS=false", "commit", "-q", "-m", "odd").returncode == 0
    got = G.run(repo, "log", ["--oneline", "-n", "1"])
    assert got.ok and "odd" in got.output, got


# --------------------------------------------------------------------------- #
# 3. one deadline for the whole check
# --------------------------------------------------------------------------- #

def _stub_git(monkeypatch, listing: str, *, code: int = 0, timed_out: bool = False,
              top_timed_out: bool = False, top: str = ""):
    from iron_jarvis.git import integration as I

    calls: list[list[str]] = []

    def fake(argv, cwd, **kw):
        calls.append(list(argv))
        if "--show-toplevel" in argv:
            return SimpleNamespace(code=-1 if top_timed_out else 0, out="" if top_timed_out else (top or str(cwd)) + "\n",
                                   err="", timed_out=top_timed_out)
        return SimpleNamespace(code=code, out=listing, err="", timed_out=timed_out)

    monkeypatch.setattr(I, "run_git", fake)
    return calls


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def test_the_whole_check_has_one_deadline_and_an_unfinished_check_runs_nothing(tmp_path, monkeypatch):
    listing = "".join(f"100644 abc 0\tf{i:03d}/a.txt\0" for i in range(100))
    _stub_git(monkeypatch, listing)
    clock = _Clock()
    monkeypatch.setattr(G, "time", clock, raising=False)
    looked: list[str] = []

    def slow(path: str) -> bool:  # each lstat takes a second (an SMB round trip, say)
        looked.append(path)
        clock.now += 1.0
        return False

    monkeypatch.setattr(G, "_is_link", slow)
    assert G._linked_folder(tmp_path, [], {}) == G.LINK_CHECK_UNSURE
    assert len(looked) <= int(G.LINK_CHECK_TIMEOUT_S) + 1, len(looked)


def test_the_listing_gets_only_what_is_left_of_the_deadline(tmp_path, monkeypatch):
    from iron_jarvis.git import integration as I

    clock = _Clock()
    monkeypatch.setattr(G, "time", clock, raising=False)
    asked: dict[str, float] = {}

    def fake(argv, cwd, **kw):
        if "--show-toplevel" in argv:
            clock.now += 15.0  # a slow rev-parse spends most of the budget
            return SimpleNamespace(code=0, out=str(cwd) + "\n", err="", timed_out=False)
        asked["timeout"] = kw["timeout"]
        return SimpleNamespace(code=0, out="", err="", timed_out=False)

    monkeypatch.setattr(I, "run_git", fake)
    assert G._linked_folder(tmp_path, [], {}) == ""
    assert asked["timeout"] <= G.LINK_CHECK_TIMEOUT_S - 15.0 + 1e-6, asked


# --------------------------------------------------------------------------- #
# 4. the refusal names only tools that do not follow the link
# --------------------------------------------------------------------------- #

def _review_tool(name: str):
    if name == "read_file":
        from iron_jarvis.tools.builtins import ReadFileTool

        return ReadFileTool(), {"path": "docs/x.txt"}
    if name == "grep":
        from iron_jarvis.tools.builtins import GrepTool

        return GrepTool(), {"pattern": "OUTSIDE"}
    if name == "list_files":
        from iron_jarvis.tools.builtins import ListFilesTool

        return ListFilesTool(), {}
    if name == "glob":
        from iron_jarvis.harness.tools import GlobTool

        return GlobTool(), {"pattern": "**/*.txt"}
    raise AssertionError(f"no runner for {name}")


@win
def test_every_tool_the_refusal_names_refuses_the_linked_file_too(world):
    repo, outside, made = world
    (repo / "docs").mkdir()
    (repo / "docs" / "x.txt").write_bytes(b"inside docs\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "docs").returncode == 0
    shutil.rmtree(repo / "docs")
    _junction(repo / "docs", outside, made)
    refused = G.run(repo, "grep", ["-n", "OUTSIDE"])
    assert not refused.ok and "is a link to another place" in refused.error
    named = [t for t in H.REVIEW_TOOLS if t != G.NAME and re.search(rf"\b{re.escape(t)}\b", refused.error)]
    assert named, refused.error  # it still points somewhere useful
    for name in named:
        tool, args = _review_tool(name)
        try:
            got = asyncio.run(tool.execute(args, _ctx(repo)))
            text = (got.output or "") + (got.error or "")
        except PermissionError as exc:  # safe_path's refusal (the registry words it)
            text = str(exc)
        assert SECRET not in text, (name, text)


# --------------------------------------------------------------------------- #
# 5. a reparse point that is not a name surrogate is not a link
# --------------------------------------------------------------------------- #

_DIR = 0o040777


def _fake_lstat(tag: int, attrs: int = 0x410):
    return lambda path, *a, **k: SimpleNamespace(st_mode=_DIR, st_file_attributes=attrs, st_reparse_tag=tag)


@pytest.mark.parametrize("tag", [0x9000001A, 0x9000701A, 0x9000001C])  # OneDrive placeholders, ProjFS
def test_a_cloud_placeholder_folder_is_not_a_link(monkeypatch, tag):
    monkeypatch.setattr(G.os, "lstat", _fake_lstat(tag))
    assert G._is_link("C:/repo/src") is False


@pytest.mark.parametrize("tag", [0xA0000003, 0xA000000C, 0xA000001D])  # junction, symlink, WSL symlink
def test_a_name_surrogate_still_counts_as_a_link(monkeypatch, tag):
    monkeypatch.setattr(G.os, "lstat", _fake_lstat(tag))
    assert G._is_link("C:/repo/src") is True


@win
def test_a_real_junction_and_a_real_folder(world):
    repo, outside, made = world
    _junction(repo / "lnk", outside, made)
    assert G._is_link(str(repo / "lnk")) is True
    assert G._is_link(str(repo / "src")) is False


# --------------------------------------------------------------------------- #
# 6. fail-closed, never an exception, never git anyway
# --------------------------------------------------------------------------- #

def test_an_lstat_that_fails_otherwise_runs_nothing(tmp_path, monkeypatch):
    _stub_git(monkeypatch, "100644 abc 0\tsrc/a.txt\0")

    def denied(path, *a, **k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(G.os, "lstat", denied)
    assert G._linked_folder(tmp_path, [], {}) == G.LINK_CHECK_UNSURE


def test_a_listing_git_refused_runs_nothing(tmp_path, monkeypatch):
    _stub_git(monkeypatch, "", code=128)
    assert G._linked_folder(tmp_path, [], {}) == G.LINK_CHECK_UNSURE


def test_a_toplevel_that_did_not_finish_runs_nothing(tmp_path, monkeypatch):
    calls = _stub_git(monkeypatch, "", top_timed_out=True)
    assert G._linked_folder(tmp_path, [], {}) == G.LINK_CHECK_UNSURE
    assert not [c for c in calls if "ls-files" in c], calls
