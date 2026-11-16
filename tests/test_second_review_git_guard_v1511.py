"""v1.511.0 (second review T2, T8; decision E3): the git guard of a coding
session.

T2 (E3). Working in the folder directly is the composer's DEFAULT kind of
coding session, and the guard used to be off there (it returned at once when
the turn had no branch of its own). Now, in a coding session that works in its
folder directly AND the folder is in a git repository, a push, a force push,
anything that throws work away, a history rewrite, a deleted branch, a git
setting and a pull request or other GitHub change ask EVERY time (a once-only
card: a session grant, "Don't ask" and auto-approve never cover it), worded as
acting on the user's own folder or remote. A commit does not ask there. A
folder that is not a repository and a caller that is not a coding session are
exactly as before.

T8. On a session's own branch (and, after T2, in the user's own folder) the
guard reads ordinary spellings it missed: ``git remote set-url`` / ``add`` /
``remove`` / ``rename`` (a git setting), ``git reset <branch>`` (moves the
branch), ``git checkout <file>`` (a discard), ``git stash pop`` / ``apply``
(the stash is shared) and a separator inside a quoted message.

The end-to-end tests drive the REAL app with REAL git in a temp folder; the
model is the only double.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

import pytest

from iron_jarvis.harness import branches as B
from iron_jarvis.harness import git_guard as G
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.daemon.app import create_app
from tests.test_build_git_v1375 import git, make_remote, make_repo, new_branch_session, no_real_gh, turn
from tests.test_build_sessions_v1367 import Script, turn_frames, until, wire
from tests.test_chat_turn_stop_v1241 import _asgi_post
from tests.test_build_plan_ask_v1367 import _finish, _start

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    no_real_gh(monkeypatch)
    yield
    B.forget_gh_state()


@contextlib.contextmanager
def on_branch(folder: Path):
    """A turn on a session's own branch, its worktree at ``folder``."""
    token = B.set_turn_branch({"repo": str(folder), "worktree": str(folder), "branch": "ij/x",
                               "base": "main", "checkout": str(folder)}, str(folder))
    try:
        yield
    finally:
        B.reset_turn_branch(token)


@contextlib.contextmanager
def in_folder(folder: Path):
    """A coding session's turn that works in ``folder`` directly."""
    token = G.set_turn_folder(G.folder_facts(str(folder)))
    try:
        yield
    finally:
        G.reset_turn_folder(token)


@pytest.fixture()
def tree(tmp_path) -> Path:
    """A working tree (a ``.git`` folder is all the guard looks for) with a
    file and a folder in it."""
    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    (root / "app.py").write_bytes(b"x = 1\n")
    (root / "src").mkdir()
    (root / "src" / "lib.py").write_bytes(b"y = 1\n")
    return root


def kinds(command: str) -> list[str]:
    return [a.kind for a in G.classify(command)]


# --------------------------------------------------------------------------- #
# T8 — on a session's own branch
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", [
    "git remote set-url origin https://example.com/other.git",
    "git remote add mirror https://example.com/mirror.git",
    "git remote remove origin",
    "git remote rm origin",
    "git remote rename origin old",
])
def test_t8a_a_remote_change_is_a_git_setting(tree, command):
    with on_branch(tree):
        assert kinds(command) == ["config"]
        check = G.guard_check(command)
        assert check.ask is True and check.once_only is True and check.reason == "git"


@pytest.mark.parametrize("command", ["git remote -v", "git remote", "git remote show origin",
                                     "git remote get-url origin"])
def test_t8a_reading_the_remotes_asks_nothing(tree, command):
    with on_branch(tree):
        assert kinds(command) == []


@pytest.mark.parametrize("command,expected", [
    ("git reset main", ["rewrite"]),
    ("git reset v1.0", ["rewrite"]),
    ("git reset --mixed feature", ["rewrite"]),
    ("git reset origin/main", ["rewrite"]),
    ("git reset", []),
    ("git reset HEAD", []),
    ("git reset app.py", []),
    ("git reset src", []),
    ("git reset HEAD app.py", []),
    ("git reset main app.py", []),
    ("git reset main --", ["rewrite"]),
    ("git reset -- app.py", []),
    ("git reset -- main", []),
    ("git reset --hard", ["discard"]),
])
def test_t8b_a_reset_to_a_branch_or_tag_moves_the_branch(tree, command, expected):
    with on_branch(tree):
        assert kinds(command) == expected
        if expected == ["rewrite"]:
            assert G.refusal(command), "refused on a session's own branch, as `git reset origin/main` is"


