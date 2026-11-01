"""v1.420.0 (C4.12) REVIEW — Build › Code's "This message" scope and Revert.

Each case was red on the lane's first cut:

* the snapshot copied the user's index with a NEW modified time, which drops
  git's racy-entry protection: an edit of the same size made in the same
  instant as the index was written read as unchanged — the "This message"
  list missed it and Revert (turn scope) would put back the OLDER content;
* the snapshot ran ``git add -A :/`` — the WHOLE repository — so a session
  in a subfolder of a large repository hashed every file outside it, and it
  was bounded by git's 300 s limit, not the 30 s read limit;
* the path check RESOLVED the path (``Path.resolve``), which follows a link
  in the folder to wherever it points (a share: an SMB connection, the
  v1.379.0 lesson), and the blob read OPENED a symlink's target;
* the hosted service never recorded a message's start (the hook below);
  a late record would have named a tree taken AFTER the tool started.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.hosted import panes as PN

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
NOW = datetime.now(timezone.utc)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True).stdout.decode()


def _row(folder: Path | str) -> types.SimpleNamespace:
    return types.SimpleNamespace(folder=str(folder), created_at=NOW - timedelta(hours=1), harness="claude-code")


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    # v1.420.1: the product restores a file through the REPO's own git config
    # (`run_git` passes no -c), and GitHub's Windows runners set a GLOBAL
    # core.autocrlf=true, so the byte pins below read CRLF there. The repo's
    # own setting wins over the global one; the autocrlf test sets its own.
    _git(folder, "config", "core.autocrlf", "false")
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "s.py").write_bytes(b"s = 0\n")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first")
    return folder


def _tree_file(repo: Path, tree: str, rel: str) -> str:
    return _git(repo, "cat-file", "-p", f"{tree}:{rel}")


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #


def test_a_same_size_edit_git_itself_sees_is_in_the_snapshot(tmp_path):
    """git re-reads an entry whose time is not older than the index file
    ("racily clean"; Git for Windows compares whole seconds). The edit keeps
    the size and the second, and the index file was written in that same
    second — the user's own ``git status`` sees the edit. MUTATION: copy the
    index with ``copyfile`` (a fresh time: the entry no longer looks racy)
    -> the snapshot holds the OLD content (red). The times are set in 2020
    so "now" can never fall in the same second."""
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
    tree = PN.snapshot_tree(str(folder))
    assert _tree_file(folder, tree, "a.py") == "a = 9\n"
    # git's own view agrees (asked AFTER: a status may rewrite the index).
    assert "a.py" in _git(folder, "status", "--porcelain")


def test_the_snapshot_reads_the_working_file_not_what_is_staged(repo):
    (repo / "a.py").write_bytes(b"a = 1\n")
    _git(repo, "add", "a.py")
    (repo / "a.py").write_bytes(b"a = 22\n")
    tree = PN.snapshot_tree(str(repo))
    assert _tree_file(repo, tree, "a.py") == "a = 22\n"
    assert "M" in _git(repo, "diff", "--cached", "--name-status")  # the user's staging untouched


def test_the_snapshot_is_bounded_by_the_read_limit(repo, monkeypatch):
    """MUTATION: no ``timeout`` -> git's 300 s default (red)."""
    from iron_jarvis.git import integration as GI
    from iron_jarvis.harness import branches as B

    seen: list[float] = []
    real = GI.run_git

    def spy(args, cwd, **kw):
        seen.append(kw.get("timeout", GI.GIT_TIMEOUT_S))
        return real(args, cwd, **kw)

    monkeypatch.setattr(GI, "run_git", spy)
    PN.snapshot_tree(str(repo))
    assert seen and max(seen) <= B.QUICK_S, seen


