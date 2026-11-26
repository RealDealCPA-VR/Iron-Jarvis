"""Next leaps 28-a (R2.17): every seam that already writes a fact writes ONE
row of the folder's coding record (``harness/scoreboard``), through the REAL
routes, and a second press never counts a piece twice.

Real git in a scratch repository, the REAL app factory and routes, real Chat ›
Code turns (only the model is a scripted double, ``tests/test_build_sessions_
v1367.Script``), the in-process stand-in coding tool for Build › Code
(``tests/test_build_app_git_v1``) and the fake Claude Code for its rewind
(``tests/test_build_app_rewind_files_v1543``). Never the real GitHub, a real
coding tool, a model or the running app.

Pinned:
* Chat › Code: a reply that changed files is one row (tool jarvis, the model
  that served it, its folder's key = the folder the user PICKED, not the
  branch copy); Commit, Merge and Discard settle it (kept / kept / thrown);
  a second press changes nothing; a rewind throws the rewound replies away;
* Keep on several attempts: the kept one is kept, the other thrown; a second
  Keep counts nothing; Discard all throws them;
* Build › Code: a reply whose own file steps changed files is one row (its
  tool, the session's model); Commit settles it; a finished check gives its
  ``checked``; a rewind WITH the files throws the dropped message's row
  away (Undo rewind puts it back), one without the files leaves it
  undecided;
* the folder's line through ``GET /build/scoreboard`` (the done-when: three
  kept and one discarded session read "kept 3 of 4 (too few to judge)").
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import scoreboard as SB
from iron_jarvis.harness import sessions as HS
from iron_jarvis.hosted import service as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests._build_app_helpers_v1409 import client, stop_all
from tests.test_build_app_git_v1 import message, patient_start, reply, wrote
from tests.test_build_app_history_v1418 import fake_tool
from tests.test_build_app_rewind_files_v1543 import _ats, _claude_two_messages
from tests.test_build_app_rewind_files_v1543 import _git as _rf_git
from tests.test_build_app_branch_v14410 import _claude_transcript
from tests.test_build_git_v1375 import git, make_repo, new_branch_session, no_real_gh, turn
from tests.test_build_sessions_v1367 import Script, asgi, wire

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

CLAUDE = ("anthropic", "claude-sonnet-4-5")


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


@pytest.fixture()
def work(tmp_path) -> Path:
    """The rewind tests' folder (``test_build_app_rewind_files_v1543.work``)."""
    folder = tmp_path / "work"
    folder.mkdir()
    _rf_git(folder, "init", "-q", "-b", "main")
    _rf_git(folder, "config", "core.autocrlf", "false")
    (folder / "a.py").write_bytes(b"a = 0\n")
    (folder / "keep.py").write_bytes(b"keep = 0\n")
    _rf_git(folder, "add", "-A")
    _rf_git(folder, "commit", "-q", "-m", "first")
    return folder


@pytest.fixture()
def claude_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


@pytest.fixture(autouse=True)
def _a_checkpoint_for_every_message(monkeypatch):
    """Each message waits for its checkpoint (the rewind tests' own rule):
    a loaded PC must not leave a message with none."""
    from iron_jarvis.hosted import panes as PN

    real = PN.record_turn_start_async

    async def patient(home, sid, folder, turn_id, *, bound=120.0, uid=""):
        return await real(home, sid, folder, turn_id, bound=120.0, uid=uid)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)


def rows_of(app, session_id: str = "") -> list[SB.CodingOutcome]:
    with session_scope(app.state.platform.engine) as db:
        q = select(SB.CodingOutcome)
        if session_id:
            q = q.where(SB.CodingOutcome.session_id == session_id)
        got = list(db.exec(q).all())
        for r in got:
            db.expunge(r)
    return got


def edit_move(n: int) -> list:
    return [ToolCall(id=f"e{n}", name="Edit",
                     arguments={"file_path": "app.py", "old_string": f"x = {n}", "new_string": f"x = {n + 1}"})]


def tid(sid: str, n: int) -> str:
    """A turn id of its own per session (a turn id names ONE conversation)."""
    return f"r{n}-{sid[-8:]}"


def ref(sid: str, n: int) -> str:
    return f"{sid}:{tid(sid, n)}"


