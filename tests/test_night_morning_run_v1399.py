"""v1.399.0 (night shift N2, "the morning") — THE PROOF, end to end.

A scripted night of three tasks in three scratch git repositories, the night
loop's own pass (``routes/night.night_pass``) driven by a clock the test
controls, the real app factory, real git worktrees, the real stream lane, the
real notifier; only the model is a scripted double and the night's card wait
is shortened:

* one task finishes with its tests PASSING (the finish check ran them), one
  NEEDS YOU (its own ``git commit`` — a safety card — is declined after the
  wait), one FAILS (the model breaks mid-way);
* each branch holds Iron Jarvis's OWN commit of the task's work with the
  task's words (decision 2) — the failed and needs-you tasks' work too — and
  the user's main checkouts are byte-identical;
* exactly ONE summary, ONE ``build.night_summary`` event (the bell's row) and
  ONE phone message, failed and needs-you first; a later pass makes neither
  a second summary nor a second notice;
* the summary's numbers equal the records (the night task, its reply line's
  finish record and receipt, the branch in git);
* Push and the pull request go through the session's existing git routes (a
  bare remote and a scripted ``gh``), and Throw away moves ONLY that task's
  folder to the trash and keeps its branch.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_morning as M
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import git, git_ok, install_fake_gh, make_repo, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    asgi_get,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    until,
    wire,
)

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable.replace("\\", "/")

TEST_CALC = '''import unittest

import calc


class Calc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)
'''


def _local(h: int, m: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 10, day, h, m).astimezone()


class NightScript(Script):
    """One scripted model for the whole night: each task's own moves, picked
    by the token in its question."""

    def __init__(self, plans: dict[str, list]) -> None:
        super().__init__([])
        self.plans = {k: list(v) for k, v in plans.items()}

    async def _next(self, system, messages, tools):
        first = next((str(getattr(m, "content", "")) for m in messages if getattr(m, "role", "") == "user"), "")
        key = next((k for k in self.plans if k in first), "")
        self.calls.append({"tools": [t["name"] for t in tools or []], "task": key})
        moves = self.plans.get(key) or []
        move = moves.pop(0) if (moves and tools) else "Done."
        if callable(move):
            move = await move(self)
        return move


async def _broke(script):
    raise RuntimeError("the model's server went away")


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


def _repo_with_tests(path: Path) -> Path:
    repo = make_repo(path)
    (repo / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_bytes(TEST_CALC.encode("utf-8"))
    (repo / ".gitignore").write_bytes(b"__pycache__/\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "calc", cwd=repo)
    return repo


def _github_remote(tmp_path: Path, repo: Path) -> Path:
    """origin reads from GitHub's address (the scripted gh answers for it)
    and PUSHES to a bare repository here — nothing leaves the PC."""
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("remote", "add", "origin", "https://github.com/acme/proj.git", cwd=repo)
    git("remote", "set-url", "--push", "origin", str(bare), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    return bare


def _task(app, task_id: str) -> dict:
    return N.get_task(app.state.platform.engine, task_id)


async def _ended(app, d, task_id: str) -> dict:
    end = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < end:
        await night_routes.night_pass(d)
        if _task(app, task_id)["state"] in N.ENDED:
            return _task(app, task_id)
        await asyncio.sleep(0.1)
    raise AssertionError(f"night task {task_id} did not end: {_task(app, task_id)}")


def _phone(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("no phone channel")


def _branch_files(repo: Path, branch: str) -> list[str]:
    base = git("merge-base", "main", branch, cwd=repo)
    out = git("diff", "--name-only", f"{base}..{branch}", cwd=repo)
    return [ln for ln in out.splitlines() if ln.strip()]


async def test_a_scripted_night_commits_each_branch_and_makes_one_summary_one_bell_row_one_phone_message(
    tmp_path, monkeypatch,
):
    repo_done = _repo_with_tests(tmp_path / "projDone")
    repo_needs = make_repo(tmp_path / "projNeeds")
    repo_fail = make_repo(tmp_path / "projFail")
    _github_remote(tmp_path, repo_needs)
    before = {p: snapshot(p) for p in (repo_done, repo_needs, repo_fail)}

    toasts: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: toasts.append(message))
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    platform = app.state.platform
    platform.config.night_max_at_once = 3
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 0.6)
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    phone = _phone(app)
    phone_before = len(phone.sent)
    notices: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: notices.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    # ONE notice per night, never one per task: a night task's turn is not
    # announced as "Your coding session finished" (every turn would be,
    # with this at 0 — a person's turn below still is).
    monkeypatch.setattr(build_routes, "FINISH_NOTICE_AFTER_S", 0.0)
    finished: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: finished.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_TURN_FINISHED else None
    )

    wire(app, NightScript({
        "TASK-DONE": [
            [ToolCall(id="d1", name="Write", arguments={
                "file_path": "calc.py", "content": "def add(a, b):\n    return a + b\n"})],
            [ToolCall(id="d2", name="Bash", arguments={"command": f'"{PY}" -m unittest -q test_calc'})],
            "Done TASK-DONE: add() adds now and its test passes.",
        ],
        "TASK-NEEDS": [
            [ToolCall(id="n1", name="Write", arguments={"file_path": "notes.md", "content": "# notes\n"})],
            [ToolCall(id="n2", name="Bash", arguments={"command": "git commit -am wip"})],
            "Done TASK-NEEDS: notes.md is there; the commit is left for you.",
        ],
        "TASK-FAIL": [
            [ToolCall(id="f1", name="Write", arguments={"file_path": "half.txt", "content": "half done\n"})],
            _broke,
        ],
    }))

    async def add(folder: Path, words: str) -> dict:
        status, task = await asgi(app, "POST", "/build/night", {"folder": str(folder), "words": words})
        assert status == 201, task
        return task

    t_done = await add(repo_done, "TASK-DONE: make add() add\n\nThe test in test_calc.py says how.")
    t_needs = await add(repo_needs, "TASK-NEEDS: write the notes file")
    t_fail = await add(repo_fail, "TASK-FAIL: write half.txt")

    out = await night_routes.night_pass(d)
    assert sorted(out["started"]) == sorted([t_done["id"], t_needs["id"], t_fail["id"]])
    assert out["summaries"] == []  # the night's work goes on

    done = await _ended(app, d, t_done["id"])
    needs = await _ended(app, d, t_needs["id"])
    fail = await _ended(app, d, t_fail["id"])
    assert (done["state"], needs["state"], fail["state"]) == (N.DONE, N.NEEDS_YOU, N.FAILED), (
        done["notes"], needs["notes"], fail["notes"])
    # Each task kept the night it ran in.
    assert done["night"] == needs["night"] == fail["night"] == "2026-10-26"

    # ---- (1) THE COMMIT: each branch holds Iron Jarvis's own commit, with the
    # task's words, the failed and needs-you tasks' work included.
    for task, repo, made in ((done, repo_done, "calc.py"), (needs, repo_needs, "notes.md"),
                             (fail, repo_fail, "half.txt")):
        branch = task["branch"]
        assert branch.startswith("ij/"), task
        assert task["commit_sha"], (task["notes"], task["morning"])
        assert git("rev-parse", "--short=12", branch, cwd=repo) == task["commit_sha"][:12]
        message = git("log", "-1", "--format=%B", branch, cwd=repo)
        first_words = task["words"].splitlines()[0]
        assert message.splitlines()[0] == first_words, message
        assert "Committed by Iron Jarvis when this night task ended" in message, message
        assert made in _branch_files(repo, branch)
        worktree = Path(setup_of(app, task["thread_id"])["git"]["worktree"])
        assert git("status", "--porcelain", cwd=worktree) == ""  # everything committed
        assert any(n["kind"] == "commit" and task["commit_sha"][:12] in n["text"] for n in task["notes"])
    assert "(done)" in git("log", "-1", "--format=%B", done["branch"], cwd=repo_done)
    assert "(needs you)" in git("log", "-1", "--format=%B", needs["branch"], cwd=repo_needs)
    assert "(failed)" in git("log", "-1", "--format=%B", fail["branch"], cwd=repo_fail)
    # The agent's OWN commit was declined (the safety card) — the only commit on
    # its branch is Iron Jarvis's.
    base = git("merge-base", "main", needs["branch"], cwd=repo_needs)
    assert git("rev-list", "--count", f"{base}..{needs['branch']}", cwd=repo_needs) == "1"
    # Nothing was pushed by the night.
    assert not git_ok("rev-parse", "--verify", f"refs/remotes/origin/{needs['branch']}", cwd=repo_needs)
    # The user's main checkouts are byte-identical.
    for repo, snap in before.items():
        assert snapshot(repo) == snap, repo

    # ---- (2) ONE summary, ONE notice, ONE phone message.
    await until(lambda: len(notices) == 1, 30)
    summaries = M.list_summaries(platform.engine)
    assert [s["night"] for s in summaries] == ["2026-10-26"]
    for _ in range(3):  # later passes add nothing
        again = await night_routes.night_pass(d)
        assert again["summaries"] == []
    await asyncio.sleep(0.3)
    assert len(notices) == 1, notices
    assert len(M.list_summaries(platform.engine)) == 1
    notice = notices[0]
    assert notice["title"] == "Last night: 1 failed, 1 needs you, 1 done", notice
    assert notice["body"].startswith("Failed: TASK-FAIL"), notice
    assert notice["counts"] == {"failed": 1, "needs_you": 1, "running": 0, "done": 1, "stopped": 0}
    await until(lambda: len(phone.sent) > phone_before, 30)
    sent = phone.sent[phone_before:]
    assert len(sent) == 1, sent
    lines = sent[0].splitlines()
    assert lines[0] == notice["title"]
    assert lines[1].startswith("• Failed: TASK-FAIL"), lines
    assert lines[2].startswith("• Needs you: TASK-NEEDS"), lines
    assert lines[3].startswith("• Done: TASK-DONE"), lines
    assert "tests passed" in lines[3] and "1 file changed" in lines[3], lines[3]
    assert "its work so far is committed" in lines[1] and "its work so far is committed" in lines[2]
    # "This PC" stays quiet: the bell (a window) or the tray says it.
    assert toasts == [], toasts
    # No night task's turn was announced on its own (the control: a person's
    # turn, at the same 0-second threshold, is).
    night_threads = {done["thread_id"], needs["thread_id"], fail["thread_id"]}
    assert not [f for f in finished if f.get("thread_id") in night_threads], finished
    plain = tmp_path / "day"
    plain.mkdir()
    sid_person = new_session(app, plain)["id"]
    status, raw = await asyncio.wait_for(open_turn(app, sid_person, {"text": "PERSON hi", "turn_id": "bt-day"}), 60)
    assert status == 200 and "event: done" in raw
    await until(lambda: any(f.get("thread_id") == sid_person for f in finished), 30)

    # ---- (3) THE NUMBERS EQUAL THE RECORDS (GET /build/night/summaries).
    status, view = await asgi_get(app, "/build/night/summaries")
    assert status == 200, view
    night = view["nights"][0]
    assert night["night"] == "2026-10-26" and night["label"] == "Monday, October 26"
    rows = night["rows"]
    assert [r["state"] for r in rows] == ["failed", "needs_you", "done"]
    by_id = {r["id"]: r for r in rows}
    for task, repo in ((done, repo_done), (needs, repo_needs), (fail, repo_fail)):
        row = by_id[task["id"]]
        record = _task(app, task["id"])
        assert row["branch"] == record["branch"]
        assert row["commit"]["sha"] == record["commit_sha"]
        assert row["seconds"] == record["ran_s"]
        assert row["files"] == len(_branch_files(repo, record["branch"]))
        replies = [m for m in messages_of(app, record["thread_id"]) if m["role"] == "assistant"]
        tokens_in = sum(int((m.get("usage") or {}).get("input_tokens") or 0) for m in replies)
        tokens_out = sum(int((m.get("usage") or {}).get("output_tokens") or 0) for m in replies)
        assert sum(u["input_tokens"] for u in row["usage"]) == tokens_in
        assert sum(u["output_tokens"] for u in row["usage"]) == tokens_out
        finish = (replies[-1].get("finish") or {}) if replies else {}
        assert row["tests"] == (finish.get("tests") if finish.get("tests") in ("passed", "failed") else "")
        assert row["session"]["exists"] and not row["session"]["pushed"]
    assert by_id[done["id"]]["tests"] == "passed"
    assert by_id[done["id"]]["files"] == 1
    assert by_id[needs["id"]]["needs"] and by_id[needs["id"]]["needs"][0].startswith("Needs your OK:")
    assert by_id[fail["id"]]["committed_words"].startswith("Its work so far is committed on ij/")
    assert by_id[needs["id"]]["committed_words"].startswith("Its work so far is committed on ij/")
    assert by_id[done["id"]]["committed_words"] == ""
    # The dashboard's fixture (dashboard/__tests__/fixtures/last-night-v1399.json)
    # holds the daemon's own keys — a key added or dropped here is a key the
    # Last night view would never read.
    fixture = json.loads((ROOT / "dashboard" / "__tests__" / "fixtures" / "last-night-v1399.json")
                         .read_text(encoding="utf-8"))
    fx_night = fixture["nights"][0]
    fx_row = fx_night["rows"][0]
    assert set(view) == set(fixture) - {"_about"}
    assert set(night) == set(fx_night)
    for r in rows:
        assert set(r) - {"failure"} == set(fx_row) - {"failure"}, set(r) ^ set(fx_row)
        assert set(r["session"]) == set(fx_row["session"])
        for u in r["usage"]:
            assert set(u) - {"cost_usd"} == set(fx_row["usage"][0]) - {"cost_usd"}
    assert "failure" in by_id[fail["id"]]

    # ---- (4) PUSH and the PULL REQUEST, through the session's own git routes.
    gh = install_fake_gh(tmp_path, monkeypatch)
    sid = needs["thread_id"]
    status, pushed = await asgi(app, "POST", f"/build/sessions/{sid}/git/push", {})
    assert status == 200, pushed
    assert git_ok("rev-parse", "--verify", f"refs/heads/{needs['branch']}", cwd=tmp_path / "remote.git")
    status, branch_view = await asgi_get(app, f"/build/sessions/{sid}/git")
    assert status == 200, branch_view
    draft = branch_view["pr_draft"]
    assert draft["title"] == "TASK-NEEDS: write the notes file"
    status, pr = await asgi(app, "POST", f"/build/sessions/{sid}/git/pr",
                            {"title": draft["title"], "body": draft["body"]})
    assert status == 200, pr
    assert pr["url"] == "https://github.com/acme/proj/pull/7"
    assert gh.read()["head"] == needs["branch"] and gh.read()["title"] == draft["title"]
    status, view = await asgi_get(app, "/build/night/summaries")
    row = {r["id"]: r for r in view["nights"][0]["rows"]}[needs["id"]]
    assert row["session"]["pushed"] is True and row["session"]["pr_url"] == pr["url"]

    # ---- (5) THROW AWAY: only that folder goes to the trash; the branch stays.
    sid_done = done["thread_id"]
    wt_done = Path(setup_of(app, sid_done)["git"]["worktree"])
    wt_fail = Path(setup_of(app, fail["thread_id"])["git"]["worktree"])
    status, refused = await asgi(app, "POST", f"/build/sessions/{sid_done}/git/discard", {})
    assert status == 400, refused  # one press is not enough
    assert wt_done.is_dir()
    status, gone = await asgi(app, "POST", f"/build/sessions/{sid_done}/git/discard", {"confirm": True})
    assert status == 200, gone
    assert not wt_done.exists()
    assert Path(gone["trash"]).is_dir() and (Path(gone["trash"]) / "calc.py").is_file()
    assert git_ok("rev-parse", "--verify", f"refs/heads/{done['branch']}", cwd=repo_done)
    assert git("rev-parse", "--short=12", done["branch"], cwd=repo_done) == done["commit_sha"][:12]
    assert wt_fail.is_dir()  # the other tasks' folders are untouched
    status, view = await asgi_get(app, "/build/night/summaries")
    row = {r["id"]: r for r in view["nights"][0]["rows"]}[done["id"]]
    assert row["session"]["removed"] is True and row["branch"] == done["branch"]
    assert snapshot(repo_done) == before[repo_done]


async def test_a_task_whose_main_folder_changed_still_commits_only_its_branch(tmp_path, monkeypatch):
    """N1's guard saw the user's MAIN folder change (a command wrote there):
    the task ends needs-you, its work is committed on ITS branch only — the
    stray file stays in the main folder untouched and uncommitted, the main
    branch does not move — and the summary says it open."""
    repo = make_repo(tmp_path / "proj")
    main_head = git("rev-parse", "main", cwd=repo)
    stray = (repo / "stray.txt").as_posix()
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    wire(app, NightScript({"TASK-STRAY": [
        [ToolCall(id="w1", name="Write", arguments={"file_path": "mine.txt", "content": "mine\n"})],
        [ToolCall(id="c1", name="Bash", arguments={"command": f'"{PY}" -c "open(\'{stray}\', \'w\').write(\'x\')"'})],
        "Done TASK-STRAY.",
    ]}))
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "TASK-STRAY: tidy up"})
    assert status == 201, task
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert (repo / "stray.txt").read_text() == "x", "the scripted command did not run"
    assert ended["state"] == N.NEEDS_YOU and ended["main_changed"] is True, ended["notes"]
    assert ended["commit_sha"]
    assert _branch_files(repo, ended["branch"]) == ["mine.txt"]
    assert git("rev-parse", "main", cwd=repo) == main_head
    assert git("status", "--porcelain", cwd=repo) == "?? stray.txt"
    [summary] = M.list_summaries(app.state.platform.engine)
    [row] = summary["rows"]
    assert row["main_changed"] is True and row["main_changed_words"] == next(
        n["text"] for n in ended["notes"] if n["kind"] == "outside")
    assert row["committed_words"].startswith("Its work so far is committed on ij/")


async def test_a_task_that_changed_nothing_gets_no_commit_and_says_so(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    wire(app, NightScript({"TASK-READ": [
        [ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"})],
        "Done TASK-READ: app.py sets x to 1.",
    ]}))
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "TASK-READ: read app.py"})
    assert status == 201, task
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["state"] == N.DONE, ended["notes"]
    assert ended["commit_sha"] == ""
    assert ended["morning"]["commit"] is None
    assert ended["morning"]["commit_note"] == M.NOTHING_CHANGED
    assert any(n["kind"] == "commit" and n["text"] == M.NOTHING_CHANGED for n in ended["notes"])
    base = git("merge-base", "main", ended["branch"], cwd=repo)
    assert git("rev-list", "--count", f"{base}..{ended['branch']}", cwd=repo) == "0"


async def test_a_commit_git_refuses_is_a_note_in_words_never_a_crash(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(b"#!/bin/sh\necho 'the hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    wire(app, NightScript({"TASK-HOOK": [
        [ToolCall(id="h1", name="Write", arguments={"file_path": "new.txt", "content": "new\n"})],
        "Done TASK-HOOK: new.txt is there.",
    ]}))
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "TASK-HOOK: write new.txt"})
    assert status == 201, task
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["state"] == N.DONE, ended["notes"]
    assert ended["commit_sha"] == ""
    note = ended["morning"]["commit_note"]
    assert note.startswith("Iron Jarvis could not commit its work: Git did not make the commit."), note
    assert "the hook says no" in note and note.endswith("Its changes are still in its branch folder.")
    # The work is still there, uncommitted, for the morning.
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    assert (worktree / "new.txt").read_text() == "new\n"
    assert ended["morning"]["uncommitted"] == 1 and ended["morning"]["files"] == 1


async def test_emergency_stop_leaves_the_work_uncommitted_in_its_branch_folder(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    platform = app.state.platform
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    gate = asyncio.Event()
    reached = asyncio.Event()

    async def hold(script):
        reached.set()
        await gate.wait()
        return "Never said."

    wire(app, NightScript({"TASK-STOP": [
        [ToolCall(id="s1", name="Write", arguments={"file_path": "s.txt", "content": "s\n"})],
        hold,
    ]}))
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "TASK-STOP: write s.txt"})
    assert status == 201, task
    await night_routes.night_pass(d)
    await asyncio.wait_for(reached.wait(), 60)
    platform.config.autonomy_kill_switch = True
    await asyncio.to_thread(platform.orchestrator.stop_unattended)
    ended = await _ended(app, d, task["id"])
    gate.set()
    assert ended["state"] == N.STOPPED, ended["notes"]
    assert ended["commit_sha"] == ""
    assert ended["morning"]["commit_note"] == M.EMERGENCY_NOT_COMMITTED
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    assert (worktree / "s.txt").is_file()
    assert git("status", "--porcelain", cwd=worktree) != ""
    # The night ended (Emergency stop holds every queued task): its summary is
    # made and announced once.
    summaries = M.list_summaries(platform.engine)
    assert [s["night"] for s in summaries] == ["2026-10-26"]
    assert summaries[0]["rows"][0]["state"] == "stopped"
    json.dumps(summaries)  # plain data