@pytest.mark.parametrize("command,expected", [
    ("git checkout app.py", ["discard"]),
    ("git checkout src", ["discard"]),
    ("git checkout src/lib.py", ["discard"]),
    ("git checkout HEAD app.py", ["discard"]),
    ("git checkout -- app.py", ["discard"]),
    ("git checkout .", ["discard"]),
    ("git checkout main", []),
    ("git checkout -b feature", []),
    ("git checkout -", []),
])
def test_t8c_a_checkout_of_a_file_is_a_discard(tree, command, expected):
    with on_branch(tree):
        assert kinds(command) == expected


@pytest.mark.parametrize("command", ["git stash pop", "git stash apply", "git stash apply stash@{1}",
                                     "git stash pop --index"])
def test_t8d_taking_from_the_shared_stash_asks_every_time(tree, command):
    with on_branch(tree):
        assert kinds(command) == ["stash"]
        check = G.guard_check(command)
        assert check.ask is True and check.once_only is True and check.reason == "git"
        assert "stash" in check.words and "shared" in check.words


@pytest.mark.parametrize("command", ["git stash", "git stash list", "git stash show -p"])
def test_t8d_putting_work_in_the_stash_or_reading_it_asks_nothing(tree, command):
    with on_branch(tree):
        assert kinds(command) == []


@pytest.mark.parametrize("command", [
    'git commit -m "fix (a); b" --amend',
    "git commit -m 'fix (a); b' --amend",
    'git commit -m "first line\nsecond; line" --amend',
    'git commit -m "a && b | c" --amend',
    "bash -lc 'git commit -m \"x; y\" --amend'",
    # PowerShell's escaped quote inside a double-quoted message
    'git commit -m "say `"hi`"; done" --amend',
    # bash's escaped quote inside a double-quoted message
    'git commit -m "say \\"hi\\"; done" --amend',
])
def test_t8e_a_separator_inside_a_quoted_message_does_not_end_the_command(tree, command):
    with on_branch(tree):
        assert "rewrite" in kinds(command), command
        assert G.refusal(command)


def test_t8e_a_quote_that_never_closes_fails_closed(tree):
    with on_branch(tree):
        assert "rewrite" in kinds('git commit -m "fix; b --amend')
        assert "rewrite" in kinds("git commit -m 'fix; b --amend")


def test_t8e_a_real_separator_still_ends_the_command(tree):
    with on_branch(tree):
        assert kinds('git commit -m "x"; git status --amend') == ["commit"]
        assert kinds("git commit -m done && git push origin HEAD") == ["commit", "push"]


# --------------------------------------------------------------------------- #
# T2 — in the user's own folder
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", [
    "git push",
    "git push origin main",
    "git push --force origin main",
    "git push origin --delete old",
    "git reset --hard",
    "git reset --hard HEAD~3",
    "git reset main",
    "git clean -fdx",
    "git checkout -- .",
    "git checkout app.py",
    "git restore app.py",
    "git branch -D old",
    "git stash drop",
    "git stash clear",
    "git stash pop",
    "git rebase main",
    "git commit --amend -m x",
    "git remote set-url origin https://example.com/other.git",
    "git config user.email someone@example.com",
    "gh pr merge 7",
    "git commit -m done && git push",
])
def test_t2_in_the_users_own_repository_these_ask_every_time(tree, command):
    with in_folder(tree):
        check = G.guard_check(command)
        assert check is not None and check.ask is True, command
        assert check.once_only is True and check.reason == "git"
        assert any(w in check.words for w in ("your own", "your remote", "GitHub")), check.words
        assert G.refusal(command) == "", "in the user's own folder it is theirs to allow, never refused"


@pytest.mark.parametrize("command", ["git commit -am wip", "git commit -m x", "git status", "git diff",
                                     "git log --oneline", "git add -A", "git checkout main"])
def test_t2_a_commit_and_reading_ask_nothing_in_the_users_folder(tree, command):
    with in_folder(tree):
        assert G.guard_check(command) is None


def test_t2_the_words_say_it_is_the_users_own_folder_or_remote(tree):
    with in_folder(tree):
        assert "your own folder" in G.guard_check("git push origin main").words
        assert "origin" in G.guard_check("git push origin main").words
        assert "your own folder" in G.guard_check("git reset --hard").words
        assert "force" in G.guard_check("git push -f origin main").words


def test_t2_a_folder_inside_a_repository_counts(tree):
    with in_folder(tree / "src"):
        assert G.guard_check("git push").ask is True


