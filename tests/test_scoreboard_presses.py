"""Next leaps 28-a (R2.17), the review's second round: the seams the first
round left unpinned, each through the REAL route (or the real daemon start),
each with a second press that counts nothing twice.

Real git in a scratch repository, a bare repository as the remote (its FETCH
address says github.com, its PUSH address is the bare one, so nothing leaves
this PC) and a FAKE ``gh`` on PATH (``tests/test_build_git_v1375``): never the
real GitHub, never a real coding tool (the in-process stand-in), a model or
the running app.

Pinned:
* the Build › Code check listener, installed by the daemon's own start: a
  reply that changed files only through a command enters the record from
  Iron Jarvis's own check, with that check's verdict, once;
* Build › Code: Open a pull request (a branch session and a session in the
  folder on its own branch), Merge and Discard each settle the session's
  undecided work; a later press never re-decides it;
* Chat › Code: Open a pull request keeps the session's work;
* Delete, in both views: a session on its own branch takes the Discard path,
  so its undecided work is thrown away and stays on record after the session
  goes; a session in the folder directly leaves its work not decided (it is
  still in the folder);
* Undo rewind (Build › Code): the rewind's thrown pieces are not decided
  again, and the next Commit keeps them; the files-only rewind throws them;
* a Build › Code write outside the session's folder is no piece.

The review's third round:
* a Build › Code reply's check is credited to the tool and model its
  message was SENT to, never the ones the session has when the check ends (a
  switch while the tests ran); a message this process did not send only
  gives its check to a row that exists;
* Chat › Code in the folder directly (the default: no Commit of Iron
  Jarvis's): the user's OWN commit keeps a reply when the menu reads the
  record, only once every file the reply named is committed after it ended
  and nothing of it is uncommitted; never a commit the coding model may
  have made, never while a reply runs;
* a rewind in a folder that is not a git repository decides nothing (there
  nothing can ever be kept).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import scoreboard as SB
from iron_jarvis.harness import sessions as HS
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import proof as PROOF
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make
from tests._build_app_helpers_v1409 import client, stop_all
from tests.test_build_app_git_v1 import github_remote
from tests.test_build_app_history_v1418 import fake_tool, until
from tests.test_build_app_proof_v1 import CHECK_SCRIPT, PASSING, command, keep, proofs, reply, settle
from tests.test_build_app_rewind_files_v1543 import _git as _rf_git
from tests.test_build_app_rewind_files_v1543 import _plain_session
from tests.test_build_git_v1375 import git, install_fake_gh, make_repo, new_branch_session, no_real_gh, turn
from tests.test_build_sessions_v1367 import Script, asgi, wire
from tests.test_scoreboard_seams import CLAUDE, edit_move, ref, tid

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

#: A FAILURE bound for waits on git and a real check run, never a speed claim.
WAIT_S = 120.0
HAND = ("-c", "user.email=dev@example.com", "-c", "user.name=Dev")


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    no_real_gh(monkeypatch)
    yield
    B.forget_gh_state()


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    return make_repo(tmp_path / "proj")


def rows_of(app, session_id: str) -> dict[str, tuple[str, str]]:
    with session_scope(app.state.platform.engine) as db:
        got = db.exec(select(SB.CodingOutcome).where(SB.CodingOutcome.session_id == session_id)).all()
        return {r.ref: (r.verdict, r.how) for r in got}


def committed_by_hand(folder: Path, name: str, body: str) -> None:
    """The user commits in the folder with their own git: no press of Iron
    Jarvis's, so nothing is decided by it."""
    (folder / name).write_text(body, encoding="utf-8")
    git("add", "-A", cwd=folder)
    git(*HAND, "commit", "-q", "-m", f"add {name}", cwd=folder)


async def branch_session(c, app, repo: Path) -> tuple[str, Path]:
    """A Build › Code session on its own branch with one reply on record."""
    r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo), "branch": True})
    assert r.status_code == 201, r.text
    made = r.json()
    assert SB.note_hosted_reply(app.state.platform.engine, made["id"], "t1") is True
    return made["id"], Path(made["folder"])


