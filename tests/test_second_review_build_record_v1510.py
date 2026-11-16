"""v1.510.0 (second north-star review, group 10 part D): a coding session's own
record never loses a reply, a plan approval or a file's old text.

* S2: a reply whose transcript write fails at its end is tried once more; if
  it still fails, the kept conversation is NOT deleted, the turn's last frame
  says so in plain words, and the next start puts the line back exactly once
  (``sessions.reconcile_unsaved_replies``, rebuilt from the turn's stored
  frames by ``routes/build.replayed_reply_line``). A helper-plans read that
  fails never skips the write. A plan approval whose session write failed is
  kept for the next try and never starts the work.
* S8: a refused delete says the session's programs were stopped; a refused
  "Choose account" + continue leaves the session on its old account and card.
* S11: accepting a suggested edit is on the session's Undo list and Undo puts
  the old text back; a put-back after a failed check is the beside-then-swap
  write.
* S12: the session's line cap drops WHOLE turns from the front.

Failures are injected (a patched store call), never a broken disk.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.harness.library_models import HarnessLibraryRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as B
from iron_jarvis.harness import library as L
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.harness import sessions as S
from tests.test_build_resume_v1391 import _reboot
from tests.test_build_sessions_v1367 import (
    Script, asgi, frames_of, messages_of, new_session, open_turn, setup_of, until, wire,
)

REPLY_TURN = "ns-1"


def _assistant(app, sid: str, turn_id: str) -> list[dict]:
    return [m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == turn_id]


def _turn_status(app, turn_id: str) -> str:
    with session_scope(app.state.platform.engine) as db:
        rec = db.get(ChatTurnRecord, turn_id)
        return str(rec.status) if rec is not None else ""


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    hdir = folder / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    # No finish check: the reply is the scripted words alone.
    (hdir / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


# --------------------------------------------------------------------------- #
# S2: the reply line
# --------------------------------------------------------------------------- #


async def test_a_reply_that_cannot_be_saved_is_kept_said_and_put_back_once(tmp_path, work, monkeypatch):
    home = str(tmp_path / "home")
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script(["Wrote it."]))
    real_append = S.append_lines
    tries: list[int] = []
    pauses: list[float] = []

    def failing_reply_write(engine, thread_id, lines, **kw):
        if any(line.get("role") == "assistant" for line in lines):
            tries.append(1)
            return {"ok": False, "updated_at": None, "count": 0}
        return real_append(engine, thread_id, lines, **kw)

    monkeypatch.setattr(S, "append_lines", failing_reply_write)
    monkeypatch.setattr(B, "_pause_before_retry", pauses.append, raising=False)
    status, raw = await open_turn(app, sid, {"text": "write it", "turn_id": REPLY_TURN})
    assert status == 200
    frames = frames_of(raw)
    event, data = frames[-1]
    assert event == "done", frames[-3:]
    # Said, on the turn's own last frame, in plain words.
    assert data.get("not_saved") == B.REPLY_NOT_SAVED, data
    assert B.REPLY_NOT_SAVED == (
        "This reply could not be saved to the session. It will be put back when Iron Jarvis next starts."
    )
    # Tried twice, with one bounded pause between (injected: no real wait).
    assert len(tries) == 2 and len(pauses) == 1 and pauses[0] == B.REPLY_SAVE_RETRY_S
    assert _assistant(app, sid, REPLY_TURN) == []
    await until(lambda: _turn_status(app, REPLY_TURN) == "done")
    # The kept conversation is NOT forgotten.
    assert RR.has_record(app.state.platform.engine, REPLY_TURN)

    # A fresh start puts the line back...
    monkeypatch.setattr(S, "append_lines", real_append)
    second = _reboot(home)
    back = _assistant(second, sid, REPLY_TURN)
    assert len(back) == 1 and back[0]["content"] == "Wrote it.", back
    assert not back[0].get("interrupted") and not back[0].get("error")
    assert isinstance(back[0].get("timing"), dict)
    # ...exactly once.
    S.reconcile_interrupted_turns(second.state.platform.engine)
    third = _reboot(home)
    assert len(_assistant(third, sid, REPLY_TURN)) == 1


def test_a_finished_turn_stopped_before_a_word_gains_no_line_at_boot(tmp_path):
    """The boot step writes only what the live writer would have written: a
    turn the user stopped before a word or a step keeps no reply line."""
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {})
    S.append_lines(engine, row.id, [{"role": "user", "content": "go", "turnId": "st-1"}])
    with session_scope(engine) as db:
        db.add(ChatTurnRecord(id="st-1", thread_id=row.id, status="stopped",
                              frames_json=json.dumps([["round", json.dumps({"round": 0, "start": 0})]])))
        db.commit()
    assert S.reconcile_unsaved_replies(engine) == 0
    assert _assistant(app, row.id, "st-1") == []


async def test_a_helper_plans_failure_still_writes_the_reply(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {})
    S.append_lines(engine, row.id, [{"role": "user", "content": "go", "turnId": "hp-1"}])
    watch = B._ReplyWatch()
    watch.see("token", json.dumps({"text": "Done with helpers."}))
    watch.see("helper", json.dumps({"id": "h1", "session": "helper-session", "state": "done"}))
    watch.see("done", json.dumps({"reply": "Done with helpers."}))
    assert watch.helpers

    def broken(engine, lines):
        raise RuntimeError("helper plan read broke")

    monkeypatch.setattr(B, "_with_helper_plans", broken)
    await B._record_turn_end(engine, row.id, "hp-1", watch, "done", None, None)
    got = _assistant(app, row.id, "hp-1")
    assert len(got) == 1 and got[0]["content"] == "Done with helpers." and got[0].get("helpers")
    assert not watch.reply_unsaved


# --------------------------------------------------------------------------- #
# S2: the plan approval
# --------------------------------------------------------------------------- #


async def test_a_plan_approval_survives_a_failed_session_write(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {"mode": "plan"})
    assert S.mode_of(S.get_row(engine, row.id)) == S.MODE_PLAN
    watch = B._ReplyWatch()
    watch.approved_plans = ["Do A, then B."]
    watch.approved_text = "Do A, then B."
    watch.plan_approved = True
    watch.done = {"reply": "Ready."}
    real_update = S.update

    def failing_update(*a, **kw):
        raise RuntimeError("the database is busy")

    monkeypatch.setattr(S, "update", failing_update)
    await B._apply_session_changes(engine, row.id, watch)
    # Kept for the next try; the session is still in plan mode.
    assert watch.approved_plans == ["Do A, then B."]
    assert S.mode_of(S.get_row(engine, row.id)) == S.MODE_PLAN
    # The work does not start as if the plan were approved, and the last frame says why.
    assert B.plan_run_due(watch) is False
    noted = frames_of(B._with_save_notes(B._durable.sse("done", {"reply": "Ready."}), watch))
    assert noted[-1][1]["not_saved"] == B.PLAN_NOT_SAVED

    # The next try writes it, and only then is it cleared.
    monkeypatch.setattr(S, "update", real_update)
    await B._apply_session_changes(engine, row.id, watch)
    assert watch.approved_plans == []
    fresh = S.get_row(engine, row.id)
    assert S.mode_of(fresh) == S.MODE_ACT
    assert setup_of(app, row.id)["plan"]["text"] == "Do A, then B."
    assert B.plan_run_due(watch) is True
    assert "not_saved" not in frames_of(B._with_save_notes(B._durable.sse("done", {"reply": "Ready."}), watch))[-1][1]


# --------------------------------------------------------------------------- #
# S8: refusals that changed something say so
# --------------------------------------------------------------------------- #


async def test_a_refused_delete_says_the_programs_were_stopped(tmp_path, work, monkeypatch):
    from iron_jarvis.harness import attempts as A
    from iron_jarvis.harness import branches as BR

    app = create_app(str(tmp_path / "home"))
    sid = new_session(app, work)["id"]
    stopped: list[str] = []
    monkeypatch.setattr(S, "end_session_programs", lambda tid: stopped.append(tid) or [])
    monkeypatch.setattr(A, "discard_for_delete", lambda platform, row: "A folder of an attempt is in use.")
    status, body = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 409 and stopped == [sid]
    assert body["detail"] == f"A folder of an attempt is in use. The session was not deleted. {B.PROGRAMS_STOPPED}"
    assert B.PROGRAMS_STOPPED == "Any programs it had running were stopped."

    def refused(platform, row):
        raise BR.GitRefused("The attempt's folder could not be moved.")

    monkeypatch.setattr(A, "discard_for_delete", refused)
    status, body = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 409
    assert body["detail"] == (
        f"The attempt's folder could not be moved. The session was not deleted. {B.PROGRAMS_STOPPED}"
    )

    monkeypatch.setattr(A, "discard_for_delete", lambda platform, row: "")
    monkeypatch.setattr(BR, "facts_of", lambda setup: {"branch": "work-branch"})

    def no_move(home, facts):
        raise BR.GitRefused("The branch's folder is open in another program.")

    monkeypatch.setattr(BR, "remove_worktree", no_move)
    status, body = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 409
    assert body["detail"] == (
        f"The branch's folder is open in another program. The session was not deleted. {B.PROGRAMS_STOPPED}"
    )
    assert S.get_row(app.state.platform.engine, sid) is not None


async def test_a_refused_continue_leaves_the_account_and_its_card(tmp_path, work, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    card = {"account": "acct-old", "account_name": "Old", "kind": "limit", "turn_id": "t0"}
    S.update(engine, sid, setup={"account": "acct-old", "account_name": "Old", "account_limit": card})
    before = setup_of(app, sid)
    assert before["account"] == "acct-old" and before["account_limit"]["kind"] == "limit"

    monkeypatch.setattr(B, "continue_problem", lambda *a, **kw: None)
    monkeypatch.setattr(B, "account_patch", lambda platform, setup, account, model=None: {
        "account": "acct-new", "account_name": "New", "account_wait": None, "account_limit": None,
        "account_moves": None,
    })

    async def refused_start(*a, **kw):
        raise HTTPException(status_code=409, detail="This turn was refused for the test.")

    monkeypatch.setattr(B, "start_build_turn", refused_start)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/account",
                              {"account": "acct-new", "continue": True})
    assert status == 409 and body["detail"] == "This turn was refused for the test."
    after = setup_of(app, sid)
    for key in ("account", "account_name", "account_limit", "account_wait", "account_moves"):
        assert after.get(key) == before.get(key), key


# --------------------------------------------------------------------------- #
# S11: an accepted suggested edit can be undone
# --------------------------------------------------------------------------- #


def _edit_row(app, work, *, before: str, after: str, thread_id: str = "t-lib") -> HarnessLibraryRecord:
    return L._mint(app.state.platform.engine, HarnessLibraryRecord(
        folder_key=L.folder_key(work), folder=str(work), kind="layer_edit", file="memory.md",
        text="Keep where add() lives.", before=before, after=after, signature="sig-edit",
        thread_id=thread_id, turn_id="t1",
    ))


async def test_undo_puts_back_the_old_text_of_an_accepted_edit(tmp_path, work):
    app = create_app(str(tmp_path / "home"))
    path = work / ".ironjarvis" / "harness" / "memory.md"
    old = b"Old notes\r\nkept as they were\r\n"
    path.write_bytes(old)
    row = _edit_row(app, work, before="Old notes\nkept as they were\n",
                    after="Old notes\nkept as they were\nadd() lives in calc.py.\n")
    status, done = await asgi(app, "POST", f"/build/library/{row.id}/accept")
    assert status == 200 and done["status"] == "accepted", done
    assert "add() lives in calc.py." in path.read_text(encoding="utf-8")
    status, listed = await asgi(app, "GET", f"/undo?session_id={S.session_id_for('t-lib')}")
    assert status == 200
    actions = listed["actions"]
    assert len(actions) == 1 and actions[0]["undoable"] and actions[0]["kind"] == "file_restore"
    assert actions[0]["path"] == ".ironjarvis/harness/memory.md" and actions[0]["workspace"] == str(work)
    status, out = await asgi(app, "POST", f"/undo/{actions[0]['action_id']}")
    assert status == 200, out
    assert path.read_bytes() == old


async def test_undo_removes_a_file_an_accepted_edit_made(tmp_path, work):
    app = create_app(str(tmp_path / "home"))
    path = work / ".ironjarvis" / "harness" / "memory.md"
    assert not path.exists()
    row = _edit_row(app, work, before="", after="add() lives in calc.py.\n")
    status, done = await asgi(app, "POST", f"/build/library/{row.id}/accept")
    assert status == 200 and path.exists()
    status, listed = await asgi(app, "GET", f"/undo?session_id={S.session_id_for('t-lib')}")
    actions = listed["actions"]
    assert len(actions) == 1 and actions[0]["kind"] == "file_delete"
    status, out = await asgi(app, "POST", f"/undo/{actions[0]['action_id']}")
    assert status == 200, out
    assert not path.exists()


async def test_a_failed_check_puts_the_file_back_atomically_and_leaves_no_undo(tmp_path, work, monkeypatch):
    from iron_jarvis.harness import components

    app = create_app(str(tmp_path / "home"))
    path = work / ".ironjarvis" / "harness" / "memory.md"
    old = b"Old notes\n"
    path.write_bytes(old)
    row = _edit_row(app, work, before="Old notes\n", after="Old notes\nmore\n")
    real_read, real_write = components.read_project_layer, L._atomic_write
    writes: list[str] = []

    def invalid(folder, **kw):
        real_read(folder, **kw)
        return components.ProjectLayer(present=True, note="made invalid for the test")

    def recorded(p, text):
        writes.append(text)
        real_write(p, text)

    monkeypatch.setattr(components, "read_project_layer", invalid)
    monkeypatch.setattr(L, "_atomic_write", recorded)
    status, body = await asgi(app, "POST", f"/build/library/{row.id}/accept")
    assert status == 409 and "made invalid for the test" in body["detail"]
    assert path.read_bytes() == old
    # The put-back is the beside-then-swap write too.
    assert writes == ["Old notes\nmore\n", "Old notes\n"]
    status, listed = await asgi(app, "GET", f"/undo?session_id={S.session_id_for('t-lib')}")
    assert listed["actions"] == []


# --------------------------------------------------------------------------- #
# S12: the line cap drops whole turns
# --------------------------------------------------------------------------- #


def _turns(n: int, *, prefix: str = "t") -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        tid = f"{prefix}{i}"
        out += [
            {"role": "user", "content": f"question {i}", "turnId": tid},
            {"role": "user", "content": f"note {i}", "turnId": tid, "steer": True},
            {"role": "assistant", "content": f"reply {i}", "turnId": tid},
        ]
    return out


def test_a_205_line_session_starts_at_a_question(tmp_path):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {})
    lines = _turns(68) + [{"role": "user", "content": "question 68", "turnId": "t68"}]
    assert len(lines) == 205
    for k in range(0, len(lines), 50):
        S.append_lines(engine, row.id, lines[k:k + 50])
    kept = messages_of(app, row.id)
    assert len(kept) <= S.MAX_MESSAGES
    first = kept[0]
    assert first["role"] == "user" and not first.get("steer") and first["content"] == "question 2", first
    # Nothing newer was dropped.
    assert kept[-1]["content"] == "question 68" and len(kept) == 205 - 6


def test_a_branched_session_also_starts_at_a_question(tmp_path):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    lines = _turns(68) + [{"role": "user", "content": "question 68", "turnId": "t68"}]
    row = S.create_with_lines(engine, str(tmp_path), "", {}, lines)
    kept = messages_of(app, row.id)
    assert kept[0]["content"] == "question 2" and len(kept) == 199


def test_one_turn_longer_than_the_cap_keeps_its_newest_lines(tmp_path):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {})
    lines = [{"role": "user", "content": "the question", "turnId": "big"}] + [
        {"role": "user", "content": f"note {i}", "turnId": "big", "steer": True} for i in range(210)
    ]
    S.append_lines(engine, row.id, lines)
    kept = messages_of(app, row.id)
    assert len(kept) == S.MAX_MESSAGES and kept[-1]["content"] == "note 209"
