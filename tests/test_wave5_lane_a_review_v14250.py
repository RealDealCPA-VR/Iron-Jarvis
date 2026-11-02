"""v1.425.0 (Wave 5, lane A — the INDEPENDENT REVIEW's pins).

Each pin went RED on the builder's code first (the docstring says how), then
green with the fix beside it; each fix was mutation-checked.

1. B7: a Stop pressed while the session's tool is still in its HANDSHAKE (the
   process is up, ``driver.open`` has not returned) used to be passed to the
   tool as an interrupt of nothing — the turn never ended, and the message was
   sent once the start finished: the tool answered a reply the user stopped.
2. C3: a session's KEPT index kept every file a snapshot had once added as
   untracked, even after a ``.gitignore`` came to ignore it (a
   ``node_modules`` installed before its ignore rule) — the per-message tree
   was no longer the folder's tree (the fresh copy's), so "This message"
   listed ignored files and every later snapshot re-read them. And a kept
   index a killed git left LOCKED (``<index>.lock``) refused every later add.
3. C1: a folder whose name changes LENGTH when lower-cased (Windows folds
   case; "İ" lower-cases to two characters) was never listed in git's way:
   its files were missing from BOTH snapshots, so a command's change there
   was silently not seen.
4. C1: a ``.git`` FILE naming a git folder on a network share made the
   finish check's automatic ``git ls-files`` open a connection to that host
   on every reply (the v1.379.0 lesson: an SMB connection sends this PC's
   sign-in hash) — the folder is walked instead, git never started.
5. C1: a session folder its enclosing repository IGNORES (``scratch/`` in the
   repository's .gitignore) got an EMPTY listing from git — a usable snapshot
   of no files, every command's change there missed. Walked, as before.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.harness import folder_changes as FC
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True).stdout.decode()


# --------------------------------------------------------------------------- #
# 1. B7: Stop during the tool's handshake
# --------------------------------------------------------------------------- #


async def test_a_stop_during_the_tools_handshake_ends_the_turn_and_never_sends_the_message(tmp_path, monkeypatch):
    """The process is started and its handshake (``driver.open``) is held;
    Stop is pressed. BUILDER'S CODE: ``interrupt`` saw a driver and a live
    process and only told the tool to interrupt (nothing was running there) —
    the turn stayed open, and when the handshake finished the message was
    sent (red: the turn did not end within 10 s; the tool got the message).
    MUTATION (the fix's check in ``interrupt`` removed): red the same way."""
    from iron_jarvis.daemon.app import create_app
    from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until, use_fake

    app = create_app(str(tmp_path / "home"))
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    work = tmp_path / "w"
    work.mkdir()
    gate = asyncio.Event()
    in_open = asyncio.Event()
    real_open = C.ClaudeDriver.open

    async def open_(self, **kw):
        if self.cwd == str(work):
            in_open.set()
            await asyncio.wait_for(gate.wait(), 60)
        return await real_open(self, **kw)

    monkeypatch.setattr(C.ClaudeDriver, "open", open_)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, work)

        async def say():
            r = await c.post(f"/build/app/sessions/{a['id']}/turn", json={"text": "ready?", "turn_id": "ta"})
            return frames(r.text)

        ta = asyncio.ensure_future(say())
        await asyncio.wait_for(in_open.wait(), 30)
        live = svc.live[a["id"]]
        assert live.running and live.driver is not None, "the process was not up yet"
        drive = live.turn.task
        r = await c.post(f"/build/app/sessions/{a['id']}/interrupt")
        assert r.status_code == 200, r.text
        evs = await asyncio.wait_for(ta, 10)
        assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}
        gate.set()
        await asyncio.wait_for(asyncio.shield(drive), 30)
        await until(lambda: live.running)
        await asyncio.sleep(0.5)
        assert not [m for m in sent(log) if m.get("type") == "user"], "the tool was sent a stopped message"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 2. C3: the kept index follows the ignore rules
# --------------------------------------------------------------------------- #


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "a.py").write_bytes(b"a = 0\n")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first")
    return folder