# --------------------------------------------------------------------------- #
# The check listener, installed by the daemon's own start
# --------------------------------------------------------------------------- #


async def test_a_reply_that_changed_files_only_through_a_command_is_noted_by_iron_jarvis_s_check(
        app, tmp_path, monkeypatch):
    """The lifespan installs the listener; a finished check reaches it through
    ``proof._tell``; the reply (no file step of its own, so its end noted
    nothing) becomes one piece with the check's verdict. Told again, it is
    still one piece."""
    work = tmp_path / "work"
    work.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "check.py").write_text(CHECK_SCRIPT, encoding="utf-8")
    (work / "test_limits.py").write_text(PASSING, encoding="utf-8")
    _rf_git(work, "init", "-q", "-b", "main")
    _rf_git(work, "add", "-A")
    _rf_git(work, "commit", "-q", "-m", "start")
    keep(app, work, command(tools))
    real_start = PN.record_turn_start_async

    async def patient(*a, **k):
        k["bound"] = WAIT_S
        return await real_start(*a, **k)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)
    async with app.router.lifespan_context(app):
        svc = S.service_for(app.state.platform)
        drivers = fake_tool(svc, monkeypatch)
        row = store.create(app.state.platform.engine, harness="codex", folder=str(work), model="gpt-x")
        await svc.start_turn(row.id, "add a rate limit", "t1")
        await until(lambda: row.id in drivers and drivers[row.id].sent, timeout=WAIT_S)
        await until(lambda: any(e.get("how") == "start" and e.get("turn") == "t1"
                                for e in PN.read_trees(svc.home, row.id)), timeout=WAIT_S)
        (work / "limits.py").write_text("LIMIT = 4\n", encoding="utf-8")
        svc._emit(svc.live[row.id], make("tool", item="c1", kind="command", name="Bash", title="Make it",
                                         input="python make.py", status="done"))
        reply(svc, row.id)
        await settle(svc, row.id)
        [rec] = proofs(svc, row.id)
        assert rec["tests"] == "passed" and rec["for_turn"] == "t1"
        await until(lambda: rows_of(app, row.id), timeout=WAIT_S)
        with session_scope(app.state.platform.engine) as db:
            (got,) = db.exec(select(SB.CodingOutcome).where(SB.CodingOutcome.session_id == row.id)).all()
            assert (got.ref, got.tool, got.model, got.checked, got.verdict) == (f"{row.id}:t1", "codex", "gpt-x",
                                                                               SB.CHECK_PASSED, "")
            assert got.folder_key == SB.folder_key(str(work))
        # The same check told again: still one piece.
        await PROOF._tell(row.id, "t1", rec, str(work))
        assert list(rows_of(app, row.id)) == [f"{row.id}:t1"]
    await stop_all(app)


