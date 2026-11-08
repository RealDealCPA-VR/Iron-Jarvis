"""Readiness 0j.5 item 10 — a remote's report-back from a CHAT-STARTED agent
run reaches the chat.

A remote that accepts a job (``202``) reports back later into the job's own
room (``agents.remote.open_handoff_room``). That room had no chat, so the
inbound route's v1.345.0 homing (``chat_append``) never appended the report:
the user read "hermes accepted the task and will report back" in the chat and
the report never arrived there. Now the room carries the chat the job came
from (``ToolContext.thread_id`` = the run's ``options["chat_thread_id"]``) as
its ``home_chat_id`` — never as ``chat_thread_id``, which is the chat's one
@-panel room — and the report is appended to that chat, with no
``panelThreadId`` (so the chat's own @-room is never replaced by the job's).
The ``remote.message`` event names the chat, which the bell opens.

Pinned through the real delegate tool, the real registry call (a fake httpx
post answering 202), the real inbound route and the real chat thread store.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.remote import DelegateRemoteTool
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext


class _Resp:
    status_code = 202
    text = ""

    def json(self):
        raise ValueError("no body")


def _fake_202(monkeypatch) -> list:
    sent: list = []

    async def post(self, url, json=None, headers=None, **kw):
        sent.append(json or {})
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return sent


def _ctx(p, sid: str, thread_id: str = "") -> ToolContext:
    return ToolContext(
        workspace=p.config.workspaces_dir, session_id=sid, agent_run_id="root-run",
        config=p.config, event_bus=p.event_bus, engine=p.engine, thread_id=thread_id,
    )


def _setup(client) -> str:
    r = client.post(
        "/agents/remote",
        json={"name": "hermes", "base_url": "http://192.168.1.50:8080/run",
              "kind": "http-task", "token": "t"},
    )
    assert r.status_code == 200, r.text
    out = client.post("/agents/remote/hermes/inbound/enable", json={})
    assert out.status_code == 200, out.text
    return out.json()["token"]


def _new_chat(client) -> str:
    r = client.put(
        "/chat/threads/new",
        json={"title": "Q3 filings", "messages": [
            {"role": "user", "content": "have hermes summarise the filings"},
        ]},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _chat_messages(p, thread_id: str) -> list[dict]:
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(p.engine) as db:
        rec = db.get(ChatThreadRecord, thread_id)
        return json.loads(rec.messages_json or "[]")


def _events(p, etype) -> list:
    seen: list = []

    def h(ev):
        if ev.type == etype:
            seen.append(ev.payload)

    p.event_bus.add_handler(h)
    return seen


def test_a_chat_started_runs_remote_report_comes_home_to_the_chat(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    sent = _fake_202(monkeypatch)
    with TestClient(app) as client:
        token = _setup(client)
        chat = _new_chat(client)
        events = _events(p, EventType.REMOTE_MESSAGE)

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="chat",
                chat_thread_id=chat,
            )
            return await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "summarise the filings"},
                _ctx(p, root.id, thread_id=chat),
            )

        res = asyncio.run(drive())
        assert res.ok is True, res.error
        room = res.data["conversation_id"]
        assert room and sent[-1]["conversation_id"] == room
        rec = AgentThreads(p.engine).get(room)
        assert rec.home_chat_id == chat
        # the job's room is NEVER the chat's @-panel room
        assert (rec.chat_thread_id or "") == ""
        assert AgentThreads(p.engine).for_chat(chat).id != room

        r = client.post(
            "/agents/remote/hermes/inbound",
            json={"conversation_id": room, "message": "Revenue is up 4%.", "kind": "done"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["homed"] is True
        msgs = _chat_messages(p, chat)
        last = msgs[-1]
        assert last["role"] == "assistant"
        assert last["content"] == "Revenue is up 4%."
        assert last["panelWho"] == "remote:hermes"
        assert last["panelKind"] == "done"
        assert "panelThreadId" not in last
        # the bell's event names the chat that holds the report
        asyncio.run(asyncio.sleep(0))
        assert any(e.get("chat_thread_id") == chat and e.get("kind") == "done" for e in events), events


def test_a_job_with_no_chat_keeps_its_report_in_its_room(tmp_path, monkeypatch):
    """Anti-vacuity: an objective's teammate (no chat) is unchanged."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_202(monkeypatch)
    with TestClient(app) as client:
        token = _setup(client)

        async def drive():
            root = await Orchestrator(p).create_session(
                "objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission"
            )
            return await DelegateTool(p).execute(
                {"agent_type": "remote:hermes", "task": "do it"}, _ctx(p, root.id)
            )

        res = asyncio.run(drive())
        room = res.data["conversation_id"]
        assert (AgentThreads(p.engine).get(room).home_chat_id or "") == ""
        r = client.post(
            "/agents/remote/hermes/inbound",
            json={"conversation_id": room, "message": "done", "kind": "done"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["homed"] is False


def test_the_delegate_remote_tool_from_a_chat_homes_too(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    p = app.state.platform
    _fake_202(monkeypatch)
    with TestClient(app) as client:
        token = _setup(client)
        chat = _new_chat(client)
        res = asyncio.run(
            DelegateRemoteTool(p).execute(
                {"agent": "hermes", "task": "check the books"}, _ctx(p, "chat", thread_id=chat)
            )
        )
        room = res.data["conversation_id"]
        r = client.post(
            "/agents/remote/hermes/inbound",
            json={"conversation_id": room, "message": "Books balance.", "kind": "done"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["homed"] is True
        assert _chat_messages(p, chat)[-1]["content"] == "Books balance."