async def edited_session(app, repo, turns: int = 1) -> tuple[str, Path]:
    """A Chat › Code session on its own branch whose ``turns`` replies each
    changed app.py (the scripted model's Edit, through the real tools). A
    script per turn: whatever else the turn asks the model (its finish
    check's note) is answered "Done." and never takes the next turn's edit."""
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    for n in range(1, turns + 1):
        wire(app, Script([edit_move(n), f"edit {n} done"], who=CLAUDE))
        await turn(app, sid, f"change {n}", tid(sid, n))
    return sid, wt


# --------------------------------------------------------------------------- #
# Chat › Code
# --------------------------------------------------------------------------- #


async def test_a_chat_code_reply_that_changed_files_is_one_row_and_commit_keeps_it_once(app, repo):
    sid, wt = await edited_session(app, repo)
    (row,) = rows_of(app, sid)
    assert row.ref == ref(sid, 1) and row.unit == SB.UNIT_REPLY and row.surface == SB.SURFACE_CODE
    assert row.tool == SB.TOOL_JARVIS and (row.provider, row.model) == CLAUDE
    # Keyed on the folder the user PICKED, never the branch's own copy.
    assert row.folder_key == SB.folder_key(str(repo)) != SB.folder_key(str(wt))
    assert row.verdict == "" and row.source == SB.SOURCE_WORK
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "one"})
    assert status == 200, done
    (row,) = rows_of(app, sid)
    assert (row.verdict, row.how) == (SB.KEPT, SB.HOW_COMMITTED) and row.decided_at is not None
    # A second press (nothing left to commit) counts nothing twice.
    status, _again = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "two"})
    assert len(rows_of(app, sid)) == 1 and rows_of(app, sid)[0].how == SB.HOW_COMMITTED
    # The line in the menu.
    status, rec = await asgi(app, "GET", f"/build/scoreboard?folder={repo}")
    assert status == 200
    (line,) = rec["rows"]
    assert (line["tool"], line["provider"], line["model"]) == (SB.TOOL_JARVIS, *CLAUDE)
    assert line["words"] == "In this folder: kept 1 of 1 (too few to judge)" or \
        line["words"].startswith("In this folder: kept 1 of 1 (too few to judge), passed its checks")


async def test_merge_keeps_the_sessions_replies_and_a_second_merge_counts_nothing(app, repo):
    sid, wt = await edited_session(app, repo)
    git("add", "-A", cwd=wt)
    git("-c", "user.email=dev@example.com", "-c", "user.name=Dev", "commit", "-q", "-m", "by hand", cwd=wt)
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 200, done
    (row,) = rows_of(app, sid)
    assert (row.verdict, row.how) == (SB.KEPT, SB.HOW_MERGED)
    status, _again = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 409
    assert [(r.verdict, r.how) for r in rows_of(app, sid)] == [(SB.KEPT, SB.HOW_MERGED)]


async def test_discard_throws_the_sessions_undecided_work_away(app, repo):
    sid, _wt = await edited_session(app, repo)
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/discard", {"confirm": True})
    assert status == 200, done
    (row,) = rows_of(app, sid)
    assert (row.verdict, row.how) == (SB.THROWN, SB.HOW_DISCARDED)


async def test_a_rewind_throws_the_rewound_replies_away_and_only_those(app, repo):
    sid, _wt = await edited_session(app, repo, turns=2)
    assert {r.ref for r in rows_of(app, sid)} == {ref(sid, 1), ref(sid, 2)}
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": tid(sid, 2)})
    assert status == 200, out
    got = {r.ref: (r.verdict, r.how) for r in rows_of(app, sid)}
    assert got == {ref(sid, 1): ("", ""), ref(sid, 2): (SB.THROWN, SB.HOW_REWOUND)}
    # Commit settles only what is still undecided: the rewound one stays thrown.
    status, _c = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "keep one"})
    assert status == 200
    got = {r.ref: (r.verdict, r.how) for r in rows_of(app, sid)}
    assert got == {ref(sid, 1): (SB.KEPT, SB.HOW_COMMITTED), ref(sid, 2): (SB.THROWN, SB.HOW_REWOUND)}


async def test_three_kept_and_one_discarded_session_read_too_few_to_judge(app, repo):
    """The phase's done-when, through the real routes."""
    for n in range(3):
        sid, _wt = await edited_session(app, repo)
        status, _c = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": f"keep {n}"})
        assert status == 200
    sid, _wt = await edited_session(app, repo)
    status, _d = await asgi(app, "POST", f"/build/sessions/{sid}/git/discard", {"confirm": True})
    assert status == 200
    status, rec = await asgi(app, "GET", f"/build/scoreboard?folder={repo}")
    (line,) = rec["rows"]
    assert (line["kept"], line["thrown"], line["undecided"], line["samples"]) == (3, 1, 0, 4)
    assert line["words"].startswith("In this folder: kept 3 of 4 (too few to judge)")


