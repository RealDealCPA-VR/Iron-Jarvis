"""v1.399.0 review of night shift N2 ("the morning").

This code COMMITS the user's repositories while they sleep and sends task
words to a phone. Each test here was red on the doer's cut (the merged
branch) before its fix — real scratch repositories, real git, the real
night pass and the real notifier; only the model is a scripted double.

* THE COMMIT TAKES ONLY WHAT IS SAFE TO KEEP: a night task's commit leaves
  out a file whose NAME or ADDED CONTENT looks like a secret (the key shapes
  the app already masks), a file over the size bound, and a dependency or
  cache folder it made — they stay in the branch folder, uncommitted, and
  the summary row names them.
* SECRETS NEVER LEAVE IN THE WORDS: the commit message, the summary row and
  the notice (the bell, the tray, the phone, every webhook) are masked.
* A GIT HOOK IS WATCHED TOO: the main-folder guard reads the user's main
  checkout AFTER Iron Jarvis's own commit (a post-commit hook that writes
  there is caught), and Emergency stop stops a hook that is still running
  (and git leaves no lock behind it for the morning's Commit button).
* LOCAL ONLY STAYS ABSOLUTE: a task whose session is Local only never has its
  words or its branch name in the notice that goes to the phone.
* NEVER A LOST NOTICE: a summary made but not yet announced (a crash between
  the two, a publish that failed) is announced on the next pass, once.
* A CRASH BETWEEN THE COMMIT AND THE RECORD keeps the commit as the task's
  (never "Nothing changed" beside a branch that holds Iron Jarvis's commit).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from iron_jarvis.comm.channels import DesktopChannel
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_commit as NC
from iron_jarvis.harness import night_morning as M
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import git, make_repo, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import asgi, asgi_get, messages_of, setup_of, until, wire
from tests.test_night_morning_run_v1399 import NightScript, _branch_files, _ended, _local, _phone, _task

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


# Secret-shaped values, built from pieces (never a real key in a test file).
OPENAI_SHAPE = "sk-" + "proj-" + "Ab3" * 12
GITHUB_SHAPE = "ghp" + "_" + "x1Y2" * 10
AWS_SHAPE = "AK" + "IA" + "QWERTYUIOPASDFGH"
ANTHROPIC_SHAPE = "sk-" + "ant-" + "api03-" + "Zz9" * 10
PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n-----END " + "OPENSSH PRIVATE KEY-----\n"


def _write(call_id: str, path: str, content: str) -> list:
    return [ToolCall(id=call_id, name="Write", arguments={"file_path": path, "content": content})]


def _hook(repo: Path, name: str, body: str) -> None:
    hook = repo / ".git" / "hooks" / name
    hook.write_bytes(("#!/bin/sh\n" + body).encode("utf-8"))
    hook.chmod(0o755)


async def _night(tmp_path, monkeypatch, plans: dict, *, at=(23, 30)):
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    monkeypatch.setattr(N, "CLOCK", lambda: _local(*at))
    wire(app, NightScript(plans))
    return app, d


async def _add(app, folder: Path, words: str) -> dict:
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(folder), "words": words})
    assert status == 201, task
    return task


def _committed(repo: Path, branch: str) -> set[str]:
    return set(_branch_files(repo, branch))


# --------------------------------------------------------------------------- #
# (1) what the night's commit takes
# --------------------------------------------------------------------------- #


async def test_the_night_commit_never_takes_a_secret_a_huge_file_or_a_dependency_folder(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    # A tracked file that ALREADY reads like a secret assignment (the user's
    # own code): a change to another line of it is still the task's work.
    (repo / "settings.py").write_bytes(b'import os\npassword = os.environ["DB_PASSWORD"]\n')
    # A tracked test fixture that already HOLDS a key-shaped fake: the night
    # adding a line to it is still committed (only a value it ADDED counts).
    (repo / "fixture_keys.py").write_bytes(f'FAKE = "{GITHUB_SHAPE}"\n'.encode("utf-8"))
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "settings", cwd=repo)
    monkeypatch.setattr(NC, "MAX_COMMIT_FILE_BYTES", 4096)
    app, d = await _night(tmp_path, monkeypatch, {"TASK-KEEP": [
        _write("w1", ".env", f"OPENAI_API_KEY={OPENAI_SHAPE}\n"),
        _write("w2", "deploy/id_ed25519", PRIVATE_KEY),
        _write("w3", "src/client.py", f'TOKEN = "{GITHUB_SHAPE}"\n'),
        _write("w4", "conf/aws.py", f'AWS_KEY_ID = "{AWS_SHAPE}"\n'),
        _write("w5", "big.txt", "a" * 8192),
        _write("w6", "node_modules/left/index.js", "module.exports = 1;\n"),
        _write("w7", ".env.example", "OPENAI_API_KEY=your-key-here\n"),
        _write("w8", "notes.md", "# notes\n"),
        _write("w9", "settings.py", 'import os\npassword = os.environ["DB_PASSWORD"]\nDEBUG = False\n'),
        _write("w10", "fixture_keys.py", f'FAKE = "{GITHUB_SHAPE}"\nOTHER = 2\n'),
        # A secret's own NAME, with nothing key-shaped inside.
        _write("w11", "home/.netrc", "machine example.com login me\n"),
        "Done TASK-KEEP.",
    ]})
    task = await _add(app, repo, "TASK-KEEP: tidy the config")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["commit_sha"], ended["notes"]
    committed = _committed(repo, ended["branch"])
    # Only what is safe to keep went into the branch's history.
    assert committed == {"notes.md", ".env.example", "settings.py", "fixture_keys.py"}, committed
    # The secret-shaped VALUES the night ADDED are in no commit of the branch
    # (the fixture's fake was there before; only its new line was added).
    history = git("log", "-p", "--format=%B", f"main..{ended['branch']}", cwd=repo)
    for shape in (OPENAI_SHAPE, AWS_SHAPE, "PRIVATE KEY"):
        assert shape not in history, shape
    assert f"+{GITHUB_SHAPE}" not in history and "TOKEN =" not in history
    # What was left out is still in the branch folder, uncommitted…
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    for rel in (".env", "deploy/id_ed25519", "src/client.py", "conf/aws.py", "big.txt",
                "node_modules/left/index.js", "home/.netrc"):
        assert (worktree / rel).is_file(), rel
    # …and the record and the summary row say so, by name and why.
    left = {x["path"]: x["why"] for x in ended["morning"]["left_out"]}
    assert set(left) == {".env", "deploy/id_ed25519", "src/client.py", "conf/aws.py", "big.txt",
                         "node_modules/left/index.js", "home/.netrc"}, left
    assert left[".env"] == "secret" and left["src/client.py"] == "secret" and left["conf/aws.py"] == "secret"
    assert left["home/.netrc"] == "secret"
    assert left["big.txt"] == "large" and left["node_modules/left/index.js"] == "dependency"
    [summary] = M.list_summaries(app.state.platform.engine)
    [row] = summary["rows"]
    said = " ".join(row["needs"])
    assert "not committed" in said and ".env" in said and "big.txt" in said, row["needs"]
    note = next(n["text"] for n in ended["notes"] if n["kind"] == "commit" and "not committed" in n["text"])
    assert note.startswith("7 files were not committed") and ".env (looks like a secret)" in note, note
    assert "big.txt (larger than 4 KB)" in note, note
    # The masked words never carry a value either.
    assert OPENAI_SHAPE not in json.dumps(summary)


async def test_a_night_commit_that_would_take_only_secrets_commits_nothing_and_says_so(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app, d = await _night(tmp_path, monkeypatch, {"TASK-ONLY": [
        _write("o1", ".env.local", f"KEY={OPENAI_SHAPE}\n"),
        "Done TASK-ONLY.",
    ]})
    task = await _add(app, repo, "TASK-ONLY: save the key")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["commit_sha"] == "", ended["notes"]
    assert git("rev-list", "--count", f"main..{ended['branch']}", cwd=repo) == "0"
    assert [x["path"] for x in ended["morning"]["left_out"]] == [".env.local"]
    assert ended["morning"]["commit_note"] != M.NOTHING_CHANGED
    assert "not committed" in ended["morning"]["commit_note"], ended["morning"]["commit_note"]
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    # Nothing is left staged: the morning's Commit button starts clean.
    assert git("diff", "--cached", "--name-only", cwd=worktree) == ""


# --------------------------------------------------------------------------- #
# (2) the words are masked: commit message, summary row, notice, phone
# --------------------------------------------------------------------------- #


async def test_a_key_in_the_tasks_words_reaches_no_commit_no_row_and_no_notice(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    toasts: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: toasts.append(message))
    app, d = await _night(tmp_path, monkeypatch, {"TASK-MASK": [
        _write("m1", "call.py", "print('hi')\n"),
        "Done TASK-MASK.",
    ]})
    platform = app.state.platform
    phone = _phone(app)
    before = len(phone.sent)
    notices: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: notices.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    words = f"TASK-MASK: call the API with {ANTHROPIC_SHAPE} and log it\n\nThe key is {ANTHROPIC_SHAPE}."
    task = await _add(app, repo, words)
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["commit_sha"], ended["notes"]
    message = git("log", "-1", "--format=%B", ended["branch"], cwd=repo)
    assert ANTHROPIC_SHAPE not in message, message
    assert message.startswith("TASK-MASK: call the API with sk-ant-***"), message
    await until(lambda: len(notices) == 1, 30)
    assert ANTHROPIC_SHAPE not in json.dumps(notices[0])
    [summary] = M.list_summaries(platform.engine)
    assert ANTHROPIC_SHAPE not in json.dumps(summary)
    await until(lambda: len(phone.sent) > before, 30)
    assert all(ANTHROPIC_SHAPE not in s for s in phone.sent[before:])


# --------------------------------------------------------------------------- #
# (3) hooks: the guard reads the main folder AFTER the commit; Emergency stop
# stops a hook that still runs
# --------------------------------------------------------------------------- #


async def test_a_hook_that_writes_into_the_main_folder_is_caught_by_the_guard(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    _hook(repo, "post-commit", f'echo hooked > "{(repo / "hooked.txt").as_posix()}"\n')
    app, d = await _night(tmp_path, monkeypatch, {"TASK-HOOKED": [
        _write("k1", "mine.txt", "mine\n"),
        "Done TASK-HOOKED.",
    ]})
    task = await _add(app, repo, "TASK-HOOKED: write mine.txt")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert (repo / "hooked.txt").is_file(), "the hook did not run"
    assert ended["commit_sha"], ended["notes"]
    assert ended["main_changed"] is True, ended["notes"]
    assert ended["state"] == N.NEEDS_YOU, ended["notes"]
    outside = next(n["text"] for n in ended["notes"] if n["kind"] == "outside")
    assert "YOUR MAIN FOLDER CHANGED" in outside
    [summary] = M.list_summaries(app.state.platform.engine)
    assert summary["rows"][0]["main_changed"] is True


async def test_the_nights_own_commit_is_never_read_as_a_main_folder_change(tmp_path, monkeypatch):
    """The control for the guard's new order: a clean task's commit (its ij/
    branch moved) leaves the main folder unchanged in the guard's eyes."""
    repo = make_repo(tmp_path / "proj")
    before = snapshot(repo)
    app, d = await _night(tmp_path, monkeypatch, {"TASK-CLEAN": [
        _write("c1", "clean.txt", "clean\n"),
        "Done TASK-CLEAN.",
    ]})
    task = await _add(app, repo, "TASK-CLEAN: write clean.txt")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["commit_sha"] and ended["state"] == N.DONE, ended["notes"]
    assert ended["main_changed"] is False, ended["notes"]
    assert snapshot(repo) == before