async def test_a_tool_switched_while_the_check_ran_takes_no_credit_for_the_reply(app, tmp_path, monkeypatch):
    """Codex wrote the reply (only through a command, so its end noted
    nothing); while Iron Jarvis's check ran, the session moved to Claude Code
    and another model (what a switch writes on the row). The piece the check
    makes is Codex's, with the model the message was sent to."""
    work = tmp_path / "work"
    work.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "check.py").write_text(CHECK_SCRIPT, encoding="utf-8")
    (work / "test_limits.py").write_text(PASSING, encoding="utf-8")
    _rf_git(work, "init", "-q", "-b", "main")
    _rf_git(work, "add", "-A")
    _rf_git(work, "commit", "-q", "-m", "start")
    keep(app, work, command(tools))
    real_start = PN.record_turn_start_async

    async def patient(*a, **k):
        k["bound"] = WAIT_S
        return await real_start(*a, **k)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)
    real_tell = PROOF._tell
    told: list[str] = []

    async def switched_first(sid, turn_id, record, folder):
        # The reply has ended and its tests ran: the switch lands now.
        store.update(app.state.platform.engine, sid, harness="claude-code", model="claude-opus-x")
        told.append(turn_id)
        await real_tell(sid, turn_id, record, folder)

    monkeypatch.setattr(PROOF, "_tell", switched_first)
    async with app.router.lifespan_context(app):
        svc = S.service_for(app.state.platform)
        drivers = fake_tool(svc, monkeypatch)
        row = store.create(app.state.platform.engine, harness="codex", folder=str(work), model="gpt-x")
        await svc.start_turn(row.id, "add a rate limit", "t1")
        await until(lambda: row.id in drivers and drivers[row.id].sent, timeout=WAIT_S)
        await until(lambda: any(e.get("how") == "start" and e.get("turn") == "t1"
                                for e in PN.read_trees(svc.home, row.id)), timeout=WAIT_S)
        (work / "limits.py").write_text("LIMIT = 4\n", encoding="utf-8")
        svc._emit(svc.live[row.id], make("tool", item="c1", kind="command", name="Bash", title="Make it",
                                         input="python make.py", status="done"))
        reply(svc, row.id)
        await settle(svc, row.id)
        await until(lambda: rows_of(app, row.id), timeout=WAIT_S)
        assert told == ["t1"]
        assert store.get(app.state.platform.engine, row.id).harness == "claude-code"
        with session_scope(app.state.platform.engine) as db:
            (got,) = db.exec(select(SB.CodingOutcome).where(SB.CodingOutcome.session_id == row.id)).all()
            assert (got.tool, got.model, got.checked) == ("codex", "gpt-x", SB.CHECK_PASSED)
    await stop_all(app)


def test_a_check_of_a_message_this_process_did_not_send_only_fills_a_row_that_exists(app):
    """A reply rejoined after a restart: its writer is not known here, so its
    check makes no piece of its own (never credited to whatever the session
    has now); a row its own file steps wrote takes the check's verdict."""
    engine = app.state.platform.engine
    row = store.create(engine, harness="codex", folder=str(app.state.platform.config.home), model="gpt-x")
    assert SB.writer_of(row.id, "t7") is None
    assert SB.on_proof(engine, row.id, "t7", {"tests": "passed"}) is False
    assert rows_of(app, row.id) == {}
    assert SB.note_hosted_reply(engine, row.id, "t8") is True
    assert SB.on_proof(engine, row.id, "t8", {"tests": "failed"}) is False
    with session_scope(engine) as db:
        (got,) = db.exec(select(SB.CodingOutcome).where(SB.CodingOutcome.session_id == row.id)).all()
        assert (got.ref, got.checked) == (f"{row.id}:t8", SB.CHECK_FAILED)


# --------------------------------------------------------------------------- #
# Build › Code presses
# --------------------------------------------------------------------------- #


async def test_build_code_open_a_pull_request_keeps_the_branch_sessions_work_and_later_presses_count_nothing(
        app, repo, tmp_path, monkeypatch):
    github_remote(tmp_path, repo)
    install_fake_gh(tmp_path, monkeypatch)
    async with client(app) as c:
        sid, wt = await branch_session(c, app, repo)
        committed_by_hand(wt, "api.py", "LIMIT = 4\n")
        r = await c.post(f"/build/app/sessions/{sid}/git/pr", json={"title": "Add api", "body": ""})
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_PULL_REQUEST)}
        r = await c.post(f"/build/app/sessions/{sid}/git/pr", json={"title": "Add api", "body": ""})
        assert r.status_code == 200, r.text
        r = await c.post(f"/build/app/sessions/{sid}/git/merge")
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_PULL_REQUEST)}
    await stop_all(app)


async def test_build_code_a_pull_request_from_the_folder_directly_keeps_the_sessions_work(
        app, repo, tmp_path, monkeypatch):
    github_remote(tmp_path, repo)
    install_fake_gh(tmp_path, monkeypatch)
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    async with client(app) as c:
        sid = (await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(repo)})).json()["id"]
        assert SB.note_hosted_reply(app.state.platform.engine, sid, "t1") is True
        git("switch", "-q", "-c", "feature", cwd=repo)
        committed_by_hand(repo, "app.py", "x = 5\n")
        r = await c.post(f"/build/app/sessions/{sid}/git/pr", json={"title": "Make x five", "body": ""})
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_PULL_REQUEST)}
    await stop_all(app)