def test_a_file_that_comes_to_be_ignored_leaves_the_kept_tree(tmp_path, repo):
    """``build/out.js`` is made before any rule ignores it (a snapshot adds it
    to the session's kept index), then ``.gitignore`` names ``build/``.
    BUILDER'S CODE: the kept tree still held ``build/out.js`` — not the fresh
    copy's tree (red). MUTATION (the ``.gitignore`` look removed): red."""
    keep = PN.kept_index(tmp_path / "home", "s1")
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_bytes(b"v1\n")
    assert PN.snapshot_tree(str(repo), keep=keep) == PN.snapshot_tree(str(repo))
    (repo / ".gitignore").write_bytes(b"build/\n")
    tree = PN.snapshot_tree(str(repo), keep=keep)
    assert "build/out.js" not in _git(repo, "ls-tree", "-r", "--name-only", tree).split()
    assert tree == PN.snapshot_tree(str(repo))
    # ...and a later edit of the ignored file changes nothing.
    (repo / "build" / "out.js").write_bytes(b"v2 rebuilt\n")
    assert PN.snapshot_tree(str(repo), keep=keep) == tree


def test_a_rule_above_the_sessions_folder_or_in_info_exclude_is_followed_too(tmp_path, repo):
    """A session in a SUBFOLDER: the rule that comes to ignore its file sits in
    the repository's top ``.gitignore`` (outside the folder ``git add -A .``
    reads) or in ``.git/info/exclude``. MUTATION (the rule-file fingerprint
    removed): red."""
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "m.py").write_bytes(b"m = 1\n")
    keep = PN.kept_index(tmp_path / "home", "s2")
    (sub / "gen.tmp").write_bytes(b"1\n")
    assert PN.snapshot_tree(str(sub), keep=keep) == PN.snapshot_tree(str(sub))
    (repo / ".gitignore").write_bytes(b"*.tmp\n")
    assert PN.snapshot_tree(str(sub), keep=keep) == PN.snapshot_tree(str(sub))
    (sub / "log.out").write_bytes(b"2\n")
    assert PN.snapshot_tree(str(sub), keep=keep) == PN.snapshot_tree(str(sub))
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_bytes(b"*.out\n")
    tree = PN.snapshot_tree(str(sub), keep=keep)
    assert tree == PN.snapshot_tree(str(sub))
    assert "pkg/log.out" not in _git(repo, "ls-tree", "-r", "--name-only", tree).split()


def test_the_kept_index_is_still_copied_once_when_no_rule_changes(tmp_path, repo, monkeypatch):
    """The speed the kept index is for stays: no rule changed, no new copy."""
    copies: list[str] = []
    real = PN._copy_index
    monkeypatch.setattr(PN, "_copy_index", lambda src, dest: (copies.append(str(dest)), real(src, dest))[1])
    keep = PN.kept_index(tmp_path / "home", "s3")
    (repo / ".gitignore").write_bytes(b"*.log\n")
    for i in range(3):
        (repo / "a.py").write_bytes(f"a = {i}\n".encode())
        (repo / f"n{i}.txt").write_bytes(b"n\n")
        PN.snapshot_tree(str(repo), keep=keep)
    assert copies == [str(keep)]



def test_a_kept_index_a_killed_git_left_locked_is_used_again(tmp_path, repo):
    """A git killed at its time limit (``procs.run`` kills the tree) leaves
    ``<kept index>.lock`` behind. BUILDER'S CODE: every later ``git add``
    refused ("File exists"), the copy-again path too — the session never had
    a message's tree again (red: GitUnreadable). MUTATION (the leftover lock
    not removed): red."""
    keep = PN.kept_index(tmp_path / "home", "s4")
    PN.snapshot_tree(str(repo), keep=keep)
    Path(str(keep) + ".lock").write_bytes(b"")
    (repo / "a.py").write_bytes(b"a = 7\n")
    tree = PN.snapshot_tree(str(repo), keep=keep)
    assert tree == PN.snapshot_tree(str(repo))
    assert "a = 7" in _git(repo, "cat-file", "-p", f"{tree}:a.py")
    assert not Path(str(keep) + ".lock").exists()