def test_a_subfolder_session_snapshots_only_its_own_folder(repo):
    """The session works in ``repo/sub``. MUTATION: ``git add -A :/`` (the
    whole repository) -> a new file OUTSIDE the folder is in the tree (red).
    The turn scope's revert reads the tree relative to the folder."""
    (repo / "outside_new.txt").write_bytes(b"not this session's\n")
    sub = repo / "sub"
    (sub / "s.py").write_bytes(b"s = 1\n")
    tree = PN.snapshot_tree(str(sub))
    listed = _git(repo, "ls-tree", "-r", "--name-only", tree)
    assert "sub/s.py" in listed and "outside_new.txt" not in listed, listed
    # A turn recorded at that point; the tool then edits s.py; Revert in the
    # turn scope puts back what the message started from.
    home = repo.parent / "home"
    events = [
        {"type": "user", "turn": "t1", "at": (NOW - timedelta(minutes=5)).isoformat()},
        {"type": "done", "turn": "t1", "at": (NOW - timedelta(minutes=4)).isoformat()},
        {"type": "user", "turn": "t2", "at": NOW.isoformat()},
    ]
    assert PN.record_turn_start(home, "s1", str(sub), "t2")
    (sub / "s.py").write_bytes(b"s = 2\n")
    view = PN.changes_view(_row(sub), events, "Claude Code", scope="turn", home=home, sid="s1", running=True)
    by = {c["rel"]: c for c in view["changes"]}
    assert view["turn_how"] == "start" and set(by) == {"s.py"}, by
    PN.revert_file(home, "s1", _row(sub), events, "Claude Code", rel="s.py", blob=by["s.py"]["blob"],
                   scope="turn", turn="t2")
    assert (sub / "s.py").read_bytes() == b"s = 1\n"


def test_a_late_record_is_never_kept(tmp_path, repo, monkeypatch):
    """The service waits a bounded time for the start's tree; one that comes
    back after that (the tool may already be changing files) is dropped.
    MUTATION: record even when abandoned (red)."""
    home = tmp_path / "home"
    real = PN.snapshot_tree

    def slow(folder):
        import time

        time.sleep(0.6)
        return real(folder)

    monkeypatch.setattr(PN, "snapshot_tree", slow)

    async def go():
        got = await PN.record_turn_start_async(home, "s1", str(repo), "t9", bound=0.05)
        await asyncio.sleep(1.2)  # the thread finishes meanwhile
        return got

    assert asyncio.run(go()) is None
    assert PN.read_trees(home, "s1") == []


# --------------------------------------------------------------------------- #
# links
# --------------------------------------------------------------------------- #


def _junction(link: Path, target: Path) -> bool:
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return True
    except Exception:  # noqa: BLE001
        return False


def test_a_link_in_the_path_is_refused_without_being_followed(tmp_path, repo, monkeypatch):
    """``sub2`` is a junction to a folder outside the session. Its target is
    never resolved (a link to a share would be an SMB connection), and the
    revert is refused in words. MUTATION: check by ``Path.resolve`` -> the
    junction's path reaches ``realpath`` (red)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_bytes(b"secret\n")
    if not _junction(repo / "sub2", outside):
        pytest.skip("no junctions here")
    touched: list[str] = []
    real = os.path.realpath

    def spy(p, *a, **k):
        touched.append(str(p))
        return real(p, *a, **k)

    monkeypatch.setattr(os.path, "realpath", spy)
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(tmp_path / "home", "s1", _row(repo), [], "Codex", rel="sub2/x.py", blob="")
    assert no.value.status == 400 and no.value.sentence == PN.REVERT_BAD_PATH
    assert not [p for p in touched if "sub2" in p], touched
    assert (outside / "x.py").read_bytes() == b"secret\n"


def test_a_symlinks_blob_is_the_link_itself_never_its_target(tmp_path, repo):
    """git stores a symlink as its target's NAME; the pane's blob does too,
    without opening the target. MUTATION: open the file (follows the link)
    -> the blob is the target's content (red)."""
    target = tmp_path / "far.txt"
    target.write_bytes(b"far away\n")
    link = repo / "lnk.txt"
    try:
        os.symlink(str(target), str(link))
    except OSError:
        pytest.skip("no symlink privilege here")
    raw = os.readlink(str(link)).encode("utf-8")
    want = hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
    assert PN._blob(str(link)) == want