# --------------------------------------------------------------------------- #
# Keep on several attempts (a card made of real branches, the real routes)
# --------------------------------------------------------------------------- #


async def attempts_card(app, repo) -> tuple[str, list[dict]]:
    """A session on its own branch whose newest reply line holds an attempts
    card of two real attempt branches, each of which changed app.py."""
    view = await new_branch_session(app, repo)
    sid, facts = view["id"], view["git"]
    items = []
    for aid, model, body in (("try1", "model-a", b"x = 10\n"), ("try2", "model-b", b"x = 20\n")):
        made = B.start(str(repo), title=f"try {aid}", start_point=git("rev-parse", "HEAD", cwd=Path(facts["worktree"])),
                       base=facts["branch"])
        (Path(made["worktree"]) / "app.py").write_bytes(body)
        items.append({"id": aid, "label": model, "provider": "openai", "model": model, "state": "done",
                      "why": "finished", "files": ["app.py"], "git": made, "branch": made["branch"],
                      "finish": {"tests": "passed" if aid == "try1" else "failed", "command": "", "removed": 0,
                                 "kept": 0, "points": 0, "unmet": 0, "after": ""}})
    card = {"items": items, "task": "fix it", "base": facts["branch"], "adopt": False}
    line = {"role": "assistant", "turnId": "att-1", "content": "Tried on two models.", A.LINE_KEY: card}
    assert HS.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": "fix it"}, line])["ok"]
    return sid, items


async def test_keep_keeps_one_attempt_throws_the_other_and_a_second_keep_counts_nothing(app, repo):
    sid, _items = await attempts_card(app, repo)
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "att-1", "attempt": "try1"})
    assert status == 200, kept
    got = {r.ref: (r.unit, r.model, r.checked, r.verdict, r.how) for r in rows_of(app, sid)}
    assert got == {
        f"{sid}:att-1:try1": (SB.UNIT_ATTEMPT, "model-a", "passed", SB.KEPT, SB.HOW_ATTEMPT_KEPT),
        f"{sid}:att-1:try2": (SB.UNIT_ATTEMPT, "model-b", "failed", SB.THROWN, SB.HOW_ATTEMPT_DROPPED),
    }
    status, again = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                               {"turn_id": "att-1", "attempt": "try2"})
    assert status == 409
    assert {r.ref: r.verdict for r in rows_of(app, sid)} == {
        f"{sid}:att-1:try1": SB.KEPT, f"{sid}:att-1:try2": SB.THROWN}
    # A later Commit of the session never re-decides an attempt.
    await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "x"})
    assert {r.ref: r.verdict for r in rows_of(app, sid)} == {
        f"{sid}:att-1:try1": SB.KEPT, f"{sid}:att-1:try2": SB.THROWN}


async def test_discard_all_throws_every_attempt_and_a_second_press_counts_nothing(app, repo):
    sid, _items = await attempts_card(app, repo)
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/discard", {"turn_id": "att-1"})
    assert status == 200, done
    assert {r.ref: (r.verdict, r.how) for r in rows_of(app, sid)} == {
        f"{sid}:att-1:try1": (SB.THROWN, SB.HOW_ATTEMPT_DROPPED),
        f"{sid}:att-1:try2": (SB.THROWN, SB.HOW_ATTEMPT_DROPPED),
    }
    status, _again = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/discard", {"turn_id": "att-1"})
    assert status == 409 and len(rows_of(app, sid)) == 2


# --------------------------------------------------------------------------- #
# Build › Code
# --------------------------------------------------------------------------- #