@pytest.mark.timeout(240)
async def test_emergency_stop_stops_a_commit_hook_that_is_still_running(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    started = tmp_path / "hook-started"
    finished = tmp_path / "hook-finished"
    _hook(repo, "pre-commit",
          f'touch "{started.as_posix()}"\nsleep 40\ntouch "{finished.as_posix()}"\n')
    app, d = await _night(tmp_path, monkeypatch, {"TASK-SLOWHOOK": [
        _write("s1", "slow.txt", "slow\n"),
        "Done TASK-SLOWHOOK.",
    ]})
    platform = app.state.platform
    task = await _add(app, repo, "TASK-SLOWHOOK: write slow.txt")
    await night_routes.night_pass(d)

    async def drive():
        while _task(app, task["id"])["state"] not in N.ENDED:
            await night_routes.night_pass(d)
            await asyncio.sleep(0.1)

    driver = asyncio.create_task(drive())
    try:
        assert await until(lambda: started.exists(), 120), "the hook never started"
        t0 = time.monotonic()
        platform.config.autonomy_kill_switch = True
        await asyncio.wait_for(asyncio.shield(driver), 20)
        took = time.monotonic() - t0
    finally:
        if not driver.done():
            driver.cancel()
    assert took < 20, took
    ended = _task(app, task["id"])
    assert ended["commit_sha"] == "", ended["notes"]
    assert git("rev-list", "--count", f"main..{ended['branch']}", cwd=repo) == "0"
    note = ended["morning"]["commit_note"]
    assert "Emergency stop" in note and "still in its branch folder" in note, note
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    assert (worktree / "slow.txt").is_file()
    # The hook was stopped (it never reached its end), and no git lock is left
    # behind for the morning's Commit button (measured: nothing to clean up).
    await asyncio.sleep(1.0)
    assert not finished.exists()
    gitdir = Path(git("rev-parse", "--absolute-git-dir", cwd=worktree))
    assert not (gitdir / "index.lock").exists()
    git("add", "-A", cwd=worktree)  # a lock would refuse this


# --------------------------------------------------------------------------- #
# (4) Local only stays absolute: no words to the phone
# --------------------------------------------------------------------------- #


async def test_a_local_only_tasks_words_and_branch_never_reach_the_phone(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    gate = asyncio.Event()
    reached = asyncio.Event()

    async def hold(script):
        reached.set()
        await gate.wait()
        return "Done TASK-SECRETPLAN."

    app, d = await _night(tmp_path, monkeypatch, {"TASK-SECRETPLAN": [
        _write("l1", "plan.txt", "plan\n"),
        hold,
    ]})
    platform = app.state.platform
    phone = _phone(app)
    before = len(phone.sent)
    notices: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: notices.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    task = await _add(app, repo, "TASK-SECRETPLAN: merge acme into globex quietly")
    await night_routes.night_pass(d)
    await asyncio.wait_for(reached.wait(), 60)
    sid = _task(app, task["id"])["thread_id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": True})
    assert status == 200
    gate.set()
    ended = await _ended(app, d, task["id"])
    branch = ended["branch"]
    assert branch
    await until(lambda: len(notices) == 1, 30)
    payload = json.dumps(notices[0])
    assert "acme" not in payload and "SECRETPLAN" not in payload and branch not in payload, payload
    await until(lambda: len(phone.sent) > before, 30)
    sent = "\n".join(phone.sent[before:])
    assert "acme" not in sent and branch not in sent, sent
    assert "Local only" in sent, sent
    # On this PC the Last night view still shows the task in full.
    status, view = await asgi_get(app, "/build/night/summaries")
    row = view["nights"][0]["rows"][0]
    assert "acme" in row["title"] and row["branch"] == branch


# --------------------------------------------------------------------------- #
# (5) never a lost notice; one notice even then
# --------------------------------------------------------------------------- #


async def _ended_quiet_night(tmp_path, monkeypatch, token: str):
    repo = make_repo(tmp_path / f"proj-{token}")
    app, d = await _night(tmp_path, monkeypatch, {token: [_write("q1", "q.txt", "q\n"), f"Done {token}."]})
    platform = app.state.platform
    notices: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: notices.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    return repo, app, d, notices


async def test_a_summary_made_but_never_announced_is_announced_on_the_next_pass(tmp_path, monkeypatch):
    """Iron Jarvis stopped (or the claim failed) after the summary was made and
    before its notice: the next pass announces it — once."""
    repo, app, d, notices = await _ended_quiet_night(tmp_path, monkeypatch, "TASK-CRASH")
    real_claim = M.claim_notice
    calls = {"n": 0}

    def claim_fails_once(engine, key):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real_claim(engine, key)

    monkeypatch.setattr(M, "claim_notice", claim_fails_once)
    task = await _add(app, repo, "TASK-CRASH: write q.txt")
    await night_routes.night_pass(d)
    await _ended(app, d, task["id"])
    for _ in range(3):
        await night_routes.night_pass(d)
    await until(lambda: len(notices) >= 1, 15)
    await asyncio.sleep(0.3)
    assert len(notices) == 1, notices
    [summary] = M.list_summaries(app.state.platform.engine)
    assert summary["notified"] is True


async def test_a_notice_whose_publish_failed_is_announced_once_later(tmp_path, monkeypatch):
    repo, app, d, notices = await _ended_quiet_night(tmp_path, monkeypatch, "TASK-PUB")
    bus = app.state.platform.event_bus
    real_publish = bus.publish
    calls = {"n": 0}

    async def publish_fails_once(type, payload=None, session_id=None):
        if type == EventType.BUILD_NIGHT_SUMMARY:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("the bus is gone")
        return await real_publish(type, payload, session_id=session_id)

    monkeypatch.setattr(bus, "publish", publish_fails_once)
    task = await _add(app, repo, "TASK-PUB: write q.txt")
    await night_routes.night_pass(d)
    await _ended(app, d, task["id"])
    for _ in range(3):
        await night_routes.night_pass(d)
    await until(lambda: len(notices) >= 1, 15)
    await asyncio.sleep(0.3)
    assert len(notices) == 1, notices


# --------------------------------------------------------------------------- #
# (6) a crash between the commit and the record
# --------------------------------------------------------------------------- #


async def test_a_crash_between_the_commit_and_its_record_keeps_the_commit_as_the_tasks(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app, d = await _night(tmp_path, monkeypatch, {"TASK-LOST": [
        _write("x1", "lost.txt", "lost\n"),
        "Done TASK-LOST.",
    ]})
    task = await _add(app, repo, "TASK-LOST: write lost.txt")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    sha = git("rev-parse", "--short=12", ended["branch"], cwd=repo)
    assert ended["commit_sha"][:12] == sha
    # As if Iron Jarvis stopped right after git made the commit: no record of it.
    engine = app.state.platform.engine
    N.set_morning(engine, task["id"], {})
    from iron_jarvis.core.db import session_scope

    with session_scope(engine) as db:
        rec = db.get(N.NightTaskRecord, task["id"])
        rec.commit_sha = ""
        db.add(rec)
        db.commit()
    blank = _task(app, task["id"])
    assert blank["commit_sha"] == "" and blank["morning"] == {}
    notes: list = []
    git_facts = setup_of(app, blank["thread_id"])["git"]
    await night_routes._settle_morning(d, blank, blank["thread_id"], git_facts, blank["state"],
                                       messages_of(app, blank["thread_id"]), None, True, False, notes)
    again = _task(app, task["id"])
    assert again["commit_sha"][:12] == sha, (again["morning"], notes)
    assert again["morning"]["commit"]["sha"][:12] == sha
    assert again["morning"]["commit_note"] != M.NOTHING_CHANGED
    assert not [n for n in notes if n[1] == M.NOTHING_CHANGED], notes


async def test_local_only_turned_on_after_the_task_ended_still_keeps_its_words_off_the_phone(tmp_path, monkeypatch):
    """The user turns Local only on for a session after its task ended, before
    the night's summary is made: the notice reads the session as it is NOW."""
    repo_a = make_repo(tmp_path / "projA")
    repo_b = make_repo(tmp_path / "projB")
    gate = asyncio.Event()
    reached = asyncio.Event()

    async def hold(script):
        reached.set()
        await gate.wait()
        return "Done TASK-LATER."

    app, d = await _night(tmp_path, monkeypatch, {
        "TASK-EARLY": [_write("e1", "e.txt", "e\n"), "Done TASK-EARLY."],
        "TASK-LATER": [_write("l1", "l.txt", "l\n"), hold],
    })
    platform = app.state.platform
    notices: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: notices.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    early = await _add(app, repo_a, "TASK-EARLY: write the quarterly acme numbers")
    later = await _add(app, repo_b, "TASK-LATER: write l.txt")
    await night_routes.night_pass(d)
    await asyncio.wait_for(reached.wait(), 60)
    ended = await _ended(app, d, early["id"])
    assert not notices  # the night's other task still runs
    status, _ = await asyncio.wait_for(
        asgi(app, "PATCH", f"/build/sessions/{ended['thread_id']}", {"local_only": True}), 30)
    assert status == 200
    gate.set()
    await _ended(app, d, later["id"])
    await until(lambda: len(notices) == 1, 30)
    payload = json.dumps(notices[0])
    assert "acme" not in payload and ended["branch"] not in payload, payload
    assert "TASK-LATER" in payload  # the other task's words are its own to send


def test_a_stopped_row_says_why_in_words_and_the_phone_line_carries_it():
    task = {
        "id": "t1", "thread_id": "th", "words": "TASK-S: tidy", "folder": "C:/p", "branch": "ij/x", "state": "stopped",
        "ran_s": 12.0,
        "notes": [{"kind": "limit", "text": "Stopped at its 90-minute limit; the reply says what it got done."},
                  {"kind": "info", "text": "Its commands ran as you."}],
        "morning": {},
    }
    row = M.summary_row(task)
    assert row["needs"][0].startswith("Stopped at its 90-minute limit"), row["needs"]
    assert "90-minute limit" in M.phone_line(row)
    emergency = dict(task, notes=[{"kind": "emergency", "text": N.EMERGENCY_NOTE}])
    assert M.summary_row(emergency)["needs"][0] == N.EMERGENCY_NOTE


def test_a_local_only_row_leaves_nothing_of_its_words_in_the_notice():
    row = M.summary_row({
        "id": "t1", "thread_id": "th", "words": "TASK-L: merge acme into globex", "folder": "C:/p",
        "branch": "ij/task-l-merge-acme-into-globex-1a2b", "state": "failed", "ran_s": 1.0,
        "notes": [{"kind": "error", "text": "acme refused"}, {"kind": "declined", "text": "Needs your OK: acme"}],
        "morning": {"local_only": True, "commit": {"sha": "abc123abc123", "subject": "acme"}},
    })
    payload = M.notice_payload("2026-10-26", [row])
    text = json.dumps(payload)
    assert "acme" not in text and "globex" not in text, text
    assert payload["lines"][0] == f"• Failed: {M.LOCAL_ONLY_TITLE}: {M.LOCAL_ONLY_LINE}."
    assert M.phone_message(payload).count("acme") == 0


@pytest.mark.parametrize("words", [
    "-n --amend: do the thing",
    "   \n\t  ",
    "fix the \"quoted\" and 'single' parts",
    "line one\r\nline two\rline three",
    "nul\x00inside and bell\x07 too",
    "Prüfe die Größe — 日本語のテスト",
    "x" * 400,
])
def test_the_commit_message_reaches_git_whole_whatever_the_words(tmp_path, words):
    from iron_jarvis.harness import branches as B

    repo = make_repo(tmp_path / "proj")
    facts = B.start(str(repo), title="msg")
    (Path(facts["worktree"]) / "m.txt").write_bytes(b"m\n")
    message = M.commit_message(words, "done")
    done = B.commit(facts, message)
    assert done["committed"] and done["sha"]
    got = git("log", "-1", "--format=%B", facts["branch"], cwd=repo)
    assert got.strip() == message.strip()
    assert "\x00" not in got and "\r" not in got and "\x07" not in got
    assert got.splitlines()[-1].startswith(M.TRAILER)
    assert len(got.splitlines()[0]) <= M.SUBJECT_CHARS
    assert git("rev-parse", "main", cwd=repo) == git("rev-parse", f"{facts['branch']}~1", cwd=repo)


async def test_the_nights_commit_refuses_a_worktree_moved_off_its_branch_or_gone_and_touches_no_other_branch(
        tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes.build_git import commit_for_night

    repo = make_repo(tmp_path / "proj")
    app, d = await _night(tmp_path, monkeypatch, {"TASK-MOVED": [_write("v1", "v.txt", "v\n"), "Done TASK-MOVED."]})
    task = await _add(app, repo, "TASK-MOVED: write v.txt")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    sid = ended["thread_id"]
    worktree = Path(setup_of(app, sid)["git"]["worktree"])
    main_head = git("rev-parse", "main", cwd=repo)
    branch_head = git("rev-parse", ended["branch"], cwd=repo)

    # (a) on ANOTHER branch: refused in words, nothing committed anywhere.
    git("switch", "-q", "-c", "elsewhere", cwd=worktree)
    (worktree / "w.txt").write_bytes(b"w\n")
    done = await commit_for_night(d, sid, M.commit_message("TASK-MOVED: again", "done"))
    assert not done["committed"] and "instead of its branch" in done["note"] and "elsewhere" in done["note"], done
    assert git("rev-parse", "elsewhere", cwd=repo) == branch_head
    assert git("rev-parse", "main", cwd=repo) == main_head
    assert git("diff", "--cached", "--name-only", cwd=worktree) == ""

    # (b) DETACHED: refused in words.
    git("switch", "-q", "--detach", cwd=worktree)
    done = await commit_for_night(d, sid, M.commit_message("TASK-MOVED: again", "done"))
    assert not done["committed"] and "detached" in done["note"], done
    assert git("rev-parse", "main", cwd=repo) == main_head

    # (c) the folder is GONE: refused in words.
    git("switch", "-q", ended["branch"], cwd=worktree)
    git("worktree", "remove", "--force", str(worktree), cwd=repo)
    done = await commit_for_night(d, sid, M.commit_message("TASK-MOVED: again", "done"))
    assert not done["committed"] and done["note"].startswith("Iron Jarvis could not commit its work"), done
    assert git("rev-parse", "main", cwd=repo) == main_head
    assert git("rev-parse", ended["branch"], cwd=repo) == branch_head


async def test_a_task_that_ran_local_only_keeps_its_words_off_the_phone_even_when_it_is_turned_off_after(
        tmp_path, monkeypatch):
    """Local only was ON while the task ran and is turned OFF before the
    night's summary is sent: what ran Local only stays Local only (the settle
    keeps it on the record; the session as it is now is not the only word)."""
    repo_a = make_repo(tmp_path / "projA")
    repo_b = make_repo(tmp_path / "projB")
    gates = {k: asyncio.Event() for k in ("early", "later")}
    reached = {k: asyncio.Event() for k in ("early", "later")}

    def holder(key: str, words: str):
        async def hold(script):
            reached[key].set()
            await gates[key].wait()
            return words
        return hold

    app, d = await _night(tmp_path, monkeypatch, {
        "TASK-EARLY": [_write("e1", "e.txt", "e\n"), holder("early", "Done TASK-EARLY.")],
        "TASK-LATER": [_write("l1", "l.txt", "l\n"), holder("later", "Done TASK-LATER.")],
    })
    platform = app.state.platform
    notices: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: notices.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    early = await _add(app, repo_a, "TASK-EARLY: the quarterly acme numbers")
    later = await _add(app, repo_b, "TASK-LATER: write l.txt")
    await night_routes.night_pass(d)
    await asyncio.wait_for(reached["early"].wait(), 60)
    await asyncio.wait_for(reached["later"].wait(), 60)
    sid = _task(app, early["id"])["thread_id"]
    status, _ = await asyncio.wait_for(asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": True}), 30)
    assert status == 200
    gates["early"].set()
    ended = await _ended(app, d, early["id"])
    assert ended["morning"]["local_only"] is True
    status, _ = await asyncio.wait_for(asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": False}), 30)
    assert status == 200
    gates["later"].set()
    await _ended(app, d, later["id"])
    await until(lambda: len(notices) == 1, 30)
    payload = json.dumps(notices[0])
    assert "acme" not in payload and ended["branch"] not in payload, payload


@pytest.mark.parametrize("secret_file", [".env", "config.py"])
async def test_a_hook_that_stages_everything_again_never_slips_a_secret_into_the_commit(
        tmp_path, monkeypatch, secret_file):
    """The repository's own pre-commit hook runs `git add -A` (a formatter
    hook often does): it stages what the screen left out back into the
    commit — a new secret file, or a key added to a file the branch already
    tracked. What WENT INTO the commit is checked again; a commit that holds
    what must not be committed is undone (Iron Jarvis's own, seconds old,
    never pushed) and the work stays in the branch folder."""
    repo = make_repo(tmp_path / "proj")
    (repo / "config.py").write_bytes(b"DEBUG = True\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "config", cwd=repo)
    _hook(repo, "pre-commit", "git add -A\n")
    content = f"KEY={OPENAI_SHAPE}\n" if secret_file == ".env" else f'DEBUG = True\nAWS = "{AWS_SHAPE}"\n'
    shape = OPENAI_SHAPE if secret_file == ".env" else AWS_SHAPE
    app, d = await _night(tmp_path, monkeypatch, {"TASK-RESTAGE": [
        _write("r1", secret_file, content),
        _write("r2", "ok.txt", "ok\n"),
        "Done TASK-RESTAGE.",
    ]})
    task = await _add(app, repo, "TASK-RESTAGE: write two files")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    history = git("log", "-p", "--format=%B", f"main..{ended['branch']}", cwd=repo)
    assert shape not in history, history
    assert secret_file not in _committed(repo, ended["branch"])
    assert ended["commit_sha"] == "", ended["notes"]
    assert "hook" in ended["morning"]["commit_note"], ended["morning"]["commit_note"]
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    assert (worktree / secret_file).read_text() == content and (worktree / "ok.txt").is_file()
    assert git("diff", "--cached", "--name-only", cwd=worktree) == ""


async def test_a_screen_whose_unstaging_fails_commits_nothing(tmp_path, monkeypatch):
    """If git cannot leave out what must not be committed, NOTHING is
    committed — never the whole folder with the secret in it."""
    from iron_jarvis.git.integration import GitRun
    from iron_jarvis.harness import branches as B

    repo = make_repo(tmp_path / "proj")
    real_g = B._g

    def refusing_g(args, cwd, **kw):
        if "--pathspec-from-file=-" in args:
            return GitRun(code=128, out="", err="fatal: refused for the test")
        return real_g(args, cwd, **kw)

    monkeypatch.setattr(B, "_g", refusing_g)
    app, d = await _night(tmp_path, monkeypatch, {"TASK-NOSCREEN": [
        _write("u1", ".env", f"KEY={OPENAI_SHAPE}\n"),
        _write("u2", "ok.txt", "ok\n"),
        "Done TASK-NOSCREEN.",
    ]})
    task = await _add(app, repo, "TASK-NOSCREEN: write two files")
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["commit_sha"] == "", ended["notes"]
    assert git("rev-list", "--count", f"main..{ended['branch']}", cwd=repo) == "0"
    assert "could not leave out" in ended["morning"]["commit_note"], ended["morning"]
    worktree = Path(setup_of(app, ended["thread_id"])["git"]["worktree"])
    assert git("diff", "--cached", "--name-only", cwd=worktree) == ""  # nothing left staged


async def test_a_row_that_ended_after_the_summary_is_brought_up_to_date_without_its_settle(tmp_path, monkeypatch):
    """Iron Jarvis stopped between a task's end and the pass that would have
    refreshed its row: the next pass reads the records, not only its own
    settle, and the row stops saying "still running"."""
    from tests.test_night_morning_v1399 import _put, _set_state

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    clock = {"now": _local(7, 1, day=27)}  # the window closed
    monkeypatch.setattr(N, "CLOCK", lambda: clock["now"])
    _put(engine, "a", N.DONE)
    _put(engine, "b", N.RUNNING)
    assert await night_routes.morning_pass(app.state.d, []) == ["2026-10-26"]
    _set_state(engine, "b", N.FAILED)
    assert await night_routes.morning_pass(app.state.d, []) == []  # no "settled" for b
    [summary] = M.list_summaries(engine)
    assert [r["state"] for r in summary["rows"]] == ["failed", "done"]
    assert M.rows_differ(engine, "2026-10-26", M.rows_for_night(N.list_tasks(engine), "2026-10-26", "23:00")) is False


async def test_two_passes_at_once_make_one_summary_and_one_notice(tmp_path, monkeypatch):
    from tests.test_night_morning_v1399 import _put

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    seen: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: seen.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    _put(platform.engine, "a", N.DONE)
    # Past the pass lock on purpose: the summary's own guards hold.
    made = await asyncio.gather(*(night_routes.morning_pass(app.state.d, ["a"]) for _ in range(4)))
    assert sorted(k for m in made for k in m) == ["2026-10-26"]
    await until(lambda: len(seen) >= 1, 15)
    await asyncio.sleep(0.3)
    assert len(seen) == 1, seen


@pytest.mark.parametrize("wall, fold, want", [
    ((2026, 11, 1, 0, 30), 0, "2026-10-31"),   # the night the clocks go back: after midnight, before
    ((2026, 11, 1, 1, 30), 1, "2026-10-31"),   # the repeated hour, its second pass
    ((2026, 11, 1, 6, 59), 0, "2026-10-31"),
    ((2026, 11, 1, 23, 30), 0, "2026-11-01"),
    ((2027, 3, 14, 3, 30), 0, "2027-03-13"),   # the night the clocks go forward
])
def test_the_night_is_the_date_its_window_opened_across_a_clock_change(wall, fold, want):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    at = datetime(*wall, fold=fold, tzinfo=ZoneInfo("America/New_York"))
    assert M.night_key(at, "23:00", "07:00") == want
    # The clock the loop really reads: this PC's local time with a FIXED offset.
    assert M.night_key(at.astimezone(at.utcoffset() and timezone(at.utcoffset())), "23:00", "07:00") == want
    # And a task an older Iron Jarvis started is placed by its UTC start the same way.
    began = at.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    assert M.night_key(M._parse_iso(began).astimezone(ZoneInfo("America/New_York")), "23:00") == want


def test_the_left_out_line_fits_the_row_and_counts_what_it_does_not_name():
    """The Last night view shows a row's line up to 400 characters
    (lib/nightShift.decodeRow): the sentence about what the commit left out
    fits, names whole paths, and counts the rest."""
    left = [{"path": f"very/long/folder/name/number-{i:02d}/" + "x" * 50 + ".env", "why": NC.SECRET}
            for i in range(30)]
    words = NC.left_out_words(left)
    assert len(words) <= NC.WORDS_CHARS <= 400, len(words)
    assert words.startswith("30 files were not committed and are still in its branch folder: ")
    named = words.count("(looks like a secret)")
    assert 1 <= named < 30 and f", and {30 - named} more." in words
    assert words.endswith("Look at them before you commit or push them yourself.")
    one = NC.left_out_words([{"path": ".env", "why": NC.SECRET}])
    assert one == ("1 file was not committed and is still in its branch folder: .env (looks like a secret). "
                   "Look at it before you commit or push it yourself.")
