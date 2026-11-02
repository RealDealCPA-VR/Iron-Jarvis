"""v1.425.0 (Wave 5, C1) — THE FINISH CHECK'S FOLDER SNAPSHOT: from git in a
repository (no 5,000-file cap), and never in front of the reply's first word.

1. IN A GIT REPOSITORY GIT SAYS WHICH FILES (``harness/folder_changes``): the
   walk stopped at list_files' 5,000 files, so in any bigger repository a
   reply that changed files by command was never checked. Now git's own
   listing (tracked + untracked that nothing ignores) is read, each file's
   size and time from a listing of only the folders that hold them. A folder
   that is not a repository keeps the walk, cap and all.
2. THE FIRST SNAPSHOT RUNS BESIDE THE FIRST MODEL CALL (``finish.start`` /
   ``FinishCheck.ready``): the model call no longer waits for it, and the
   reply's FIRST TOOL does — so it is still the folder before any tool ran.

Driven through the REAL lanes (registry, permission engine, ledger, journal,
the shell tool) with a scripted model, and the real git. MIRROR NOTE
(lock-step): the ready() wait is pinned on both lanes. Each "now" pin fails on
the code before (v1.422.4); the docstring says how.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from iron_jarvis.harness import finish as F
from iron_jarvis.harness import folder_changes as FC
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — the fixture
    NOTE_HEAD,
    TEST_CMD,
    Model,
    _body,
    _lane,
    _shell,
    _write,
    env,
)

FILES_HEAD = "## Files this reply made that the request does not name\n"


@pytest.fixture(autouse=True)
def _no_kept_snapshots():
    FC.forget_kept()
    yield
    FC.forget_kept()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    return path


def _put(root: Path, rel: str, data: bytes = b"x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _many(root: Path, n: int, folder: str = "gen") -> None:
    """``n`` small files, 50 to a folder (untracked: git lists them too)."""
    for i in range(n):
        d = root / folder / f"d{i // 50:03d}"
        if i % 50 == 0:
            d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i:05d}.txt").write_bytes(b"1")


def _py(code: str) -> str:
    return f'python -c "{code}"'


def _files(note: str) -> str:
    return note.split(FILES_HEAD, 1)[1].split("\n\n", 1)[0]


def _met(model: Model) -> str:
    note = model.calls[-1]["messages"][-1].content
    assert note.startswith(NOTE_HEAD)
    return "1. met: checked.\nNothing needed changing."


# --------------------------------------------------------------------------- #
# 1. in a git repository, git's listing
# --------------------------------------------------------------------------- #


def test_a_repository_past_the_old_cap_is_read_whole(tmp_path):
    """5,010 files in a repository (list_files' cap is 5,000). OLD CODE: "this
    folder has more than 5000 files to compare" — no picture, so a command's
    change went unchecked."""
    from iron_jarvis.tools import builtins as B

    repo = _repo(tmp_path / "repo")
    _put(repo, "app.py", b"X = 1\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "init")
    _many(repo, 5009)
    assert 5009 + 1 > B._MAX_WALK_ENTRIES
    snap = FC.take(repo)
    assert snap.usable, snap.problem
    assert snap.how == "git"
    assert len(snap.files) == 5010 and "app.py" in snap.files and "gen/d000/f00000.txt" in snap.files
    # ...and the walk of the same folder still stops at its cap (a folder that
    # is NOT a repository keeps it).
    assert FC.take(repo, how="walk").problem == FC.PROBLEM_TOO_MANY.format(limit=B._MAX_WALK_ENTRIES)


def test_the_snapshot_is_git_s_listing_under_the_walk_s_pruning(tmp_path):
    """git's OWN ignore rules (a nested .gitignore, ``.git/info/exclude``),
    and the walk's pruning on top (dependency and cache folders, dot-folders
    but a project's CI folder, cache files): the same files the walk saw,
    minus what git ignores that the walk's reading of .gitignore cannot see."""
    repo = _repo(tmp_path / "repo")
    for rel in ("app.py", "pkg/mod.py", ".github/workflows/ci.yml", "node_modules/m/i.js",
                "__pycache__/app.cpython-312.pyc", ".pytest_cache/v", ".vscode/settings.json",
                "pkg/notes.tmp", "pkg/out/o.txt", "secret.dat", "pkg/a.pyc"):
        _put(repo, rel)
    _put(repo, "pkg/.gitignore", b"*.tmp\nout/\n")
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("secret.dat\n", encoding="utf-8")
    git = FC.take(repo)
    assert git.how == "git" and git.usable
    assert sorted(git.files) == [".github/workflows/ci.yml", "app.py", "pkg/.gitignore", "pkg/mod.py"]
    # The walk saw the same files (with the same sizes and times) plus what
    # git ignores: an ignored FILE (the walk leaves those to the comparison)
    # and the ``.git/info/exclude`` one it cannot see at all — git's own
    # rules are the truth.
    walk = FC.take(repo, how="walk")
    assert sorted(walk.files) == sorted([*git.files, "pkg/notes.tmp", "secret.dat"])
    assert {r: walk.files[r] for r in git.files} == dict(git.files)


def test_a_folder_that_is_not_a_repository_is_walked_and_git_never_starts(tmp_path, monkeypatch):
    from iron_jarvis.git import integration as GI

    started: list[list[str]] = []
    real = GI.run_git
    monkeypatch.setattr(GI, "run_git", lambda args, *a, **kw: (started.append(args), real(args, *a, **kw))[1])
    _put(tmp_path, "plain/a.txt")
    snap = FC.take(tmp_path / "plain")
    assert snap.how == "walk" and sorted(snap.files) == ["a.txt"]
    assert started == [], "git was started for a folder that is not in a repository"


def test_a_subfolder_of_a_repository_lists_only_its_own_files(tmp_path):
    repo = _repo(tmp_path / "repo")
    _put(repo, "top.txt")
    _put(repo, "sub/a.txt")
    _put(repo, "sub/deep/b.txt")
    _put(repo, ".gitignore", b"*.log\n")
    _put(repo, "sub/run.log")  # ignored by the repository's own rules, above the folder
    snap = FC.take(repo / "sub")
    assert snap.how == "git" and sorted(snap.files) == ["a.txt", "deep/b.txt"]


def test_a_repository_that_is_the_whole_home_folder_is_walked(tmp_path, monkeypatch):
    """A dotfiles repository in the home folder often ignores everything
    (``*``): git's listing would hide every file of every project under it."""
    home = _repo(tmp_path / "home")
    _put(home, ".gitignore", b"*\n")
    project = home / "Projects" / "app"
    _put(project, "app.py")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    assert Path.home() == home
    snap = FC.take(project)
    assert snap.how == "walk" and sorted(snap.files) == ["app.py"]


def test_the_end_of_a_reply_reads_the_folder_the_way_its_start_did(tmp_path):
    plain = tmp_path / "plain"
    _put(plain, "a.txt")
    gone = FC.take(plain, how="git")
    assert not gone.usable and gone.problem == FC.PROBLEM_NOT_A_REPO and gone.how == "git"
    repo = _repo(tmp_path / "repo")
    _put(repo, "a.txt")
    assert FC.take(repo, how="walk").how == "walk"


def test_a_repository_inside_the_repository_and_a_submodule_are_walked(tmp_path):
    """git lists a repository inside this one as one folder ("inner/") and a
    submodule as one path; their files are WALKED (as the walk always did),
    so a command's change inside one is still seen. MUTATION: skip them (red:
    their files missing)."""
    repo = _repo(tmp_path / "repo")
    _put(repo, "a.txt")
    inner = _repo(repo / "inner")
    _put(inner, "in.py")
    _put(inner, "node_modules/x.js")  # the walk's pruning inside it too
    sub_src = _repo(tmp_path / "sub_src")
    _put(sub_src, "lib.py")
    _git(sub_src, "add", "-A")
    _git(sub_src, "commit", "-qm", "s")
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "vendor/lib")
    snap = FC.take(repo)
    assert snap.how == "git" and snap.usable
    assert {"a.txt", "inner/in.py", "vendor/lib/lib.py", ".gitmodules"} <= set(snap.files), sorted(snap.files)
    assert not any("node_modules" in r or "/.git/" in r for r in snap.files), sorted(snap.files)
    before = snap
    (repo / "vendor" / "lib" / "lib.py").write_bytes(b"changed by a command")
    _put(inner, "made.py")
    got = FC.compare(before, FC.take(repo, how="git"))
    assert got.changed == ["vendor/lib/lib.py"] and got.created == ["inner/made.py"]