async def test_build_code_merge_keeps_the_work_and_a_second_merge_counts_nothing(app, repo):
    async with client(app) as c:
        sid, wt = await branch_session(c, app, repo)
        committed_by_hand(wt, "api.py", "LIMIT = 4\n")
        r = await c.post(f"/build/app/sessions/{sid}/git/merge")
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_MERGED)}
        r = await c.post(f"/build/app/sessions/{sid}/git/merge")
        assert r.status_code >= 400
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_MERGED)}
    await stop_all(app)


async def test_build_code_discard_throws_the_work_away_and_a_second_press_counts_nothing(app, repo):
    async with client(app) as c:
        sid, wt = await branch_session(c, app, repo)
        (wt / "api.py").write_text("LIMIT = 4\n", encoding="utf-8")
        r = await c.post(f"/build/app/sessions/{sid}/git/discard", json={"confirm": True})
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.THROWN, SB.HOW_DISCARDED)}
        r = await c.post(f"/build/app/sessions/{sid}/git/discard", json={"confirm": True})
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.THROWN, SB.HOW_DISCARDED)}
    await stop_all(app)


async def test_build_code_deleting_a_branch_session_throws_its_work_and_a_folder_session_leaves_it(app, repo):
    async with client(app) as c:
        sid, wt = await branch_session(c, app, repo)
        r = await c.delete(f"/build/app/sessions/{sid}")
        assert r.status_code == 200, r.text
        assert not wt.exists() and store.get(app.state.platform.engine, sid) is None
        # On record after the session is gone, thrown away (the Discard path).
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.THROWN, SB.HOW_DISCARDED)}
        # A session in the folder directly: its work is still in the folder.
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo)})
        here = r.json()["id"]
        assert SB.note_hosted_reply(app.state.platform.engine, here, "t1") is True
        r = await c.delete(f"/build/app/sessions/{here}")
        assert r.status_code == 200, r.text
        assert rows_of(app, here) == {f"{here}:t1": ("", "")}
    await stop_all(app)


# --------------------------------------------------------------------------- #
# Chat › Code presses
# --------------------------------------------------------------------------- #


def chat_piece(app, sid: str, repo: Path) -> None:
    assert SB.note(app.state.platform.engine, session_id=sid, ref=f"{sid}:r1", folder=str(repo),
                   surface=SB.SURFACE_CODE, tool=SB.TOOL_JARVIS, provider="anthropic", model="claude-x")


async def test_chat_code_open_a_pull_request_keeps_the_sessions_work_once(app, repo, tmp_path, monkeypatch):
    github_remote(tmp_path, repo)
    install_fake_gh(tmp_path, monkeypatch)
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    chat_piece(app, sid, repo)
    committed_by_hand(wt, "api.py", "LIMIT = 4\n")
    status, pr = await asgi(app, "POST", f"/build/sessions/{sid}/git/pr", {"title": "Add api", "body": ""})
    assert status == 200, pr
    assert rows_of(app, sid) == {f"{sid}:r1": (SB.KEPT, SB.HOW_PULL_REQUEST)}
    status, again = await asgi(app, "POST", f"/build/sessions/{sid}/git/pr", {"title": "Add api", "body": ""})
    assert status == 200, again
    assert rows_of(app, sid) == {f"{sid}:r1": (SB.KEPT, SB.HOW_PULL_REQUEST)}


async def test_chat_code_deleting_a_branch_session_throws_its_work_and_a_folder_session_leaves_it(
        app, repo, tmp_path):
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    chat_piece(app, sid, repo)
    status, out = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 200, out
    assert not wt.exists()
    assert rows_of(app, sid) == {f"{sid}:r1": (SB.THROWN, SB.HOW_DISCARDED)}
    # A session in a plain folder (no branch of its own): not decided.
    plain = tmp_path / "plain"
    plain.mkdir()
    status, made = await asgi(app, "POST", "/build/sessions", {"folder": str(plain)})
    assert status == 201, made
    chat_piece(app, made["id"], plain)
    status, out = await asgi(app, "DELETE", f"/build/sessions/{made['id']}")
    assert status == 200, out
    assert rows_of(app, made["id"]) == {f"{made['id']}:r1": ("", "")}