# --------------------------------------------------------------------------- #
# 3. C1: a folder whose lower-cased name changes length
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name != "nt", reason="names are folded only on Windows")
def test_a_folder_whose_lower_case_name_is_longer_is_still_read(tmp_path):
    """"İ" (U+0130) lower-cases to "i̇" — two characters. BUILDER'S CODE: the
    folders to list were cut from the folded path at the UNFOLDED path's
    positions, so "İstanbul/" was never listed and its file was missing from
    the snapshot (red). MUTATION (the cut read from the unfolded path again):
    red."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel in ("a.txt", "İstanbul/deep/b.txt"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    snap = FC.take(repo)
    assert snap.how == "git" and snap.usable, snap.problem
    assert sorted(snap.files) == sorted(["a.txt", "İstanbul/deep/b.txt"])


# --------------------------------------------------------------------------- #
# 4. C1: a .git file pointing at a share
# --------------------------------------------------------------------------- #


def test_a_git_file_that_names_a_network_share_is_walked_and_git_never_starts(tmp_path, monkeypatch):
    """A downloaded folder holds a ``.git`` FILE whose ``gitdir:`` is a share.
    BUILDER'S CODE: git was started (red) — it would connect to that host on
    every reply. Walked instead. MUTATION (the look at the ``.git`` file
    removed): red."""
    from iron_jarvis.git import integration as GI

    started: list[list[str]] = []
    real = GI.run_git
    monkeypatch.setattr(GI, "run_git", lambda args, *a, **kw: (started.append(args), real(args, *a, **kw))[1])
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"x")
    for spelling in ("gitdir: //nohost.invalid/share/repo.git\n", "gitdir: \\\\nohost.invalid\\share\\x\n",
                     "gitdir: \\\\?\\UNC\\nohost.invalid\\share\\x\n"):
        (folder / ".git").write_text(spelling, encoding="utf-8")
        snap = FC.take(folder)
        # (the walk records the ``.git`` FILE itself; the comparison never
        # names a ``.git`` path)
        assert snap.how == "walk" and sorted(snap.files) == [".git", "app.py"], spelling
    assert started == [], "git was started for a repository whose git folder is on a share"


def test_a_worktrees_git_file_on_this_pc_still_reads_through_git(tmp_path):
    """A worktree's ``.git`` file names a LOCAL git folder: git's way, as
    before (the guard reads only a share)."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "a.txt").write_bytes(b"a")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "i")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    assert (wt / ".git").is_file()
    snap = FC.take(wt)
    assert snap.how == "git" and sorted(snap.files) == ["a.txt"]


# --------------------------------------------------------------------------- #
# 5. C1: a session folder its enclosing repository ignores
# --------------------------------------------------------------------------- #


def test_a_folder_the_enclosing_repository_ignores_is_walked_and_its_change_is_seen(tmp_path):
    """The session works in ``repo/scratch/proj`` and the repository's
    ``.gitignore`` names ``scratch/``. BUILDER'S CODE: git's listing there is
    EMPTY — a "usable" snapshot of no files, so a command's new file was never
    seen (red: ``how == "git"``, no files). Walked instead, as before git's
    way. MUTATION (the ``check-ignore`` look removed): red."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_bytes(b"scratch/\n")
    proj = repo / "scratch" / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "a.py").write_bytes(b"a")
    (proj / "sub" / "b.py").write_bytes(b"b")
    before = FC.take(proj)
    assert before.how == "walk" and before.usable, before.problem
    assert sorted(before.files) == ["a.py", "sub/b.py"]
    (proj / "made.py").write_bytes(b"1")
    got = FC.compare(before, FC.take(proj, how=before.how))
    assert got.created == ["made.py"]


def test_a_folder_inside_a_repository_that_nothing_ignores_still_reads_through_git(tmp_path):
    """The control: a subfolder git does not ignore is read git's way."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_bytes(b"other/\n")
    proj = repo / "pkg"
    proj.mkdir()
    (proj / "a.py").write_bytes(b"a")
    snap = FC.take(proj)
    assert snap.how == "git" and sorted(snap.files) == ["a.py"]