def test_the_git_cap_is_its_own(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    _many(repo, 6)
    monkeypatch.setattr(FC, "GIT_MAX_FILES", 5)
    snap = FC.take(repo)
    assert snap.how == "git" and snap.problem == FC.PROBLEM_TOO_MANY.format(limit=5) and not snap.files
    # list_files' cap never applies to git's listing.
    monkeypatch.setattr(FC, "GIT_MAX_FILES", 200_000)
    monkeypatch.setattr(FC, "_caps", lambda: (3, 10.0))
    assert FC.take(repo).usable


def test_a_repositorys_own_fsmonitor_program_never_runs(tmp_path):
    """``git ls-files`` runs the repository's ``core.fsmonitor`` program
    (measured on this PC with git 2.50: the hook wrote its file). The
    snapshot switches it off — a repository's own config must not start a
    program when Iron Jarvis only reads which files there are."""
    repo = _repo(tmp_path / "repo")
    _put(repo, "a.txt")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "i")
    marker = tmp_path / "ran.txt"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8", newline="\n")
    _git(repo, "config", "core.fsmonitor", hook.as_posix())
    snap = FC.take(repo)
    assert snap.how == "git" and sorted(snap.files) == ["a.txt"]
    assert not marker.exists(), "the repository's fsmonitor program ran"


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows folder link")
def test_a_junction_in_place_of_a_tracked_folder_is_never_followed(tmp_path):
    """git still lists the tracked files of ``pkg/`` after the folder was
    replaced by a junction to a folder outside: the listing never goes
    through it (the walk's rule — a link can lead outside, or to a share)."""
    import _winapi
    import shutil

    repo = _repo(tmp_path / "repo")
    _put(repo, "a.txt")
    _put(repo, "pkg/mod.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "i")
    outside = tmp_path / "outside"
    _put(outside, "mod.py", b"secret")
    shutil.rmtree(repo / "pkg")
    _winapi.CreateJunction(str(outside), str(repo / "pkg"))
    snap = FC.take(repo)
    assert snap.how == "git" and sorted(snap.files) == ["a.txt"]


# --------------------------------------------------------------------------- #
# ...through the lanes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_check_reads_a_command_s_change_in_a_repository_past_the_old_cap(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """OLD CODE: the note's files section said "None." and "…this folder has
    more than 5000 files to compare" — the command's new file went unseen."""
    platform, ws, wire = env
    _git(ws, "init", "-q")
    _git(ws, "add", "app.py", "test_mini.py")
    _git(ws, "commit", "-qm", "init")
    _many(ws, 5050)
    model = wire([_write("app.py", "X = 2\n"), _shell(_py("open('made.py', 'w').write('1')")), "Done.", _met])
    await _lane(lane, platform, _body(ws), monkeypatch)
    files = _files(model.notes()[0])
    assert "- `made.py`" in files, files
    assert "more than" not in files


async def test_a_reply_that_removed_the_repository_is_never_compared_walk_to_git(env, monkeypatch):  # noqa: F811 — the fixture
    """The reply began in a repository and its command removed ``.git``: the
    end is read git's way too, so the folder cannot be compared (said) —
    never a walk set against git's listing, where every file git's own rules
    left out (``.git/info/exclude``) would read as made by the reply."""
    from tests.test_finish_lanes_v1386 import _stream

    platform, ws, wire = env
    _git(ws, "init", "-q")
    (ws / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (ws / ".git" / "info" / "exclude").write_text("local.dat\n", encoding="utf-8")
    _put(ws, "local.dat")
    wipe = _py("import shutil, stat, os; shutil.rmtree('.git', onerror=lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p)));"
               " open('made.py', 'w').write('1')")
    model = wire([_write("app.py", "X = 2\n"), _shell(wipe), "Done.", _met])
    await _stream(platform, _body(ws))
    assert not (ws / ".git").exists()
    files = _files(model.notes()[0])
    assert "local.dat" not in files
    assert F.FinishCheck.CANNOT_COMPARE.format(why=FC.PROBLEM_NOT_A_REPO) in files


# --------------------------------------------------------------------------- #
# 2. beside the first model call; before the first tool
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_first_model_call_never_waits_for_the_snapshot_and_no_tool_runs_before_it(
        env, lane, monkeypatch):  # noqa: F811 — the fixture
    """The snapshot is held until the reply's first model call has been made
    (a gate the scripted model opens), then reads the folder a little later.
    NOW: the model call opens the gate while the snapshot waits (the call was
    not held), and the shell call starts only after the snapshot was read —
    so the file its command makes is listed as made by the reply. OLD CODE:
    the snapshot was read BEFORE the first model call (the gate was never
    opened in time: ``gate.wait`` timed out). MUTATION (a lane that does not
    wait for :meth:`FinishCheck.ready`): the command runs while the folder is
    still being read."""
    platform, ws, wire = env
    gate = threading.Event()
    seen: dict[str, float] = {}
    opened_in_time: list[bool] = []
    real = FC.take

    def held(folder, **kw):
        if kw.get("how"):
            return real(folder, **kw)  # the end of the reply: as it is
        opened_in_time.append(gate.wait(4.0))
        time.sleep(0.6)  # the tool, were it not waiting, would run now
        out = real(folder, **kw)
        seen["read"] = time.monotonic()
        return out

    monkeypatch.setattr(FC, "take", held)
    real_invoke = platform.registry.invoke

    async def invoke(*a, **kw):
        seen.setdefault("tool", time.monotonic())
        return await real_invoke(*a, **kw)

    monkeypatch.setattr(platform.registry, "invoke", invoke)

    def first(model: Model):
        gate.set()
        return _shell(_py("open('made.py', 'w').write('1')"))

    model = wire([first, _write("app.py", "X = 2\n", "w2"), "Done.", _met])
    await _lane(lane, platform, _body(ws), monkeypatch)
    assert opened_in_time == [True], "the first model call waited for the snapshot"
    assert seen["tool"] >= seen["read"], "a tool ran before the folder was read"
    assert "- `made.py`" in _files(model.notes()[0])


async def test_a_reply_with_no_tool_never_waits_for_the_snapshot(env, monkeypatch):  # noqa: F811 — the fixture
    """A plain answer (no tool) ends without waiting for the folder's read:
    nothing to compare. OLD CODE: every reply waited for it before its first
    word."""
    from tests.test_finish_lanes_v1386 import _stream

    platform, ws, wire = env
    release = threading.Event()
    real = FC.take

    def held(folder, **kw):
        release.wait(60.0)
        return real(folder, **kw)

    monkeypatch.setattr(FC, "take", held)
    # Review (v1.425.0): no wall-clock bar (a loaded runner is not the code):
    # the read is held until AFTER the reply ended, so a reply that waited for
    # it could only end once the walk's own bound gave up on it — and the read
    # would then be over. MUTATION (``finish.start`` awaits the read): red.
    wire(["Just an answer."])
    try:
        out = await _stream(platform, _body(ws, text="what does app.py do?"))
        still_reading = [t for t in F._FIRST_READS if not t.done()]
    finally:
        release.set()
    await asyncio.gather(*list(F._FIRST_READS))  # the read the reply never waited for ends
    assert out["done"]["reply"].startswith("Just an answer.") and "finish" not in out["done"]
    assert still_reading, "the reply waited for the folder's read"


async def test_a_carry_on_still_finds_the_snapshot_read_beside_the_cut_reply(env):  # noqa: F811 — the fixture
    """The read keeps the snapshot for a carry-on of this reply even though
    the reply never waited for it (``FC.keep`` runs when the read ends)."""
    platform, ws, wire = env
    harness = type("H", (), {"plan_mode": False, "tree": None, "plan": None})()
    check = await F.start(
        engine=platform.engine, home=platform.config.home, event_bus=None,
        session_id="build:carry", folder=ws, messages=[], harness=harness, turn_id="t-cut",
    )
    assert check is not None
    await check.ready()
    got = FC.kept("build:carry", "t-cut", ws)
    assert got is not None and got[0] is check._before and got[0].usable