async def menu(app, folder: Path) -> dict:
    status, rec = await asgi(app, "GET", f"/build/scoreboard?folder={folder}")
    assert status == 200, rec
    (line,) = rec["rows"]
    return line


async def test_chat_code_in_the_folder_directly_the_users_own_commit_keeps_the_reply(app, repo):
    """The default session works in the folder (no branch, so no Commit of
    Iron Jarvis's). A real reply edits app.py; the menu's read keeps it only
    once the user's own commit holds it and nothing of app.py is left
    uncommitted."""
    view = await new_branch_session(app, repo, approval_mode="yolo", branch=False)
    sid = view["id"]
    assert not view.get("git")
    wire(app, Script([edit_move(1), "edit 1 done"], who=CLAUDE))
    await turn(app, sid, "change 1", tid(sid, 1))
    assert (repo / "app.py").read_bytes() == b"x = 2\n"
    assert rows_of(app, sid) == {ref(sid, 1): ("", "")}
    # Nothing committed yet: not decided.
    line = await menu(app, repo)
    assert (line["kept"], line["undecided"]) == (0, 1) and line["words"].endswith("1 not decided yet")
    # Committed, then changed again by hand: still something of it uncommitted.
    git("add", "-A", cwd=repo)
    git(*HAND, "commit", "-q", "-m", "keep the change", cwd=repo)
    with open(repo / "app.py", "ab") as fh:
        fh.write(b"y = 0\n")
    assert rows_of(app, sid) == {ref(sid, 1): ("", "")}
    assert (await menu(app, repo))["undecided"] == 1
    assert rows_of(app, sid) == {ref(sid, 1): ("", "")}
    # The user commits the rest: kept, once.
    git("add", "-A", cwd=repo)
    git(*HAND, "commit", "-q", "-m", "and the rest", cwd=repo)
    line = await menu(app, repo)
    assert (line["kept"], line["thrown"], line["undecided"]) == (1, 0, 0)
    assert line["words"].startswith("In this folder: kept 1 of 1 (too few to judge)")
    assert rows_of(app, sid) == {ref(sid, 1): (SB.KEPT, SB.HOW_COMMITTED)}
    assert SB.keep_own_commits(app.state.platform.engine, str(repo)) == 0


