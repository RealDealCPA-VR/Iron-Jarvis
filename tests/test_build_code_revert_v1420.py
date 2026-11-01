"""v1.420.0 (C4.12) — Build › Code's Changes pane: ONE message's own
changes, and Revert one file.

Before: ``hosted/panes.py`` compared git with the ROW's start, so every
earlier message's edits piled up in the list, and the pane said "No Undo
here" with no way back short of git.

Now:

* ``scope=turn`` lists one message's changes against the folder's TREE at
  its start — recorded exactly by ``record_turn_start`` (the service's call),
  or the newest idle read between the message before it and its start
  (``note_seen``, the pane's own read), the first message reading as the
  whole session; not known is SAID, never guessed;
* Revert puts ONE file back as it was at the scope's start (``git restore
  --worktree`` from the base; a file that did not exist then is MOVED to the
  trash, never deleted), under the session's git lock; the branch, the
  commits, git's staging area and every other file stay;
* refused in words before anything is touched: no repository, a reply
  running, a file changed since the pane read it, a path outside the folder,
  a network folder (by its TEXT), a new file git has staged, a message whose
  start was not seen.

Real git in a scratch repository; the real routes for the press.
"""

from __future__ import annotations

import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted.events import EventLog, make
from tests._build_app_helpers_v1409 import client, new_session, stop_all

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
NOW = datetime.now(timezone.utc)


