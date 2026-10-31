"""v1.414.0 review — Build › Code's right panes (Changes, Usage, Session).

Each test here was RED on the first cut (8a7379ff) and pins one defect the
review found:

* a program under the coding tool showed ``--token VALUE`` / ``--api-key
  VALUE`` in the clear (the panes masked shapes only, not the v1.404.0
  secret-flag rule the Session tab of Chat › Code already applies);
* a process listing that could not be read, or one cut at its bound, read as
  "Nothing is running" / the whole list;
* a window the TOOL reported whose reset has passed was still shown as what
  is left (Iron-Proxy's own windows were already dropped then);
* git that did not answer (a time limit, "dubious ownership") read as "No
  files changed yet" or "not in a git repository"; git missing read as "not
  in a git repository";
* one ``git diff`` child PER changed file (54 git processes, ~3 s, for 50
  files) on every 5-second poll while a reply ran.

Real git in a scratch repository; nothing starts a coding tool or a model.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.hosted import panes as PN

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(cwd: Path, *args: str, when: str | None = None) -> None:
    env = dict(os.environ)
    if when:
        env["GIT_COMMITTER_DATE"] = when
        env["GIT_AUTHOR_DATE"] = when
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=env)


def _row(folder: Path, created: datetime | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(folder=str(folder), created_at=created or datetime.now(timezone.utc))


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first", when="2020-01-01T00:00:00+00:00")
    return folder


# --------------------------------------------------------------------------- #
# Background: a secret after a flag, an unreadable listing, a cut listing
# --------------------------------------------------------------------------- #
def test_a_secret_after_a_flag_on_a_child_command_line_never_shows():
    secret = "s3cr3tV4lueQ9zz"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "--token", secret,
                              "--api-key", "Zx9vQ2mLkP0aB7"])
    try:
        deadline = time.monotonic() + 15
        got: list = []
        while time.monotonic() < deadline:
            got = PN.programs_under(os.getpid())
            if any(g["pid"] == child.pid for g in got):
                break
            time.sleep(0.1)
        mine = [g for g in got if g["pid"] == child.pid]
        assert mine, got
        cmd = mine[0]["command"]
        assert "--token" in cmd and "time.sleep(30)" in cmd, cmd
        assert secret not in cmd and "Zx9vQ2mLkP0aB7" not in cmd, cmd
    finally:
        child.kill()
        child.wait(10)


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def name(self) -> str:
        return f"worker{self.pid}.exe"

    def cmdline(self) -> list[str]:
        return [f"worker{self.pid}.exe"]


def test_a_listing_that_could_not_be_read_is_never_nothing_running(monkeypatch):
    import psutil

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    view = PN.session_view(types.SimpleNamespace(folder=""), [], 4242, True)
    assert view["tool_running"] is True and view["programs"] == []
    assert view["programs_known"] is False, view


def test_a_listing_cut_at_its_bound_says_so(monkeypatch):
    import psutil

    count = PN.MAX_PROGRAMS + 5

    class Parent:
        def __init__(self, pid):
            pass

        def children(self, recursive=False):
            return [_FakeProc(100 + i) for i in range(count)]

    monkeypatch.setattr(psutil, "Process", Parent)
    view = PN.session_view(types.SimpleNamespace(folder=""), [], 4242, True)
    assert len(view["programs"]) == PN.MAX_PROGRAMS
    assert view["programs_known"] is True and view["programs_more"] is True
    # Exactly at the bound: nothing more to say.
    monkeypatch.setattr(PN, "MAX_PROGRAMS", PN.MAX_PROGRAMS + 5)
    assert PN.session_view(types.SimpleNamespace(folder=""), [], 4242, True)["programs_more"] is False


# --------------------------------------------------------------------------- #
# Usage: a window the tool reported that has since reset
# --------------------------------------------------------------------------- #
def test_a_tool_reported_window_whose_reset_has_passed_is_not_shown_as_what_is_left():
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    evs = [{"type": "usage", "at": "2026-10-30T09:00:00Z", "windows": [
        {"key": "five_hour", "label": "5-hour window", "percent": 97, "resets_at": past},
        {"key": "seven_day", "label": "7-day window", "percent": 40, "resets_at": soon},
    ]}]
    got = PN.seen_windows(evs)
    assert [w["key"] for w in got["windows"]] == ["seven_day"], got
    # Every window over: nothing known, never the old percent.
    evs[0]["windows"] = evs[0]["windows"][:1]
    assert PN.seen_windows(evs) is None
    # A window with no reset time stays (it was what the tool said, with its age).
    evs[0]["windows"] = [{"key": "five_hour", "label": "5-hour window", "percent": 12, "resets_at": None}]
    assert PN.seen_windows(evs)["windows"][0]["percent"] == 12


# --------------------------------------------------------------------------- #
# Changes: git that did not answer is never "no changes"
# --------------------------------------------------------------------------- #
def _failing(fail_on: str, *, timed_out: bool = False, err: str = ""):
    real = PN._git

    def fake(args, cwd, *, input=None):
        if fail_on in args:
            from iron_jarvis.git.integration import GitRun

            return GitRun(code=-1 if timed_out else 128, out="", err=err, timed_out=timed_out)
        return real(args, cwd, input=input)

    return fake


@pytest.mark.parametrize("step", ["--name-status", "--numstat", "--others", "rev-list"])
def test_a_git_step_that_timed_out_is_never_a_made_up_list(repo, monkeypatch, step):
    """No list from git that did not answer: not "No files changed yet" (the
    list read), not every file "binary" (the counts), not the new files left
    out (untracked), not every tracked file "created" (the base: a rev-list
    that timed out read as "no commits yet", so the empty tree)."""
    row = _row(repo)
    (repo / "app.py").write_text("x = 1\ny = 3\n", encoding="utf-8")
    (repo / "fresh.txt").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr(PN, "_git", _failing(step, timed_out=True))
    view = PN.changes_view(row, [], "Claude Code")
    assert view["source"] == "none" and view["changes"] == [], view
    assert view["words"] == "git could not read this folder's changes, so none are listed: it did not answer in time.", view


def test_a_repository_with_no_commits_reads_against_the_empty_tree(tmp_path):
    folder = tmp_path / "fresh"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "a.txt").write_text("a\n", encoding="utf-8")
    _git(folder, "add", "a.txt")
    (folder / "b.txt").write_text("b\n", encoding="utf-8")
    view = PN.changes_view(_row(folder), [], "Codex")
    assert view["source"] == "git", view
    assert {c["rel"]: c["status"] for c in view["changes"]} == {"a.txt": "created", "b.txt": "created"}


def test_a_repository_git_will_not_read_is_never_not_a_repository(repo, monkeypatch):
    row = _row(repo)
    monkeypatch.setattr(PN, "_git", _failing(
        "--is-inside-work-tree",
        err="fatal: detected dubious ownership in repository at 'C:/x'\nTo add an exception..."))
    view = PN.changes_view(row, [], "Claude Code")
    assert view["source"] == "none", view
    assert "not in a git repository" not in view["words"] and "dubious ownership" in view["words"], view
    # A rev-parse that ran out of time is not "not a repository" either.
    monkeypatch.setattr(PN, "_git", _failing("--is-inside-work-tree", timed_out=True))
    view = PN.changes_view(row, [], "Claude Code")
    assert view["source"] == "none" and "not in a git repository" not in view["words"], view
    # A real "not a git repository" still falls back to the tool's record.
    monkeypatch.undo()
    plain = repo.parent / "plain"
    plain.mkdir()
    assert PN.changes_view(_row(plain), [], "Claude Code")["source"] == "tool"


def test_git_missing_is_said_as_git_missing(tmp_path, monkeypatch):
    from iron_jarvis.harness import branches as B

    monkeypatch.setattr(B, "git_path", lambda: None)
    view = PN.changes_view(_row(tmp_path), [], "Pi")
    assert view["source"] == "tool"
    assert "not in a git repository" not in view["words"] and "git is not installed" in view["words"], view


# --------------------------------------------------------------------------- #
# Changes: a bounded number of git children, the same diffs
# --------------------------------------------------------------------------- #
def test_many_changed_files_are_read_with_a_bounded_number_of_git_children(repo, monkeypatch):
    names = [f"f{i:02d}.txt" for i in range(30)] + ["with space.txt", "ünï.txt", "bin.dat", "gone.txt", "move-me.txt"]
    for n in names:
        (repo / n).write_bytes(b"\0\1\2" if n == "bin.dat" else f"line {n}\nkeep\n".encode())
    (repo / "move-me.txt").write_text("a fairly long line so git can see the rename\n" * 5, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "more", when="2020-01-02T00:00:00+00:00")
    row = _row(repo)
    for n in names[:30] + ["with space.txt", "ünï.txt"]:
        (repo / n).write_text(f"line {n}\nchanged\n", encoding="utf-8")
    (repo / "bin.dat").write_bytes(b"\0\x09\x09\x09")
    (repo / "gone.txt").unlink()
    _git(repo, "mv", "move-me.txt", "moved.txt")
    (repo / "fresh.txt").write_text("new\n", encoding="utf-8")

    calls: list[list[str]] = []
    real = PN._git

    def counting(args, cwd, *, input=None):
        calls.append(list(args))
        return real(args, cwd, input=input)

    monkeypatch.setattr(PN, "_git", counting)
    view = PN.changes_view(row, [], "Claude Code")
    assert len(calls) <= 8, [c[:3] for c in calls]
    by = {c["rel"]: c for c in view["changes"]}
    assert set(by) >= {"with space.txt", "ünï.txt", "bin.dat", "gone.txt", "moved.txt", "fresh.txt", "f00.txt"}
    # Every file's diff is exactly what git says for that file alone.
    base = view["base"]
    full = subprocess.run(["git", "rev-parse", base], cwd=repo, capture_output=True, text=True).stdout.strip()
    for rel in ("f00.txt", "with space.txt", "ünï.txt", "gone.txt"):
        alone = subprocess.run(
            ["git", "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "-M", "--relative", full, "--", rel],
            cwd=repo, capture_output=True).stdout.decode("utf-8").replace("\r\n", "\n")
        assert by[rel]["diff"].replace("\r\n", "\n") == alone, rel
    assert "+changed" in by["f00.txt"]["diff"] and by["f00.txt"]["added"] == 1
    assert by["gone.txt"]["status"] == "deleted"
    assert by["moved.txt"].get("note") == "renamed from move-me.txt"
    assert by["bin.dat"].get("binary") is True and by["bin.dat"]["diff"] == "binary file"
    assert by["fresh.txt"]["status"] == "created"


# --------------------------------------------------------------------------- #
# Usage: Iron-Proxy's numbers carry the time Iron-Proxy saw them
# --------------------------------------------------------------------------- #
def test_iron_proxys_windows_carry_when_they_were_seen(tmp_path):
    from iron_jarvis.hosted import store
    from iron_jarvis.daemon.app import create_app
    import json

    app = create_app(str(tmp_path / "home"))
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    newer = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")

    class Proxy:
        enabled = True

        def cached_accounts(self):
            return (
                [{"id": "p1", "title": "Work", "provider": "anthropic", "lane": "cli"}],
                {"p1": {"status": "ready", "plan": "max", "windows": [
                    {"usedPercent": 63, "windowMins": 300, "resetsAt": soon, "at": newer},
                    {"usedPercent": 20, "windowMins": 10080, "resetsAt": soon, "at": old},
                ]}},
            )

    app.state.platform.iron_proxy = Proxy()
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(tmp_path), "unset": []}
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(tmp_path), account="p1",
                       account_json=json.dumps(rec))
    acc = PN.account_usage(app.state.platform, row)
    assert [w["at"] for w in acc["windows"]] == [newer, old]
    # The age said for the account is the OLDEST number's (never younger than
    # any number shown).
    assert acc["at"] == old


def test_a_combined_diff_whose_pieces_do_not_line_up_is_read_file_by_file(monkeypatch):
    """The one-child read trusts a piece only when it starts with the
    ``diff --git`` line predicted for ITS entry; pieces in another order (or a
    prefix setting such as ``diff.mnemonicPrefix``) read each file alone."""
    from iron_jarvis.git.integration import GitRun

    a = "diff --git a/a.txt b/a.txt\n@@ -1 +1 @@\n-a\n+A\n"
    b = "diff --git a/b.txt b/b.txt\n@@ -1 +1 @@\n-b\n+B\n"
    calls: list[list[str]] = []

    def fake(args, cwd, *, input=None):
        calls.append(list(args))
        tail = args[args.index("--") + 1:]
        if tail == ["a.txt", "b.txt"]:
            return GitRun(code=0, out=b + a, err="")  # the wrong order
        return GitRun(code=0, out={"a.txt": a, "b.txt": b}[tail[0]], err="")

    monkeypatch.setattr(PN, "_git", fake)
    got = PN._patches("C:/x", "abc", [("M", "a.txt", "a.txt"), ("M", "b.txt", "b.txt")])
    assert got == [a, b]
    assert len(calls) == 3


def test_a_rev_list_that_failed_in_a_repository_with_commits_is_not_no_commits(repo, monkeypatch):
    """Only HEAD naming no commit reads against the empty tree; a rev-list
    that FAILED (not a time limit) in a repository with commits would have
    listed every tracked file as created."""
    monkeypatch.setattr(PN, "_git", _failing("rev-list", err="fatal: bad object HEAD"))
    view = PN.changes_view(_row(repo), [], "Claude Code")
    assert view["source"] == "none" and view["changes"] == [], view
    assert view["words"].endswith(": bad object HEAD."), view


def test_a_diff_git_did_not_give_is_said_and_a_slow_one_is_not_retried_file_by_file(repo, monkeypatch):
    """A combined diff that ran out of time is not read again file by file
    (up to 50 more waits of 30 s), and a file whose diff git did not give says
    so — never an empty diff under real line counts."""
    from iron_jarvis.git.integration import GitRun

    for n in ("a.txt", "b.txt"):
        (repo / n).write_text("1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two", when="2020-01-02T00:00:00+00:00")
    row = _row(repo)
    for n in ("a.txt", "b.txt"):
        (repo / n).write_text("2\n", encoding="utf-8")
    real = PN._git
    diffs: list[list[str]] = []

    def slow(args, cwd, *, input=None):
        if args[0] == "diff" and "--name-status" not in args and "--numstat" not in args:
            diffs.append(list(args))
            return GitRun(code=-1, out="", err="", timed_out=True)
        return real(args, cwd, input=input)

    monkeypatch.setattr(PN, "_git", slow)
    view = PN.changes_view(row, [], "Codex")
    assert len(diffs) == 1, diffs
    by = {c["rel"]: c for c in view["changes"]}
    assert by["a.txt"]["added"] == 1 and by["a.txt"]["diff"] == ""
    assert by["a.txt"]["note"] == PN.DIFF_UNREAD and by["b.txt"]["note"] == PN.DIFF_UNREAD