def commit_at(folder: Path, when: int, message: str) -> None:
    """The user's own commit, dated ``when`` (seconds since 1970, UTC)."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
           "GIT_AUTHOR_DATE": f"{when} +0000", "GIT_COMMITTER_DATE": f"{when} +0000"}
    for args in (["add", "-A"], [*HAND, "commit", "-q", "-m", message]):
        r = subprocess.run(["git", *args], cwd=str(folder), capture_output=True, env=env)
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


def ended_at(app, ref_: str, when: int) -> None:
    with session_scope(app.state.platform.engine) as db:
        (row,) = db.exec(select(SB.CodingOutcome).where(SB.CodingOutcome.ref == ref_)).all()
        row.created_at = datetime.fromtimestamp(when, tz=timezone.utc).replace(tzinfo=None)
        db.add(row)
        db.commit()


async def folder_session(app, repo: Path, replies: "list[tuple[str, str, int]]") -> str:
    """A Chat › Code session in the folder directly whose replies (turn, the
    file its line names or "", when it ended) are on record."""
    status, made = await asgi(app, "POST", "/build/sessions", {"folder": str(repo), "branch": False})
    assert status == 201, made
    sid = made["id"]
    engine = app.state.platform.engine
    lines = []
    for t, name, _when in replies:
        lines.append({"role": "user", "content": f"make {t}", "turnId": t})
        lines.append({"role": "assistant", "turnId": t, "content": "Done.",
                      "documents": [str(repo / name)] if name else []})
        assert SB.note(engine, session_id=sid, ref=f"{sid}:{t}", folder=str(repo), surface=SB.SURFACE_CODE,
                       tool=SB.TOOL_JARVIS, provider="anthropic", model="claude-x")
    assert HS.append_lines(engine, sid, lines)["ok"]
    for t, _name, when in replies:
        ended_at(app, f"{sid}:{t}", when)
    return sid


async def test_the_users_own_commit_keeps_only_what_it_holds_after_the_reply_and_never_the_models_own(
        app, repo, monkeypatch):
    """Two folder sessions in one folder. Session A: t1's file was committed
    after t1 ended (kept); t2's in the SAME commit, before t2 ended (not
    decided); t4 names no file, a command changed it (not decided). Session
    B: t3's file was committed after it, but B's coding model ran a ``git
    commit`` after t3 (that commit may be the model's: not decided). While a
    reply runs, nothing is decided."""
    base = int(time.time()) - 3600
    b = await folder_session(app, repo, [("t3", "c.py", base)])
    a = await folder_session(app, repo, [("t1", "a.py", base + 20), ("t2", "b.py", base + 40),
                                         ("t4", "", base + 60)])
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(ToolInvocation(
            session_id=HS.SESSION_PREFIX + b, agent_run_id="run", tool="shell",
            args_json=json.dumps({"command": "git commit -am 'c'"}), reversibility="irreversible",
            created_at=datetime.fromtimestamp(base + 5, tz=timezone.utc).replace(tzinfo=None)))
        db.commit()
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text(f"{name[0]} = 1\n", encoding="utf-8")
    commit_at(repo, base + 30, "a, b and c")  # after t1 and t3, before t2
    (repo / "d.py").write_text("d = 1\n", encoding="utf-8")
    commit_at(repo, base + 70, "d")  # after t4
    from iron_jarvis.core.turns import TURNS

    with monkeypatch.context() as m:
        m.setattr(TURNS, "thread_states", lambda: {a: {"running": True, "waiting": False}})
        assert SB.keep_own_commits(engine, str(repo)) == 0
    assert SB.keep_own_commits(engine, str(repo)) == 1
    assert rows_of(app, a) == {f"{a}:t1": (SB.KEPT, SB.HOW_COMMITTED), f"{a}:t2": ("", ""), f"{a}:t4": ("", "")}
    assert rows_of(app, b) == {f"{b}:t3": ("", "")}


async def test_chats_coding_record_reads_the_users_own_commit_like_the_menu(app, repo):
    """"Which model does best in this folder?" answers from the same record,
    so the user's own commit keeps the folder session's reply there too."""
    from iron_jarvis.settings import records_scoreboard as RS

    base = int(time.time()) - 3600
    sid = await folder_session(app, repo, [("t1", "a.py", base)])
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    commit_at(repo, base + 10, "a")
    got = RS.report(app.state.platform, None, str(repo), "", phone=False, withhold=False)
    assert "kept 1 of 1 (too few to judge)" in got.output, got.output
    assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_COMMITTED)}


async def test_a_rewind_in_a_folder_that_is_not_a_repository_decides_nothing(app, tmp_path):
    """There nothing can ever be kept (no Commit, merge or branch), so a real
    Chat › Code rewind leaves the rewound reply not decided: the record never
    shows a figure that could only count throw-aways."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.py").write_bytes(b"x = 1\n")
    assert not SB.has_git(plain)
    view = await new_branch_session(app, plain, approval_mode="yolo")
    sid = view["id"]
    wire(app, Script([edit_move(1), "edit 1 done"], who=CLAUDE))
    await turn(app, sid, "change 1", tid(sid, 1))
    assert rows_of(app, sid) == {ref(sid, 1): ("", "")}
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": tid(sid, 1)})
    assert status == 200, out
    assert (plain / "app.py").read_bytes() == b"x = 1\n"
    assert rows_of(app, sid) == {ref(sid, 1): ("", "")}


# --------------------------------------------------------------------------- #
# Build › Code: the files-only rewind, and Undo rewind
# --------------------------------------------------------------------------- #


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    _rf_git(folder, "init", "-q", "-b", "main")
    _rf_git(folder, "config", "core.autocrlf", "false")
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "keep.py").write_bytes(b"keep = 0\n")
    _rf_git(folder, "add", "-A")
    _rf_git(folder, "commit", "-q", "-m", "first")
    return folder