async def test_a_build_code_reply_that_changed_files_is_one_row_and_commit_keeps_it(app, repo, monkeypatch):
    await patient_start(monkeypatch)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    async with client(app) as c:
        made = (await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo)})).json()
        sid = made["id"]
        await message(svc, sid, drivers)
        wrote(svc, sid, repo, "api/limits.py", "LIMIT = 4\n")
        await reply(svc, sid)
        await _settled(lambda: rows_of(app, sid))
        (row,) = rows_of(app, sid)
        assert row.ref == f"{sid}:t1" and row.surface == SB.SURFACE_TOOL and row.tool == "codex"
        assert row.folder_key == SB.folder_key(str(repo)) and row.verdict == ""
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "limits", "paths": ["api/limits.py"]})
        assert r.status_code == 200, r.text
        (row,) = rows_of(app, sid)
        assert (row.verdict, row.how) == (SB.KEPT, SB.HOW_COMMITTED)
        # A finished check of that reply gives its verdict (the listener).
        assert SB.on_proof(app.state.platform.engine, sid, "t1", {"tests": "passed"}) is False
        assert rows_of(app, sid)[0].checked == SB.CHECK_PASSED
        # A reply with no file step of its own is not a piece.
        await message(svc, sid, drivers, text="what does it do?", turn="t2")
        await reply(svc, sid, text="It limits requests.")
        assert [r.ref for r in rows_of(app, sid)] == [f"{sid}:t1"]
    await stop_all(app)


async def _settled(read) -> None:
    from tests.test_build_app_history_v1418 import until

    await until(lambda: bool(read()), timeout=60.0)


async def test_a_build_code_rewind_with_its_files_throws_the_dropped_message_away(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids, _at_second = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        engine = app.state.platform.engine
        # The two replies' pieces (the stand-in tool's work was made by the
        # test, so it is noted here as the reply's end would note it).
        for t in ("t1", "t2"):
            assert SB.note_hosted_reply(engine, sid, t) is True
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        # Without the files: the work is still in the folder, so not decided.
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2})
        assert r.status_code == 200, r.text
        assert {x.ref: x.verdict for x in rows_of(app, sid)} == {f"{sid}:t1": "", f"{sid}:t2": ""}
    await stop_all(app)


async def test_a_build_code_rewind_that_puts_the_files_back_marks_it_rewound(
        app, work, claude_home, tmp_path, monkeypatch):
    async with client(app) as c:
        sid, hid, uuids, _at_second = await _claude_two_messages(app, c, work, monkeypatch, tmp_path)
        _claude_transcript(claude_home, hid, uuids)
        engine = app.state.platform.engine
        for t in ("t1", "t2"):
            SB.note_hosted_reply(engine, sid, t)
        at2 = _ats((await c.get(f"/build/app/sessions/{sid}")).json())[1]
        plan = (await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at2})).json()
        r = await c.post(f"/build/app/sessions/{sid}/rewind", json={"to": at2, "files": True, "expect": plan["id"]})
        assert r.status_code == 200, r.text
        got = {x.ref: (x.verdict, x.how) for x in rows_of(app, sid)}
        assert got == {f"{sid}:t1": ("", ""), f"{sid}:t2": (SB.THROWN, SB.HOW_REWOUND)}
        # Undo rewind puts the files back: that piece is not decided again.
        r = await c.post(f"/build/app/sessions/{sid}/rewind/undo")
        assert r.status_code == 200, r.text
        assert {x.ref: (x.verdict, x.how) for x in rows_of(app, sid)} == {f"{sid}:t1": ("", ""), f"{sid}:t2": ("", "")}
    await stop_all(app)


async def test_the_build_code_reply_end_reads_nothing_for_a_reply_with_no_file_step(monkeypatch):
    """A reply's end with no finished file step schedules nothing (no row
    read for it); one with a finished write step is read OFF the loop."""
    import asyncio
    import threading

    from iron_jarvis.harness import stall

    called: list = []
    loop_thread = threading.current_thread()
    monkeypatch.setattr(SB, "note_hosted_turn",
                        lambda *a, **k: called.append((a[2], threading.current_thread() is loop_thread)))

    class L:
        sid = "app_x"

    def ended(turn_id: str, events: list) -> None:
        SB.after_hosted_turn(type("Svc", (), {"engine": None})(), L(),
                             type("T", (), {"id": turn_id, "events": events})(), {"stop": "completed"})

    before = set(stall._KEEP)  # noqa: SLF001
    ended("t9", [{"type": "tool", "kind": "read", "status": "done", "path": "C:/x/a.py"},
                 {"type": "tool", "kind": "edit", "status": "failed", "path": "C:/x/a.py"}])
    ended("t10", [{"type": "tool", "kind": "write", "status": "done", "path": "a.py"}])
    await asyncio.gather(*(set(stall._KEEP) - before), return_exceptions=True)  # noqa: SLF001
    assert called == [("t10", False)]
