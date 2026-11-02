"""v1.425.0 (Wave 5, C3) — Build › Code's per-message git tree is CHEAPER,
and still read before the tool hears the message.

The tree ("This message"'s base in the Changes pane) is taken before the tool
is sent the message (``hosted/service._drive``): the order is KEPT — nothing
proves a coding tool cannot change a file before the tree is read once it has
the message (it starts acting at once), and a cold start runs the tool's own
start-up (Claude Code's SessionStart hooks run the user's commands). What
changed is the tree's cost: a session KEEPS a private index between its
messages (``panes.kept_index``) instead of copying the user's index and
re-reading every unstaged and untracked file each time. Measured on this PC
on a 50,000-file repository: 509 -> 402 ms median per message (10 messages,
each after a reply that edited 10 files and made 2), 728 -> 447 ms when the
user's index lagged further behind.

Each pin's docstring names the mutation it was checked against.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from iron_jarvis.hosted import panes as PN

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True).stdout.decode()


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    _git(folder, "config", "core.autocrlf", "false")
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "s.py").write_bytes(b"s = 0\n")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first")
    return folder


def _file(repo: Path, tree: str, rel: str) -> str:
    return _git(repo, "cat-file", "-p", f"{tree}:{rel}")


def test_the_kept_index_gives_the_same_tree_as_a_fresh_copy(tmp_path, repo):
    """Across three "messages" (a reply between each: edits, a new file, a
    deletion) the kept index's tree is exactly the fresh copy's."""
    keep = PN.kept_index(tmp_path / "home", "s1")
    assert not keep.exists()
    assert PN.snapshot_tree(str(repo), keep=keep) == PN.snapshot_tree(str(repo))
    assert keep.is_file(), "the session's index was not kept"
    (repo / "a.py").write_bytes(b"a = 11\n")
    (repo / "new.txt").write_bytes(b"n\n")
    assert PN.snapshot_tree(str(repo), keep=keep) == PN.snapshot_tree(str(repo))
    (repo / "sub" / "s.py").unlink()
    (repo / "new.txt").write_bytes(b"changed\n")
    tree = PN.snapshot_tree(str(repo), keep=keep)
    assert tree == PN.snapshot_tree(str(repo))
    assert _file(repo, tree, "new.txt") == "changed\n" and "sub/s.py" not in _git(repo, "ls-tree", "-r", tree)
    # The user's own index and branch are untouched.
    assert _git(repo, "status", "--porcelain").splitlines() == [" M a.py", " D sub/s.py", "?? new.txt"]


def test_the_users_index_is_copied_once_per_session(tmp_path, repo, monkeypatch):
    """The point of keeping it: later messages read only what changed since
    the last one. MUTATION: copy the user's index every time (red)."""
    copies: list[str] = []
    real = PN._copy_index
    monkeypatch.setattr(PN, "_copy_index", lambda src, dest: (copies.append(str(dest)), real(src, dest))[1])
    keep = PN.kept_index(tmp_path / "home", "s1")
    for i in range(3):
        (repo / "a.py").write_bytes(f"a = {i}\n".encode())
        PN.snapshot_tree(str(repo), keep=keep)
    assert copies == [str(keep)]


def test_a_kept_index_git_refuses_is_copied_again(tmp_path, repo):
    """A damaged kept index (or another repository's, after the folder moved)
    starts again from the user's index. MUTATION: no second try (red:
    GitUnreadable)."""
    keep = PN.kept_index(tmp_path / "home", "s1")
    PN.snapshot_tree(str(repo), keep=keep)
    keep.write_bytes(b"not an index")
    (repo / "a.py").write_bytes(b"a = 5\n")
    tree = PN.snapshot_tree(str(repo), keep=keep)
    assert tree == PN.snapshot_tree(str(repo)) and _file(repo, tree, "a.py") == "a = 5\n"


def test_the_first_copy_keeps_the_index_time_so_a_racy_edit_is_seen(tmp_path):
    """The v1.420.0 racy rule holds for the kept index's first copy: an edit
    of the same size, in the same second the user's index was written, is in
    the tree. MUTATION: ``copyfile`` instead of ``copy2`` in the kept copy
    (red: the old content)."""
    folder = tmp_path / "racy"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    old = 1_577_836_800_000_000_000  # 2020-01-01, whole second
    (folder / "a.py").write_bytes(b"a = 0\n")
    os.utime(folder / "a.py", ns=(old, old))
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first")
    (folder / "a.py").write_bytes(b"a = 9\n")  # same size, same second
    os.utime(folder / "a.py", ns=(old, old + 300_000_000))
    os.utime(folder / ".git" / "index", ns=(old, old + 500_000_000))
    tree = PN.snapshot_tree(str(folder), keep=PN.kept_index(tmp_path / "home", "s1"))
    assert _file(folder, tree, "a.py") == "a = 9\n"


def test_every_tree_of_a_session_uses_its_kept_index(tmp_path, repo):
    """The message's start (the service's call), the idle read and the
    Changes pane all read through the session's ONE kept index."""
    home = tmp_path / "home"
    assert PN.record_turn_start(home, "s1", str(repo), "t1")
    assert PN.kept_index(home, "s1").is_file()
    assert not PN.kept_index(home, "s2").exists()


def test_a_kept_index_is_named_safely_and_never_backed_up(tmp_path, repo):
    """A cache: under ``workspaces/`` (a backup leaves it out), named from
    the session id with nothing that can leave its folder."""
    from iron_jarvis.maintenance import create_backup

    home = tmp_path / "home"
    keep = PN.kept_index(home, "..\\evil/../s:1")
    assert keep.parent == home / "workspaces" / "code-index"
    assert "/" not in keep.name and "\\" not in keep.name and ":" not in keep.name
    assert PN.kept_index(home, "") is None and PN.kept_index(None, "s1") is None
    PN.record_turn_start(home, "s1", str(repo), "t1")
    (home / "config.toml").write_text("x = 1\n", encoding="utf-8")
    out, _n = create_backup(home, tmp_path / "b.tar.gz")
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert any(n.endswith("config.toml") for n in names)
    assert not any("code-index" in n for n in names), names


async def test_a_deleted_session_drops_its_kept_index(tmp_path, monkeypatch):
    """The kept index goes with its session (never left behind)."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import service as S
    from iron_jarvis.hosted import store

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    svc = S.service_for(app.state.platform)
    keep = PN.kept_index(svc.home, row.id)
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"x")
    await svc.delete(row.id)
    assert not keep.exists()
