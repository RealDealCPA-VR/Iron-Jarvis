"""An @ reply comes home (v1.345.0, lane S): ``POST /chat/panel`` with a saved
``thread_id`` APPENDS the round's replies to THAT chat before answering, and a
remote that reports back into a chat-bound room lands in the chat it was
asked in — driven through the real app, the real round (the offline mock
adapter answers a local seat; a fake ``httpx`` post answers a remote).

THE BUG this pins against (the user's install, 2026-10-16): a six-minute
remote round answered after the user had opened ANOTHER saved chat, and the
page's continuation saved the reply over that chat. Here the reply is written
by the daemon into the chat it was asked in, the other chat is untouched, and
without ``thread_id`` the route's answer is byte-identical to before.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


class _FakeResp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _fake_remote(monkeypatch, responder):
    async def fake_post(self, url, json=None, headers=None, **kw):
        return responder(url, json)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _add_remote(client, name="hermes"):
    r = client.post(
        "/agents/remote",
        json={"name": name, "base_url": "http://192.168.1.50:8080/run", "kind": "http-task",
              "token": "t", "model": "m"},
    )
    assert r.status_code == 200, r.text


def _saved_chat(client, text="find me a place to eat in orlando") -> dict:
    made = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": text}]})
    assert made.status_code == 200, made.text
    return made.json()


def _thread(client, tid) -> dict:
    r = client.get(f"/chat/threads/{tid}")
    assert r.status_code == 200, r.text
    return r.json()


def _room_row(app, room_id):
    with session_scope(app.state.platform.engine) as db:
        from iron_jarvis.agents.threads import AgentThreadRecord

        rec = db.get(AgentThreadRecord, room_id)
        assert rec is not None
        return {"chat_thread_id": rec.chat_thread_id, "participants": json.loads(rec.participants_json)}


# --------------------------------------------------------------------------- #
# POST /chat/panel with thread_id
# --------------------------------------------------------------------------- #
def test_a_panel_round_with_a_thread_id_appends_the_reply_to_that_chat(app):
    client = TestClient(app)
    asked = _saved_chat(client, "@builder what should the brief cover?")
    tid, stale = asked["id"], asked["updated_at"]
    r = client.post(
        "/chat/panel",
        json={
            "message": "@builder what should the brief cover?",
            "chat_thread_id": tid,
            "thread_id": tid,
            "history": [{"who": "user", "content": "@builder what should the brief cover?"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "panel" and body["spoke"] == ["builtin:builder"]
    # the answer says it appended and hands the page its next stamp
    assert body["appended"] is True
    assert body["updated_at"] and body["updated_at"] != stale
    got = _thread(client, tid)
    assert got["updated_at"] == body["updated_at"]
    assert len(got["messages"]) == 2, got["messages"]
    line = got["messages"][-1]
    # EXACTLY the page's sendPanel shape
    assert line["role"] == "assistant"
    assert line["content"] and line["content"] != "(no reply)"
    assert line["panelWho"] == "builtin:builder"
    assert line["panelThreadId"] == body["thread_id"]
    assert "panelError" not in line and "panelNote" not in line and "panelKind" not in line
    assert line["at"].endswith("+00:00")
    # the room is bound to the chat
    assert _room_row(app, body["thread_id"])["chat_thread_id"] == tid


def test_the_saved_thread_id_alone_binds_the_room(app):
    """``thread_id`` is the binding id even when the page sent no
    ``chat_thread_id`` (a chat saved at send has one id — the saved one)."""
    client = TestClient(app)
    saved = _saved_chat(client, "@builder plan the brief")
    body = client.post(
        "/chat/panel", json={"message": "@builder plan the brief", "thread_id": saved["id"]}
    ).json()
    assert body["appended"] is True
    assert _room_row(app, body["thread_id"])["chat_thread_id"] == saved["id"]
    # the next round from the same chat reuses that room
    again = client.post(
        "/chat/panel", json={"message": "@builder and the budget?", "thread_id": saved["id"]}
    ).json()
    assert again["thread_id"] == body["thread_id"]
    assert len(_thread(client, saved["id"])["messages"]) == 3


def test_the_other_open_chat_is_untouched_and_the_stale_page_409s(app):
    """The user's exact shape: the round was asked in chat A; chat B is open
    when it answers. B keeps its two messages; A holds the reply; a page save
    over A with the pre-round stamp is refused."""
    client = TestClient(app)
    a = _saved_chat(client, "@builder do some tax research")
    b_msgs = [
        {"role": "user", "content": "find me a place to eat in orlando"},
        {"role": "assistant", "content": "Try the Ravenous Pig."},
    ]
    b = client.put("/chat/threads/new", json={"messages": b_msgs}).json()
    body = client.post(
        "/chat/panel",
        json={"message": "@builder do some tax research", "chat_thread_id": a["id"], "thread_id": a["id"]},
    ).json()
    assert body["appended"] is True
    got_b = _thread(client, b["id"])
    assert [m["content"] for m in got_b["messages"]] == [m["content"] for m in b_msgs]
    assert got_b["updated_at"] == b["updated_at"]
    got_a = _thread(client, a["id"])
    assert got_a["messages"][-1]["panelWho"] == "builtin:builder"
    # the page's old continuation (full rewrite with the stale stamp) is refused
    r = client.put(
        f"/chat/threads/{a['id']}",
        json={"messages": [{"role": "user", "content": "@builder do some tax research"}],
              "if_updated_at": a["updated_at"]},
    )
    assert r.status_code == 409


def test_without_a_thread_id_the_answer_and_the_chats_are_byte_identical(app):
    client = TestClient(app)
    saved = _saved_chat(client)
    body = client.post(
        "/chat/panel", json={"message": "@builder what next?", "chat_thread_id": saved["id"]}
    ).json()
    assert set(body) == {"mode", "thread_id", "unknown_mentions", "entries", "spoke", "skipped"}, body
    assert "appended" not in body and "updated_at" not in body
    got = _thread(client, saved["id"])
    assert len(got["messages"]) == 1 and got["updated_at"] == saved["updated_at"]


def test_a_thread_id_that_names_no_saved_chat_is_said_not_a_failure(app):
    client = TestClient(app)
    body = client.post(
        "/chat/panel", json={"message": "@builder hi", "chat_thread_id": "c1", "thread_id": "chat_gone"}
    ).json()
    assert body["mode"] == "panel"
    assert body["appended"] is False and body["updated_at"] is None


def test_a_remote_that_will_report_back_leaves_a_progress_line(app, monkeypatch):
    _fake_remote(monkeypatch, lambda url, body: _FakeResp(202, None, "queued"))
    client = TestClient(app)
    _add_remote(client)
    saved = _saved_chat(client, "@hermes do some tax research")
    body = client.post(
        "/chat/panel",
        json={"message": "@hermes do some tax research", "chat_thread_id": saved["id"],
              "thread_id": saved["id"]},
    ).json()
    assert body["spoke"] == ["remote:hermes"] and body["appended"] is True
    line = _thread(client, saved["id"])["messages"][-1]
    assert line["panelWho"] == "remote:hermes"
    assert line["panelKind"] == "progress"
    # v1.355.0 (G62, on purpose): message-back is OFF for this remote, so
    # the line says plainly that its answer will not come back here.
    assert "Its answer will not come back here" in line["content"]
    assert line["panelThreadId"] == body["thread_id"]


def test_a_failed_seat_and_the_context_drop_keep_the_pages_marks(app):
    """``panel_reply_lines`` mirrors sendPanel: an error entry is "(no reply)"
    + panelError, the drop note rides the FIRST reply only."""
    lines = agents_routes.panel_reply_lines(
        {
            "entries": [
                {"who": "user", "content": "@a @b go"},
                {"who": "builtin:a", "content": "", "error": "a couldn't answer: boom"},
                {"who": "remote:b", "content": "ok", "documents": ["C:/x/y.pdf"]},
            ],
            "context": {"chat_messages": 3, "chat_dropped": 1},
        },
        "athr_7",
    )
    assert lines == [
        {
            "role": "assistant",
            "content": "(no reply)",
            "panelWho": "builtin:a",
            "panelThreadId": "athr_7",
            "panelError": True,
            "panelNote": "Left out 1 older message to fit the agent's model",
        },
        {
            "role": "assistant",
            "content": "ok",
            "panelWho": "remote:b",
            "panelThreadId": "athr_7",
            "documents": ["C:/x/y.pdf"],
        },
    ]
    plural = agents_routes.panel_reply_lines(
        {"entries": [{"who": "builtin:a", "content": "x"}], "context": {"chat_dropped": 2}}, "r"
    )
    # v1.357.0 integrate (G75 words, moved on purpose)
    assert plural[0]["panelNote"] == "Left out 2 older messages to fit the agent's model"


def test_a_session_verdict_is_unchanged_by_a_thread_id(app):
    client = TestClient(app)
    saved = _saved_chat(client)
    body = client.post(
        "/chat/panel",
        json={"message": "@builder write me a PDF of the brief", "hands": True,
              "chat_thread_id": saved["id"], "thread_id": saved["id"]},
    ).json()
    assert body["mode"] == "session" and body["target"] == "builder"
    assert "appended" not in body
    assert len(_thread(client, saved["id"])["messages"]) == 1


# --------------------------------------------------------------------------- #
# Remote inbound into a chat-bound room
# --------------------------------------------------------------------------- #
def _enable(client, name="hermes"):
    r = client.post(f"/agents/remote/{name}/inbound/enable", json={})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_an_inbound_line_for_a_chat_bound_room_lands_in_the_chat_with_its_files(app, tmp_path, monkeypatch):
    _fake_remote(monkeypatch, lambda url, body: _FakeResp(202, None, "queued"))
    client = TestClient(app)
    _add_remote(client)
    saved = _saved_chat(client, "@hermes do some tax research")
    round_ = client.post(
        "/chat/panel",
        json={"message": "@hermes do some tax research", "chat_thread_id": saved["id"],
              "thread_id": saved["id"]},
    ).json()
    room = round_["thread_id"]
    token = _enable(client)
    seen: list = []
    app.state.platform.event_bus.add_handler(lambda e: seen.append(e))
    r = client.post(
        "/agents/remote/hermes/inbound",
        json={
            "conversation_id": room,
            "message": "Done — the research is attached.",
            "kind": "done",
            "files": [{"name": "research.md", "content_b64": base64.b64encode(b"# tax").decode()}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["homed"] is True and out["phoned"] is False
    got = _thread(client, saved["id"])
    # the @ line, the progress line, then the remote's report
    assert [m.get("panelKind") for m in got["messages"]] == [None, "progress", "done"]
    line = got["messages"][-1]
    assert line["role"] == "assistant" and line["panelWho"] == "remote:hermes"
    assert line["panelThreadId"] == room
    assert line["content"].startswith("Done — the research is attached.")
    assert len(line["documents"]) == 1
    path = line["documents"][0]
    assert "remote-inbox" in path and path.endswith("research.md")
    assert str(tmp_path) in path or path[1:3] == ":\\" or path.startswith("/")
    with open(path, "rb") as fh:
        assert fh.read() == b"# tax"
    # the dedupe key the page's live mirror uses = the room entry's own ``at``
    room_last = client.get(f"/agents/threads/{room}").json()["messages"][-1]
    assert room_last["inbound"] is True and line["panelAt"] == room_last["at"]
    # and the chat was told
    updated = [e for e in seen if e.type == "chat.thread_updated"]
    assert updated and updated[-1].payload == {"thread_id": saved["id"], "who": "remote:hermes", "entries": 3}


def test_a_room_bound_to_no_chat_homes_nothing(app, monkeypatch):
    client = TestClient(app)
    _add_remote(client)
    threads = AgentThreads(app.state.platform.engine)
    rec = threads.for_chat("", title="orphan")
    from iron_jarvis.agents.threads import clean_participants

    threads.add_participants(
        rec.id, clean_participants([{"source": "remote", "name": "hermes", "role": "participant"}])
    )
    calls: list = []
    monkeypatch.setattr(
        agents_routes, "append_to_chat_thread", lambda *a, **k: calls.append(a) or {"ok": False}
    )
    token = _enable(client)
    r = client.post(
        "/agents/remote/hermes/inbound",
        json={"conversation_id": rec.id, "message": "hello?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200 and r.json()["homed"] is False
    assert calls == [], "no chat to come home to"


def test_a_phone_bound_room_keeps_the_phone_path_and_never_both(app, monkeypatch):
    """A room bound to a DAEMON-owned (phone) thread goes through the comm
    store + the phone exactly as v1.285.0 — the chat writer is never called."""
    import asyncio
    from types import SimpleNamespace

    from iron_jarvis.comm.inbound import InboundPoller
    from iron_jarvis.comm.threads import CommThreadStore

    _fake_remote(monkeypatch, lambda url, body: _FakeResp(200, {"result": "on it"}))
    client = TestClient(app)
    _add_remote(client)
    platform = app.state.platform
    store = CommThreadStore(platform.engine, event_bus=platform.event_bus)

    async def _turn(platform_, personas, body):
        return {"reply": "Jarvis here.", "escalate": False}

    poller = InboundPoller(
        platform.notifier, SimpleNamespace(), platform.engine, event_bus=platform.event_bus,
        thread_store=store, chat_turn=_turn, personas={}, platform=platform,
    )

    class _Chan:
        reflex_source = "comm"

        def __init__(self):
            self.sent: list[str] = []

        def is_authorized(self, sender_id):
            return True

        def send(self, message, **kw):
            self.sent.append(message)
            return {"ok": True}

        inbound_enabled = True
        chat_enabled = True

    ch = _Chan()
    msg = SimpleNamespace(sender_id="u1", reply_to="u1", text="@hermes start the job",
                          is_bot=False, sender_name="Val")
    out = asyncio.run(poller._handle_chat("telegram", ch, msg, "@hermes start the job", "Val"))
    tid = out["thread_id"]
    room = AgentThreads(platform.engine).for_chat(tid)
    app.state.d.comm_thread_store = store
    poller.inbound_channels = lambda: [("telegram", ch)]  # type: ignore[method-assign]
    app.state.d.inbound_poller = poller
    calls: list = []
    real = agents_routes.append_to_chat_thread
    monkeypatch.setattr(
        agents_routes, "append_to_chat_thread",
        lambda *a, **k: calls.append(a) or real(*a, **k),
    )
    before = len(client.get(f"/chat/threads/{tid}").json()["messages"])
    token = _enable(client)
    r = client.post(
        "/agents/remote/hermes/inbound",
        json={"conversation_id": room.id, "message": "Done — 3 files.", "kind": "done"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["phoned"] is True and r.json()["homed"] is False
    assert ch.sent[-1] == "hermes: Done — 3 files."
    assert calls == [], "the phone path is the comm store's, never the chat writer's"
    msgs = client.get(f"/chat/threads/{tid}").json()["messages"]
    assert len(msgs) == before + 1, "exactly one line — never both paths"
    assert msgs[-1]["panelWho"] == "remote:hermes" and "panelThreadId" not in msgs[-1]
