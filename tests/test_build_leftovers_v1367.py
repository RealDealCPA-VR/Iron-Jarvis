"""v1.367.0 (IJ harness Phase 4) — the leftovers the review named, closed at
ship time. Each pin was checked by putting the old behaviour back.

1. ``DELETE /chat/threads/{id}`` on a Build session that is still answering
   is refused with the same 409 ``DELETE /build/sessions/{id}`` gives — the
   chat door must not be the way around it.
2. Remember and Turn into workflow are the chat page's doors; a Build
   session is never a chat, so both refuse it in words (409) instead of
   writing a memory / draft that links to a chat page that shows nothing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.build import RUNNING_SENTENCE
from iron_jarvis.daemon.routes.chat import BUILD_SESSION_NOT_A_CHAT
from tests.test_build_sessions_v1367 import new_session


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


def _folder(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "a.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _with_messages(client, sid: str) -> None:
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, sid)
        row.messages_json = (
            '[{"role":"user","content":"fix the bug"},'
            '{"role":"assistant","content":"Fixed it in a.py."}]'
        )
        db.add(row)


def test_chat_delete_refuses_a_build_session_that_is_still_answering(tmp_path):
    with _client(tmp_path) as client:
        sid = new_session(client, _folder(tmp_path))["id"]
        entry = TURNS.new_thread_turn(sid)
        TURNS.enter_thread(entry)
        try:
            r = client.delete(f"/chat/threads/{sid}")
            assert r.status_code == 409, r.text
            assert r.json()["detail"] == RUNNING_SENTENCE
            assert client.get(f"/build/sessions/{sid}").status_code == 200
        finally:
            TURNS.leave_thread(entry)
        # Once it is idle the chat door deletes it as before.
        assert client.delete(f"/chat/threads/{sid}").status_code == 200
        assert client.get(f"/build/sessions/{sid}").status_code == 404


def test_chat_delete_still_deletes_a_running_plain_chat(tmp_path):
    """The refusal is for Build sessions only: a chat's delete is unchanged."""
    with _client(tmp_path) as client:
        r = client.put("/chat/threads/new", json={
            "title": "plain", "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        entry = TURNS.new_thread_turn(tid)
        TURNS.enter_thread(entry)
        try:
            assert client.delete(f"/chat/threads/{tid}").status_code == 200
        finally:
            TURNS.leave_thread(entry)


def test_remember_and_crystallize_refuse_a_build_session(tmp_path):
    with _client(tmp_path) as client:
        sid = new_session(client, _folder(tmp_path))["id"]
        _with_messages(client, sid)
        for path in (f"/chat/threads/{sid}/remember", f"/chat/threads/{sid}/crystallize"):
            r = client.post(path, json={})
            assert r.status_code == 409, (path, r.text)
            assert r.json()["detail"] == BUILD_SESSION_NOT_A_CHAT


# --------------------------------------------------------------------------- #
# 3. an approved PROSE plan keeps the steps plan_update gave
# --------------------------------------------------------------------------- #


def _frames(watch, frames):
    import json as _json

    for event, data in frames:
        watch.see(event, _json.dumps(data))


async def _approved_steps(monkeypatch, reply: str, plan_args) -> list:
    from iron_jarvis.daemon.routes import build as B

    written: dict = {}

    def fake_update(engine, thread_id, setup=None, **_kw):
        written.update(setup or {})

    monkeypatch.setattr(B.S, "update", fake_update)
    watch = B._ReplyWatch()
    frames = []
    if plan_args is not None:
        frames += [
            ("tool_call", {"id": "p1", "name": "plan_update", "status": "started", "args": plan_args}),
            ("tool_call", {"id": "p1", "name": "plan_update", "status": "finished", "ok": True}),
        ]
    frames += [
        ("token", {"text": reply}),
        ("tool_call", {"id": "x1", "name": "exit_plan_mode", "status": "started", "args": {}}),
        ("tool_call", {"id": "x1", "name": "exit_plan_mode", "status": "finished", "ok": True}),
    ]
    _frames(watch, frames)
    await B._apply_session_changes(object(), "chat_x", watch)
    return written["plan"]["steps"]


async def test_a_prose_plan_keeps_the_steps_plan_update_gave(monkeypatch):
    steps = await _approved_steps(
        monkeypatch,
        "I will read greet.py, add a farewell function, then run the check.",
        {"plan": [
            {"step": "Read greet.py", "status": "completed"},
            {"step": "Add farewell()", "status": "in_progress"},
            {"step": "Run the check", "status": "pending"},
        ]},
    )
    assert [s["step"] for s in steps] == ["Read greet.py", "Add farewell()", "Run the check"]
    assert {s["status"] for s in steps} == {"pending"}


async def test_a_listed_plan_still_wins_over_plan_update(monkeypatch):
    steps = await _approved_steps(
        monkeypatch,
        "Plan:\n1. Open greet.py\n2. Write farewell\n",
        {"plan": [{"step": "Something else", "status": "in_progress"}]},
    )
    assert [s["step"] for s in steps] == ["Open greet.py", "Write farewell"]


async def test_no_plan_update_and_prose_keeps_no_steps(monkeypatch):
    assert await _approved_steps(monkeypatch, "Just prose here.", None) == []