# --------------------------------------------------------------------------- #
# names and line endings
# --------------------------------------------------------------------------- #


def test_a_file_name_with_glob_letters_and_a_leading_dash_reverts_alone(tmp_path, repo):
    odd = repo / "-odd [1].py"
    odd.write_bytes(b"odd = 0\n")
    twin = repo / "-odd 1.py"  # what "[1]" would match as a glob
    twin.write_bytes(b"twin = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "odd")
    row = types.SimpleNamespace(folder=str(repo), created_at=datetime.now(timezone.utc) + timedelta(seconds=5),
                                harness="claude-code")
    odd.write_bytes(b"odd = 1\n")
    twin.write_bytes(b"twin = 1\n")
    PN.revert_file(tmp_path / "home", "s1", row, [], "Codex", rel="-odd [1].py", blob=PN._blob(str(odd)))
    assert odd.read_bytes() == b"odd = 0\n"
    assert twin.read_bytes() == b"twin = 1\n"


def test_autocrlf_true_keeps_the_turn_scope_and_revert_honest(tmp_path, repo):
    """CI's runners set ``core.autocrlf=true``: the content check reads the
    file's bytes on both sides, so a revert is never refused for line ends,
    and the reverted file is not listed again."""
    _git(repo, "config", "core.autocrlf", "true")
    home = tmp_path / "home"
    events = [{"type": "user", "turn": "t1", "at": NOW.isoformat()}]
    assert PN.record_turn_start(home, "s1", str(repo), "t1")
    (repo / "a.py").write_bytes(b"a = 5\r\n")
    view = PN.changes_view(_row(repo), events, "Codex", scope="turn", home=home, sid="s1", running=True)
    by = {c["rel"]: c for c in view["changes"]}
    assert set(by) == {"a.py"}, by
    PN.revert_file(home, "s1", _row(repo), events, "Codex", rel="a.py", blob=by["a.py"]["blob"],
                   scope="turn", turn="t1")
    again = PN.changes_view(_row(repo), events, "Codex", scope="turn", home=home, sid="s1", running=True)
    assert [c["rel"] for c in again["changes"]] == [], again["changes"]


# --------------------------------------------------------------------------- #
# THE SERVICE HOOK: a message's start is recorded by the hosted service
# --------------------------------------------------------------------------- #


async def test_a_recorded_session_scopes_this_message_to_its_exact_start(tmp_path, repo, monkeypatch):
    """The user edits a.py BEFORE sending; the tool edits sub/s.py after it
    received the message. "This message" lists the tool's edit only and says
    it is exact (``start``). MUTATION: drop the service's call -> the first
    message reads as the whole session and lists a.py too (red)."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import claude as C
    from tests._build_app_helpers_v1409 import client, new_session, stop_all, until, use_fake

    use_fake(monkeypatch, "claude_full_mode_edit_v1418", tmp_path / "fake.log")
    real_send = C.ClaudeDriver.send_user

    async def tool_edits_after_it_hears(self, text, files):
        (repo / "sub" / "s.py").write_bytes(b"s = 7\n")  # the tool's work
        return await real_send(self, text, files)

    monkeypatch.setattr(C.ClaudeDriver, "send_user", tool_edits_after_it_hears)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        (repo / "a.py").write_bytes(b"a = 3\n")  # the user's own, before the message
        r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "tq1"})
        assert r.status_code == 200, r.text
        svc = app.state.platform.hosted

        async def idle():
            live = svc.live.get(sid)
            return live is None or live.turn is None or not live.turn.running

        await until(idle, timeout=60)
        view = (await c.get(f"/build/app/sessions/{sid}/changes", params={"scope": "turn"})).json()
    await stop_all(app)
    assert view.get("turn_how") == "start", view
    assert {ch["rel"] for ch in view["changes"]} == {"sub/s.py"}, view["changes"]
