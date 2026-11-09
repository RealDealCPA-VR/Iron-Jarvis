"""v1.459.0 — a remote's report for a job handed over from a NEW chat comes
home to that chat.

Readiness 0k left this open: a new chat's first question goes out before the
page's first save names the chat, so ``ToolContext.thread_id`` is "" and the
job's room (``agents.remote.open_handoff_room``) had no ``home_chat_id``: the
remote's later report stayed in its agent room. The room now keeps the chat
TURN (``home_turn_id``) and the inbound route reads the chat that turn was
linked to (``POST /chat/turns/{id}/thread`` → ``ChatTurnRecord.thread_id``),
writes it onto the room, and appends the report there — the media jobs' rule.

Pinned through the real tools, the real inbound route and the real chat store.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.remote import DelegateRemoteTool
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext
from tests.test_remote_report_comes_home_wavef import _chat_messages, _fake_202, _new_chat, _setup


def _turn_ctx(p, turn_id: str) -> ToolContext:
    return ToolContext(
        workspace=p.config.workspaces_dir, session_id="chat", agent_run_id="",
        config=p.config, event_bus=p.event_bus, engine=p.engine, thread_id="", turn_id=turn_id,
    )


def _link(p, turn_id: str, chat: str) -> None:
    """What ``POST /chat/turns/{id}/thread`` leaves behind once the page's
    first save names the chat."""
    with session_scope(p.engine) as db:
        db.add(ChatTurnRecord(id=turn_id, thread_id=chat, status="done"))
        db.commit()


def _report(client, token: str, room: str, text: str):
    return client.post(
        "/agents/remote/hermes/inbound",
        json={"conversation_id": room, "message": text, "kind": "done"},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_a_report_for_a_job_from_a_new_chat_comes_home_once_the_chat_is_saved(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_202(monkeypatch)
    with TestClient(app) as client:
        token = _setup(client)
        res = asyncio.run(
            DelegateRemoteTool(p).execute(
                {"agent": "hermes", "task": "check the books"}, _turn_ctx(p, "turn_new1")
            )
        )
        assert res.ok, res.error
        room = res.data["conversation_id"]
        rec = AgentThreads(p.engine).get(room)
        assert (rec.home_chat_id or "") == "" and rec.home_turn_id == "turn_new1"

        chat = _new_chat(client)
        _link(p, "turn_new1", chat)
        r = _report(client, token, room, "Books balance.")
        assert r.status_code == 200, r.text
        assert r.json()["homed"] is True
        assert _chat_messages(p, chat)[-1]["content"] == "Books balance."
        # the chat is kept on the room: the next report needs no lookup
        assert AgentThreads(p.engine).get(room).home_chat_id == chat


def test_the_delegate_tool_carries_the_turn_too(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_202(monkeypatch)
    with TestClient(app) as client:
        token = _setup(client)
        res = asyncio.run(
            DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "summarise the filings"},
                _turn_ctx(p, "turn_new2"),
            )
        )
        assert res.ok, res.error
        room = res.data["conversation_id"]
        chat = _new_chat(client)
        _link(p, "turn_new2", chat)
        r = _report(client, token, room, "Revenue is up 4%.")
        assert r.json()["homed"] is True
        assert _chat_messages(p, chat)[-1]["content"] == "Revenue is up 4%."


def test_a_chat_that_was_never_saved_keeps_the_report_in_its_room(tmp_path, monkeypatch):
    """Anti-vacuity: no link, no chat — the old behaviour."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_202(monkeypatch)
    with TestClient(app) as client:
        token = _setup(client)
        res = asyncio.run(
            DelegateRemoteTool(p).execute(
                {"agent": "hermes", "task": "check the books"}, _turn_ctx(p, "turn_never")
            )
        )
        room = res.data["conversation_id"]
        r = _report(client, token, room, "Books balance.")
        assert r.status_code == 200, r.text
        assert r.json()["homed"] is False
        assert (AgentThreads(p.engine).get(room).home_chat_id or "") == ""


def test_a_chat_id_wins_and_no_turn_is_kept(tmp_path):
    app = create_app(str(tmp_path))
    rec = AgentThreads(app.state.platform.engine).create("x", [], "", "chat_1", "turn_1")
    assert rec.home_chat_id == "chat_1" and (rec.home_turn_id or "") == ""