def test_t2_a_folder_that_is_not_a_repository_is_exactly_as_before(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with in_folder(plain):
        for command in ("git push --force", "git reset --hard", "git clean -fdx", "git stash pop"):
            assert G.guard_check(command) is None
            assert G.refusal(command) == ""


def test_t2_a_caller_that_is_not_a_coding_session_is_exactly_as_before():
    assert B.current() is None and G.TURN_FOLDER.get() is None
    for command in ("git push --force", "git reset --hard", "git stash pop", "git remote set-url origin x"):
        assert G.guard_check(command) is None
        assert G.refusal(command) == ""


def test_t2_a_branch_turn_keeps_its_own_rules(tree):
    with in_folder(tree), on_branch(tree):
        assert G.guard_check("git commit -m x").ask is True, "a commit asks on a session's own branch"
        assert G.guard_check("git push --force").ask is False, "a force push is refused there"


def test_t2_judging_a_command_in_the_users_folder_reads_nothing_on_disk(tree, monkeypatch):
    """Whether the folder is a repository is found once, when the turn is
    armed (off the event loop) — never by a stat while a command is judged."""
    with in_folder(tree):
        def no_stat(*a, **k):
            raise AssertionError("a stat while judging a command")

        monkeypatch.setattr(os, "stat", no_stat)
        monkeypatch.setattr(os, "lstat", no_stat)
        assert G.guard_check("git push origin main").ask is True
        assert G.guard_check("git reset --hard").ask is True


# --------------------------------------------------------------------------- #
# T2 end to end: the real stream lane, a direct session on "Don't ask"
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


#: git by its full path (the agent's shell may run on a scrubbed PATH).
GITX = f'"{shutil.which("git")}"' if shutil.which("git") else "git"


@needs_git
async def test_t2_a_push_and_a_hard_reset_in_the_users_folder_ask_every_time_on_dont_ask(app, tmp_path):
    repo = make_repo(tmp_path / "proj")
    bare = make_remote(tmp_path, repo)
    remote_before = git("rev-parse", "main", cwd=bare)
    view = await new_branch_session(app, repo, approval_mode="yolo", branch=False)
    assert not view.get("git"), "the session works in the folder directly"
    (repo / "app.py").write_bytes(b"x = 2\n")
    wire(app, Script([
        [ToolCall(id="c1", name="Bash", arguments={"command": f"{GITX} commit -am mine"})],
        [ToolCall(id="c2", name="Bash", arguments={"command": f"{GITX} push origin main"})],
        [ToolCall(id="c3", name="Bash", arguments={"command": f"{GITX} reset --hard HEAD~1"})],
        "Done.",
    ]))
    drop, task = await _start(app, view["id"], "commit, push and reset", "t2-direct")
    cards = await until(lambda: [d for e, d in turn_frames(app, "t2-direct") if e == "approval"])
    assert cards, "a push from the user's own folder ran with no card on Don't ask"
    card = cards[0]
    assert card["once_only"] is True and card["once_reason"] == "git" and card["can_always"] is False
    assert "pushes from your own folder to origin" in card["cost_note"]
    # A WIDER answer ("for this conversation") is read as once: the next
    # git that throws work away asks again.
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "conversation"}))[0] == 200
    second = (await until(lambda: [d for e, d in turn_frames(app, "t2-direct") if e == "approval"][1:]))[0]
    assert second["once_only"] is True and "rewrites history in your own folder" in second["cost_note"]
    assert (await _asgi_post(app, f"/chat/approvals/{second['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "t2-direct", drop, task)
    resolved = [d["decision"] for e, d in turn_frames(app, "t2-direct") if e == "approval_resolved"]
    assert resolved == ["once", "deny"], "the commit asked nothing; the push and the reset each asked"
    head = git("rev-parse", "HEAD", cwd=repo)
    assert git("rev-parse", "main", cwd=bare) == head != remote_before, "the allowed push ran"
    assert git("log", "-1", "--format=%s", cwd=repo) == "mine", "the denied reset did not run"


@needs_git
async def test_t2_a_folder_that_is_not_a_repository_asks_nothing_new(app, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    view = await new_branch_session(app, plain, approval_mode="yolo")
    wire(app, Script([[ToolCall(id="c1", name="Bash", arguments={"command": f"{GITX} stash pop"})], "Done."]))
    frames = await turn(app, view["id"], "pop", "t2-plain")
    assert not [d for e, d in frames if e == "approval"]