def _git(cwd: Path, *args: str, when: str | None = None) -> str:
    env = dict(os.environ)
    if when:
        env["GIT_COMMITTER_DATE"] = when
        env["GIT_AUTHOR_DATE"] = when
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=env).stdout.decode()


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _row(folder: Path | str, created: datetime | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(folder=str(folder), created_at=created or NOW - timedelta(hours=1),
                                 harness="claude-code")


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "a.py").write_text("a = 0\n", encoding="utf-8")
    (folder / "b.py").write_text("b = 0\n", encoding="utf-8")
    (folder / "keep.py").write_text("keep = 0\n", encoding="utf-8")
    (folder / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first", when="2020-01-01T00:00:00+00:00")
    return folder


def _turn(tid: str, start: datetime, end: datetime | None) -> list[dict]:
    out = [make("user", text="do it", turn=tid, at=_iso(start))]
    if end is not None:
        out.append(make("done", stop="completed", turn=tid, at=_iso(end)))
    return out


def _two_turns(home: Path, repo: Path) -> list[dict]:
    """Turn 1 edits a.py; the pane reads the folder while idle; turn 2 edits
    a.py again, edits b.py and makes c.py."""
    events = _turn("t1", NOW - timedelta(minutes=10), NOW - timedelta(minutes=9))
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    PN.note_seen(home, "s1", str(repo), events)
    # The idle read is stamped with the REAL clock: turn 2 starts after it.
    events += _turn("t2", datetime.now(timezone.utc) + timedelta(seconds=60), None)
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    (repo / "c.py").write_text("c = 2\n", encoding="utf-8")
    (repo / "noise.log").write_text("ignored\n", encoding="utf-8")
    return events


def _by(view: dict) -> dict:
    return {c["rel"]: c for c in view["changes"]}


# --------------------------------------------------------------------------- #
# THIS MESSAGE'S CHANGES
# --------------------------------------------------------------------------- #


def test_the_turn_scope_lists_only_this_messages_changes(tmp_path, repo):
    """MUTATION: diff the turn scope against the session base (drop the
    ``base=tree`` branch) -> a.py reads "a = 0" -> "a = 2" and the turn row
    count grows (red)."""
    home = tmp_path / "home"
    events = _two_turns(home, repo)
    turn = PN.changes_view(_row(repo), events, "Claude Code", scope="turn", home=home, sid="s1", running=True)
    assert turn["scope"] == "turn" and turn["turn"] == "t2" and turn["turn_how"] == "seen", turn
    by = _by(turn)
    assert set(by) == {"a.py", "b.py", "c.py"}, by.keys()
    assert "-a = 1" in by["a.py"]["diff"] and "+a = 2" in by["a.py"]["diff"]
    assert by["c.py"]["status"] == "created"
    assert "last read it" in turn["words"]
    whole = PN.changes_view(_row(repo), events, "Claude Code", home=home, sid="s1", running=True)
    assert whole["scope"] == "session" and "-a = 0" in _by(whole)["a.py"]["diff"]
    # Every git row carries the blob Revert checks; the words name Revert.
    assert all(c["blob"] for c in turn["changes"]) and turn["can_revert"] is True
    assert "No Undo" in turn["no_undo"] and "Revert puts ONE file back" in turn["no_undo"]


def test_a_recorded_turn_start_is_exact_and_wins_over_an_idle_read(tmp_path, repo):
    home = tmp_path / "home"
    events = _two_turns(home, repo)
    PN.record_turn_start(home, "s1", str(repo), "t2")  # the folder as it is now: turn 2 so far
    (repo / "keep.py").write_text("keep = 9\n", encoding="utf-8")
    view = PN.changes_view(_row(repo), events, "Codex", scope="turn", home=home, sid="s1", running=True)
    assert view["turn_how"] == "start" and set(_by(view)) == {"keep.py"}, _by(view).keys()
    assert view["words"] == PN.TURN_WORDS_START


def test_a_message_whose_start_was_not_seen_says_so_and_lists_the_session(tmp_path, repo):
    home = tmp_path / "home"
    events = _turn("t1", NOW - timedelta(minutes=10), NOW - timedelta(minutes=9))
    events += _turn("t2", NOW + timedelta(seconds=5), None)  # nobody read the folder in between
    (repo / "a.py").write_text("a = 5\n", encoding="utf-8")
    view = PN.changes_view(_row(repo), events, "Claude Code", scope="turn", home=home, sid="s1", running=True)
    assert view["scope"] == "session" and view["words"].startswith(PN.TURN_UNKNOWN), view["words"]
    assert set(_by(view)) == {"a.py"}


def test_the_first_message_is_the_whole_session(tmp_path, repo):
    home = tmp_path / "home"
    events = _turn("t1", NOW - timedelta(minutes=1), None)
    (repo / "a.py").write_text("a = 7\n", encoding="utf-8")
    view = PN.changes_view(_row(repo), events, "Claude Code", scope="turn", home=home, sid="s1", running=True)
    assert view["scope"] == "turn" and view["turn_how"] == "first" and view["words"] == PN.TURN_WORDS_FIRST


def test_an_idle_read_records_the_folder_at_most_once_a_while_and_never_mid_reply(tmp_path, repo):
    home = tmp_path / "home"
    done = _turn("t1", NOW - timedelta(minutes=10), NOW - timedelta(minutes=9))
    PN.note_seen(home, "s1", str(repo), done)
    PN.note_seen(home, "s1", str(repo), done)
    assert len(PN.read_trees(home, "s1")) == 1
    PN.note_seen(home, "s1", str(repo), done + _turn("t2", NOW, None))  # a reply runs
    assert len(PN.read_trees(home, "s1")) == 1


def test_a_snapshot_never_touches_the_users_index_or_branch(tmp_path, repo):
    (repo / "a.py").write_text("a = 3\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    (repo / "new.txt").write_text("n\n", encoding="utf-8")
    before = (_git(repo, "diff", "--cached", "--name-status"), _git(repo, "status", "--porcelain"),
              _git(repo, "rev-parse", "HEAD"))
    tree = PN.snapshot_tree(str(repo))
    after = (_git(repo, "diff", "--cached", "--name-status"), _git(repo, "status", "--porcelain"),
             _git(repo, "rev-parse", "HEAD"))
    assert before == after
    listed = _git(repo, "ls-tree", "--name-only", tree)
    assert "new.txt" in listed and "a.py" in listed


# --------------------------------------------------------------------------- #
# REVERT ONE FILE
# --------------------------------------------------------------------------- #


def test_revert_in_the_turn_scope_puts_back_this_messages_change_only(tmp_path, repo):
    """THE ACCEPTANCE: a recorded session reverts one file. a.py goes back to
    what turn 1 left (never to the commit); b.py, the commit, the branch and
    git's staging area stay. MUTATION: restore from the session base -> a.py
    reads "a = 0" (red)."""
    home = tmp_path / "home"
    events = _two_turns(home, repo)
    view = PN.changes_view(_row(repo), events, "Claude Code", scope="turn", home=home, sid="s1", running=False)
    row = _by(view)["a.py"]
    head = _git(repo, "rev-parse", "HEAD")
    staged = _git(repo, "diff", "--cached", "--name-status")
    got = PN.revert_file(home, "s1", _row(repo), events, "Claude Code", rel="a.py", blob=row["blob"],
                         scope="turn", turn="t2")
    assert got["ok"] and "every other file" in got["words"] and "when Iron Jarvis last read" in got["words"]
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 2\n"
    assert _git(repo, "rev-parse", "HEAD") == head and _git(repo, "diff", "--cached", "--name-status") == staged


def test_revert_of_a_file_this_message_made_moves_it_to_the_trash(tmp_path, repo):
    home = tmp_path / "home"
    events = _two_turns(home, repo)
    view = PN.changes_view(_row(repo), events, "Claude Code", scope="turn", home=home, sid="s1")
    got = PN.revert_file(home, "s1", _row(repo), events, "Claude Code", rel="c.py",
                         blob=_by(view)["c.py"]["blob"], scope="turn", turn="t2")
    assert not (repo / "c.py").exists()
    moved = Path(got["moved_to"])
    assert moved.is_file() and moved.read_text(encoding="utf-8") == "c = 2\n"
    assert str(home / "trash") in str(moved) and "trash" in got["words"]


def test_revert_in_the_session_scope_goes_back_to_the_sessions_start(tmp_path, repo):
    home = tmp_path / "home"
    events = _two_turns(home, repo)
    view = PN.changes_view(_row(repo), events, "Pi", home=home, sid="s1")
    PN.revert_file(home, "s1", _row(repo), events, "Pi", rel="a.py", blob=_by(view)["a.py"]["blob"])
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 0\n"
    # a deleted file comes back
    (repo / "keep.py").unlink()
    PN.revert_file(home, "s1", _row(repo), events, "Pi", rel="keep.py", blob="")
    assert (repo / "keep.py").read_text(encoding="utf-8") == "keep = 0\n"


def test_a_file_changed_since_the_pane_read_it_is_never_put_back_over(tmp_path, repo):
    """MUTATION: drop the blob check -> the user's newer edit is lost (red)."""
    home = tmp_path / "home"
    events = _two_turns(home, repo)
    view = PN.changes_view(_row(repo), events, "Claude Code", home=home, sid="s1")
    (repo / "b.py").write_text("b = mine\n", encoding="utf-8")
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(home, "s1", _row(repo), events, "Claude Code", rel="b.py", blob=_by(view)["b.py"]["blob"])
    assert "changed after this list was read" in no.value.sentence
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = mine\n"


@pytest.mark.parametrize("rel", ["../outside.py", "C:/Windows/win.ini", "/etc/passwd", "\\\\host\\share\\x.py",
                                 "sub/../../x.py", ""])
def test_a_path_outside_the_folder_is_refused(tmp_path, repo, rel):
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(tmp_path / "home", "s1", _row(repo), [], "Codex", rel=rel, blob="")
    assert no.value.status == 400 and no.value.sentence == PN.REVERT_BAD_PATH


def test_the_refusals_say_why_and_touch_nothing(tmp_path, repo, monkeypatch):
    home = tmp_path / "home"
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(home, "s1", _row(plain), [], "Codex", rel="x.py", blob=PN._blob(str(plain / "x.py")))
    assert "not in a git repository" in no.value.sentence and "Codex" in no.value.sentence
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(home, "s1", _row(repo), [], "Codex", rel="a.py", blob="", running=True)
    assert no.value.sentence == PN.REVERT_RUNNING.format(tool="Codex")
    # A turn whose start nobody saw (not the first) cannot be reverted to.
    events = _turn("t1", NOW - timedelta(minutes=10), NOW - timedelta(minutes=9)) + _turn("t2", NOW, None)
    (repo / "a.py").write_text("a = 4\n", encoding="utf-8")
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(home, "s1", _row(repo), events, "Codex", rel="a.py",
                       blob=PN._blob(str(repo / "a.py")), scope="turn", turn="t2")
    assert "did not read the folder just before" in no.value.sentence
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 4\n"
    # A new file git has staged is left alone.
    (repo / "staged.py").write_text("s\n", encoding="utf-8")
    _git(repo, "add", "staged.py")
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(home, "s1", _row(repo), [], "Codex", rel="staged.py", blob=PN._blob(str(repo / "staged.py")))
    assert "staging area" in no.value.sentence and (repo / "staged.py").exists()


def test_a_network_folder_is_refused_from_its_text_before_any_stat(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a network folder was touched")

    monkeypatch.setattr(os.path, "isdir", boom)
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(tmp_path, "s1", _row("\\\\host\\share\\repo"), [], "Codex", rel="a.py", blob="")
    assert no.value.sentence == PN.ON_SHARE


# --------------------------------------------------------------------------- #
# THE ROUTES
# --------------------------------------------------------------------------- #


async def test_the_press_reverts_through_the_route_under_the_sessions_git_lock(tmp_path, repo, monkeypatch):
    from iron_jarvis.daemon.routes import build_git

    app = create_app(str(tmp_path / "home"))
    keys: list[str] = []
    real = build_git._locked

    def spy(key, fn):
        keys.append(key)
        return real(key, fn)

    monkeypatch.setattr(build_git, "_locked", spy)
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        (repo / "b.py").write_text("b = 9\n", encoding="utf-8")
        body = (await c.get(f"/build/app/sessions/{sid}/changes")).json()
        row = _by(body)["b.py"]
        r = await c.post(f"/build/app/sessions/{sid}/changes/revert", json={"path": "b.py", "blob": "stale"})
        assert r.status_code == 409 and "changed after this list was read" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{sid}/changes/revert", json={"path": "b.py", "blob": row["blob"]})
        assert r.status_code == 200, r.text
        assert (repo / "b.py").read_text(encoding="utf-8") == "b = 0\n"
        after = (await c.get(f"/build/app/sessions/{sid}/changes")).json()
        assert "b.py" not in _by(after)
        turn = (await c.get(f"/build/app/sessions/{sid}/changes", params={"scope": "turn"})).json()
        assert turn["source"] == "git"
    await stop_all(app)
    assert keys == [f"app:{sid}", f"app:{sid}"]


async def test_the_route_refuses_while_a_reply_runs(tmp_path, repo):
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        (repo / "b.py").write_text("b = 9\n", encoding="utf-8")
        svc = S.service_for(app.state.platform)
        svc.live[sid] = types.SimpleNamespace(
            turn=types.SimpleNamespace(running=True), log=EventLog(svc.home, sid), running=False, proc=None)
        try:
            r = await c.post(f"/build/app/sessions/{sid}/changes/revert",
                             json={"path": "b.py", "blob": PN._blob(str(repo / "b.py"))})
        finally:
            svc.live.pop(sid, None)
    await stop_all(app)
    assert r.status_code == 409 and "still working on a reply" in r.json()["detail"]
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 9\n"