async def test_the_files_only_rewind_throws_the_work_away_and_undo_rewind_puts_it_back_to_be_kept(app, work):
    sid, ats, _at_second = await _plain_session(app, work, "grok")
    engine = app.state.platform.engine
    for t in ("t1", "t2"):
        assert SB.note_hosted_reply(engine, sid, t) is True
    async with client(app) as c:
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": ats[1]})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": ats[1], "expect": plan["id"]})
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": ("", ""), f"{sid}:t2": (SB.THROWN, SB.HOW_REWOUND)}
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": ("", ""), f"{sid}:t2": ("", "")}
        # Nothing left to undo: a second press changes nothing.
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 409
        assert rows_of(app, sid) == {f"{sid}:t1": ("", ""), f"{sid}:t2": ("", "")}
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "keep both", "paths": ["a.py"]})
        assert r.status_code == 200, r.text
        assert rows_of(app, sid) == {f"{sid}:t1": (SB.KEPT, SB.HOW_COMMITTED), f"{sid}:t2": (SB.KEPT, SB.HOW_COMMITTED)}
    await stop_all(app)


def test_an_undo_takes_back_only_its_own_rewind(tmp_path):
    """Two rewinds threw two pieces away; the undo of the newest puts back
    only that one (an older rewind's piece, or a piece decided another way,
    is never touched)."""
    from sqlmodel import create_engine

    from iron_jarvis.core.db import init_db

    engine = create_engine(f"sqlite:///{(tmp_path / 'ij.db').as_posix()}")
    init_db(engine)
    shop = tmp_path / "shop"
    (shop / ".git").mkdir(parents=True)  # a repository: there work can be kept
    for t in ("t1", "t2", "t3"):
        assert SB.note(engine, session_id="s", ref=f"s:{t}", folder=str(shop), surface=SB.SURFACE_TOOL,
                       tool="codex")
    assert SB.rewound(engine, "s", ["t3"], undo_key="2026-11-26T10:00:00Z") == 1
    assert SB.rewound(engine, "s", ["t2"], undo_key="2026-11-26T11:00:00Z") == 1
    assert SB.settle_session(engine, "s", SB.KEPT, SB.HOW_COMMITTED) == 1
    assert SB.unrewound(engine, "s", "2026-11-26T11:00:00Z") == 1
    assert SB.unrewound(engine, "s", "") == 0
    with session_scope(engine) as db:
        got = {r.ref: (r.verdict, r.how) for r in db.exec(select(SB.CodingOutcome)).all()}
    assert got == {"s:t1": (SB.KEPT, SB.HOW_COMMITTED), "s:t2": ("", ""), "s:t3": (SB.THROWN, SB.HOW_REWOUND)}


# --------------------------------------------------------------------------- #
# Only the session's own folder
# --------------------------------------------------------------------------- #


def test_a_write_outside_the_sessions_folder_is_no_piece(app, tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    engine = app.state.platform.engine
    row = store.create(engine, harness="codex", folder=str(folder))
    elsewhere = tmp_path / "scratch" / "plan.md"
    home_file = Path.home() / ".codex" / "memory.md"
    near = tmp_path / "shop-old" / "a.py"  # shares the folder's name as a prefix
    outside = [{"type": "tool", "kind": "write", "status": "done", "path": str(p)} for p in (elsewhere, home_file, near)]
    assert SB.hosted_changed(outside, str(folder)) is False
    assert SB.note_hosted_turn(engine, row.id, "t1", outside) is False
    inside = outside + [{"type": "tool", "kind": "edit", "status": "done", "path": str(folder / "api" / "a.py")}]
    assert SB.hosted_changed(inside, str(folder)) is True
    relative = [{"type": "tool", "kind": "write", "status": "done", "path": "api/b.py"}]
    assert SB.hosted_changed(relative, str(folder)) is True
    assert SB.note_hosted_turn(engine, row.id, "t2", inside) is True
    with session_scope(engine) as db:
        assert [r.ref for r in db.exec(select(SB.CodingOutcome)).all()] == [f"{row.id}:t2"]
