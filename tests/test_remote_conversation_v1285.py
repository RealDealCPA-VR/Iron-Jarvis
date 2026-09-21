"""A remote agent is a conversation, both ways (v1.285.0).

The user's question, verbatim: "what is stopping my from interacting with my
remote agents the way i can with slack?" The answer was the transport: one POST
per turn carrying only the task text, no memory between calls, and no way for
the remote to reach back. Pinned here, end to end through the real routes, the
real ``AgentThreads`` round and a fake ``httpx`` post (the v1.157.0 harness):

1. OUTWARD — ``RemoteAgentRegistry.run`` carries the exchange as real prior
   turns (OpenAI dialects) or as ``history`` + ``conversation_id`` + ``reply_to``
   beside ``task`` (``http-task``); nothing is sent when nothing is given, so an
   older endpoint sees the old body byte for byte; a ``202`` / ``accepted``
   answer is recorded as "working, will report back", never as a timeout.
2. THE ROUND — a remote seat is shown the chat (its own lines unprefixed, the
   user as user, everyone else named), and its room id rides as the
   conversation id.
3. INWARD — ``POST /agents/remote/{name}/inbound`` is token-exempt in the
   middleware and verified FAIL-CLOSED in the handler with the remote's OWN
   inbound token (minted once by ``/inbound/enable``, rotated on re-enable,
   forgotten by ``/inbound/disable``); the remote may only post into a room it
   sits in; files land under the per-agent inbox through the v1.157.0 trust
   boundary; the line is a room entry (``inbound: True``, ``kind``) and an event.
4. THE PHONE — "@hermes …" on a chat-enabled channel goes to the remote as a
   conversation (the thread as rows), its reply comes back named on the same
   thread and phone, the conversation STAYS with it, "@jarvis" ends it; and an
   inbound post into a room bound to a phone thread lands on that thread and
   is sent to the phone.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.agents.threads import AgentThreads, clean_participants
from iron_jarvis.comm.inbound import BACK_TO_JARVIS_REPLY, InboundPoller
from iron_jarvis.comm.threads import ADDRESSEE_KEY, CommThreadStore
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import _is_exempt


class _FakeResp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _install_fake_post(monkeypatch, captured: list, responder=None) -> None:
    """Record every outward POST; answer with ``responder(url, json)`` or a
    canned ``{"result": ...}`` that echoes what it was shown."""

    async def fake_post(self, url, json=None, headers=None, **kw):
        captured.append({"url": url, "json": json, "headers": headers or {}})
        if responder is not None:
            return responder(url, json)
        if json and "messages" in json:
            return _FakeResp(200, {"choices": [{"message": {"content": "chat-said-ok"}}]})
        if json and "input" in json:
            return _FakeResp(200, {"output_text": "responses-said-ok"})
        return _FakeResp(200, {"result": f"http-said-ok ({len((json or {}).get('history') or [])} rows)"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def _add_remote(client, name="hermes", kind="http-task", base="http://192.168.1.50:8080/run"):
    r = client.post(
        "/agents/remote",
        json={"name": name, "base_url": base, "kind": kind, "token": "t", "model": "m"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# (1) Outward: the conversation rides the wire, per dialect; nothing when empty.
# --------------------------------------------------------------------------- #
def test_http_task_carries_history_conversation_and_reply_to(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    reg = RemoteAgentRegistry(app.state.platform.engine)
    rec = reg.upsert("box", "http://192.168.1.9:9000/run", "http-task")
    out = asyncio.run(
        reg.run(
            rec,
            "and now?",
            lambda n: None,
            history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
                {"role": "tool", "content": "coerced to assistant"},
                {"role": "user", "content": "   "},
            ],
            conversation_id="athr_1",
            reply_to="http://desk:8787/agents/remote/box/inbound",
        )
    )
    assert out["ok"] and not out.get("accepted")
    sent = captured[-1]["json"]
    assert sent["task"] == "and now?"
    assert sent["conversation_id"] == "athr_1"
    assert sent["reply_to"] == "http://desk:8787/agents/remote/box/inbound"
    assert sent["history"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "assistant", "content": "coerced to assistant"},
    ]


def test_a_bare_task_is_still_the_old_body(app, monkeypatch):
    """No history, no conversation id, no reply_to → ``{"task"}`` exactly, so
    an endpoint written against v1.157.0 sees no change."""
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    reg = RemoteAgentRegistry(app.state.platform.engine)
    rec = reg.upsert("box", "http://192.168.1.9:9000/run", "http-task")
    asyncio.run(reg.run(rec, "do it", lambda n: None))
    assert captured[-1]["json"] == {"task": "do it"}


def test_openai_dialects_send_real_prior_turns(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    reg = RemoteAgentRegistry(app.state.platform.engine)
    hist = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    chat = reg.upsert("gpt", "http://192.168.1.10:1234/v1", "openai-chat", model="local")
    asyncio.run(reg.run(chat, "next", lambda n: None, history=hist, conversation_id="c"))
    assert captured[-1]["json"]["messages"] == [*hist, {"role": "user", "content": "next"}]
    assert "conversation_id" not in captured[-1]["json"], "not part of that dialect"
    resp = reg.upsert("rsp", "http://192.168.1.11:1234/v1", "openai-responses", model="local")
    asyncio.run(reg.run(resp, "next", lambda n: None, history=hist))
    assert captured[-1]["json"]["input"] == [*hist, {"role": "user", "content": "next"}]
    asyncio.run(reg.run(resp, "alone", lambda n: None))
    assert captured[-1]["json"]["input"] == "alone", "a lone task keeps the string form"


def test_a_202_or_accepted_body_means_working_will_report_back(app, monkeypatch):
    captured: list = []
    _install_fake_post(
        monkeypatch, captured, responder=lambda url, body: _FakeResp(202, None, "queued")
    )
    reg = RemoteAgentRegistry(app.state.platform.engine)
    rec = reg.upsert("box", "http://192.168.1.9:9000/run", "http-task")
    out = asyncio.run(reg.run(rec, "long job", lambda n: None))
    assert out == {"ok": True, "accepted": True, "result": "", "detail": "accepted", "files": []}
    _install_fake_post(
        monkeypatch, captured, responder=lambda url, body: _FakeResp(200, {"accepted": True})
    )
    out = asyncio.run(reg.run(rec, "long job", lambda n: None))
    assert out["accepted"] is True
    # ...but a body that ALSO carries a result is a normal answer.
    _install_fake_post(
        monkeypatch,
        captured,
        responder=lambda url, body: _FakeResp(200, {"accepted": True, "result": "done already"}),
    )
    out = asyncio.run(reg.run(rec, "quick", lambda n: None))
    assert out["ok"] and not out.get("accepted") and out["result"] == "done already"


# --------------------------------------------------------------------------- #
# (2) The round shows a remote the CONVERSATION and names the room.
# --------------------------------------------------------------------------- #
def test_a_remote_seat_is_shown_the_chat_as_a_conversation(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    client = TestClient(app)
    _add_remote(client)
    body = client.post(
        "/chat/panel",
        json={
            "message": "@hermes what would you add?",
            "chat_thread_id": "c1",
            "history": [
                {"who": "user", "content": "we need a Q3 brief"},
                {"who": "jarvis", "content": "Revenue up 8%."},
                {"who": "remote:hermes", "content": "I can pull the ledger."},
            ],
        },
    ).json()
    assert body["mode"] == "panel"
    assert body["spoke"] == ["remote:hermes"]
    sent = captured[-1]["json"]
    assert sent["conversation_id"] == body["thread_id"], "the room IS the conversation id"
    assert sent["history"] == [
        {"role": "user", "content": "we need a Q3 brief"},
        {"role": "assistant", "content": "Iron Jarvis: Revenue up 8%."},
        {"role": "assistant", "content": "I can pull the ledger."},  # its own line, unprefixed
    ]
    assert sent["task"].endswith("what would you add?")
    assert "reply_to" not in sent, "inbound is off — no address to message back to"
    assert body["entries"][-1]["content"].startswith("http-said-ok (3 rows)")


def test_remote_history_shape_is_pinned():
    p = {"key": "remote:hermes", "name": "hermes", "role": "participant"}
    parts = [
        {"key": "builtin:builder", "name": "builder", "role": "lead"},
        p,
    ]
    rows = AgentThreads.remote_history(
        [
            {"who": "user", "content": "q"},
            {"who": "builtin:builder", "content": "a plan"},
            {"who": "remote:hermes", "content": "my take"},
            {"who": "jarvis", "content": "J's line"},
            {"who": "dynamic:remy", "content": ""},  # blank: skipped
            {"who": "dynamic:remy", "content": "yo"},
        ],
        p,
        parts,
    )
    assert rows == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "builder (lead): a plan"},
        {"role": "assistant", "content": "my take"},
        {"role": "assistant", "content": "Iron Jarvis: J's line"},
        {"role": "assistant", "content": "remy (agent): yo"},
    ]


def test_an_accepted_remote_round_is_recorded_as_pending(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured, responder=lambda u, b: _FakeResp(202, None, "queued"))
    client = TestClient(app)
    _add_remote(client)
    body = client.post(
        "/chat/panel", json={"message": "@hermes build the report", "chat_thread_id": "c1"}
    ).json()
    entry = body["entries"][-1]
    assert entry["pending"] is True
    assert "will report back" in entry["content"]
    assert not entry.get("error")


# --------------------------------------------------------------------------- #
# (3) Inward: enable → token once; the door is fail-closed; the line lands.
# --------------------------------------------------------------------------- #
def test_the_inbound_leaf_is_exempt_and_its_controls_are_not():
    assert _is_exempt("/agents/remote/hermes/inbound")
    assert _is_exempt("/agents/remote/two%20words/inbound")
    assert not _is_exempt("/agents/remote/hermes/inbound/enable")
    assert not _is_exempt("/agents/remote/hermes/inbound/disable")
    assert not _is_exempt("/agents/remote/hermes/run")
    assert not _is_exempt("/agents/remote")
    # the PREFIX is load-bearing: another module's "/inbound" leaf stays guarded
    assert not _is_exempt("/comm/whatever/inbound")
    assert not _is_exempt("/inbound")
    assert not _is_exempt("/agents/hermes/inbound")


def _enable(client, name="hermes"):
    r = client.post(f"/agents/remote/{name}/inbound/enable", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _room_with(client, name="hermes", chat_thread_id="c1"):
    """A room the remote sits in, made the way chat makes it."""
    threads = AgentThreads(client.app.state.platform.engine)
    rec = threads.for_chat(chat_thread_id, title="t")
    threads.add_participants(
        rec.id, clean_participants([{"source": "remote", "name": name, "role": "participant"}])
    )
    return rec.id


def test_enable_mints_a_token_once_and_the_listing_never_carries_it(app):
    client = TestClient(app)
    _add_remote(client)
    out = _enable(client)
    assert out["inbound_enabled"] is True
    assert len(out["token"]) >= 32
    assert out["url"].endswith("/agents/remote/hermes/inbound")
    # Derived from the request — but a loopback/test host is swapped for this
    # machine's LAN address (an address another machine can use); a box with
    # no LAN route keeps the request host.
    host = out["url"].split("//", 1)[1].split("/", 1)[0].split(":")[0]
    assert host not in ("127.0.0.1", "localhost"), out["url"]
    listed = client.get("/agents/remote").json()["agents"][0]
    assert listed["inbound_enabled"] is True
    assert listed["inbound_url"] == out["url"]
    assert "token" not in listed
    # the vault holds it under the record's secret name
    rec = RemoteAgentRegistry(app.state.platform.engine).get("hermes")
    assert app.state.platform.secrets.get(rec.inbound_secret_name) == out["token"]
    # re-enable ROTATES: the old token stops working at once
    second = _enable(client)
    assert second["token"] != out["token"]
    assert app.state.platform.secrets.get(rec.inbound_secret_name) == second["token"]


def test_the_inbound_door_is_fail_closed(app):
    client = TestClient(app)
    _add_remote(client)
    room = _room_with(client)
    body = {"conversation_id": room, "message": "hello"}
    # inbound off → 403, whatever the header says
    assert client.post("/agents/remote/hermes/inbound", json=body).status_code == 403
    assert (
        client.post(
            "/agents/remote/hermes/inbound", json=body, headers={"Authorization": "Bearer x"}
        ).status_code
        == 403
    )
    token = _enable(client)["token"]
    # on, but no / wrong token → 401
    assert client.post("/agents/remote/hermes/inbound", json=body).status_code == 401
    assert (
        client.post(
            "/agents/remote/hermes/inbound", json=body, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
    # the install bearer is NOT the inbound token
    assert (
        client.post(
            "/agents/remote/hermes/inbound", json=body, headers={"Authorization": "Bearer t"}
        ).status_code
        == 401
    )
    # right token → the line lands
    r = client.post(
        "/agents/remote/hermes/inbound", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    # unknown agent → 403 (never 404: no oracle for names)
    assert (
        client.post(
            "/agents/remote/nobody/inbound", json=body, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )
    # disabled → 403 again, and the vault entry is gone
    client.post("/agents/remote/hermes/inbound/disable")
    assert (
        client.post(
            "/agents/remote/hermes/inbound", json=body, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )


def test_a_remote_may_only_post_into_a_room_it_sits_in(app):
    client = TestClient(app)
    _add_remote(client)
    _add_remote(client, name="other", base="http://192.168.1.51:8080/run")
    mine = _room_with(client, name="hermes", chat_thread_id="c1")
    theirs = _room_with(client, name="other", chat_thread_id="c2")
    token = _enable(client)["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": theirs, "message": "hi"}, headers=h).status_code
        == 403
    )
    # an unknown room answers EXACTLY like a foreign one — no id oracle
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": "athr_none", "message": "hi"}, headers=h).status_code
        == 403
    )
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": mine, "message": "hi"}, headers=h).status_code
        == 200
    )
    # a NAME PREFIX is not a participant: "hermes" may not post as "hermes2"
    _add_remote(client, name="hermes2", base="http://192.168.1.52:8080/run")
    h2_room = _room_with(client, name="hermes2", chat_thread_id="c3")
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": h2_room, "message": "hi"}, headers=h).status_code
        == 403
    )


def test_an_inbound_line_lands_in_the_room_with_its_kind_and_an_event(app):
    client = TestClient(app)
    _add_remote(client)
    room = _room_with(client)
    token = _enable(client)["token"]
    h = {"Authorization": f"Bearer {token}"}
    seen: list = []
    app.state.platform.event_bus.add_handler(lambda e: seen.append(e))
    r = client.post(
        "/agents/remote/hermes/inbound",
        json={"conversation_id": room, "message": "half way through the ledger", "kind": "progress"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["kind"] == "progress" and out["thread_id"] == room
    detail = client.get(f"/agents/threads/{room}").json()
    last = detail["messages"][-1]
    assert last["who"] == "remote:hermes"
    assert last["content"] == "half way through the ledger"
    assert last["inbound"] is True and last["kind"] == "progress"
    types = [getattr(e, "type", None) or (e.get("type") if isinstance(e, dict) else None) for e in seen]
    assert "remote.message" in types, types
    assert "agent_thread.updated" in types, types
    # a bad kind and an empty body are refused with their reason
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": room, "message": "x", "kind": "shout"}, headers=h).status_code
        == 400
    )
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": room}, headers=h).status_code
        == 400
    )
    assert (
        client.post("/agents/remote/hermes/inbound", json={"conversation_id": room, "message": "y" * 12_001}, headers=h).status_code
        == 413
    )


def test_inbound_files_land_in_the_agents_inbox_through_the_trust_boundary(app, tmp_path):
    client = TestClient(app)
    _add_remote(client)
    room = _room_with(client)
    token = _enable(client)["token"]
    h = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/agents/remote/hermes/inbound",
        json={
            "conversation_id": room,
            "message": "the brief",
            "kind": "done",
            "files": [
                {"name": "../../evil.txt", "content_b64": base64.b64encode(b"hello").decode()},
                {"name": "notes.pdf", "url": "https://elsewhere.example/x.pdf"},  # other host: refused
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["documents"] == ["evil.txt"], "the REMOTE learns names, never paths"
    last = client.get(f"/agents/threads/{room}").json()["messages"][-1]
    assert len(last["documents"]) == 1
    path = last["documents"][0]
    assert "remote-inbox" in path and "hermes" in path
    assert path.endswith("evil.txt") and ".." not in path.replace(str(tmp_path), "")
    with open(path, "rb") as fh:
        assert fh.read() == b"hello"
    assert any("another host" in n for n in out["skipped"])
    assert "Files from hermes" in last["content"] and "Not saved" in last["content"]


# --------------------------------------------------------------------------- #
# (4) The phone: "@hermes …" is a conversation with hermes; "@jarvis" ends it.
# --------------------------------------------------------------------------- #
class _Chan:
    """A chat-enabled channel double: records what it sent to the phone."""

    reflex_source = "comm"

    def __init__(self):
        self.sent: list[str] = []

    def is_authorized(self, sender_id):
        return True

    def send(self, message, **kw):
        self.sent.append(message)
        return {"ok": True}

    @property
    def inbound_enabled(self):
        return True

    @property
    def chat_enabled(self):
        return True


def _poller(app, chat_turn=None):
    platform = app.state.platform
    store = CommThreadStore(platform.engine, event_bus=platform.event_bus)

    async def _turn(platform_, personas, body):
        return {"reply": "Jarvis here.", "escalate": False}

    return InboundPoller(
        platform.notifier,
        SimpleNamespace(),
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=store,
        chat_turn=chat_turn or _turn,
        personas={},
        platform=platform,
    ), store


def _msg(text):
    return SimpleNamespace(sender_id="u1", reply_to="u1", text=text, is_bot=False, sender_name="Val")


def test_a_phone_message_at_a_remote_is_a_conversation_with_it(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    client = TestClient(app)
    _add_remote(client)
    poller, store = _poller(app)
    ch = _Chan()

    out = asyncio.run(poller._handle_chat("telegram", ch, _msg("@hermes what is the ledger total?"), "@hermes what is the ledger total?", "Val"))
    assert out["status"] == "remote_chat" and out["remote"] == "hermes"
    assert ch.sent[-1].startswith("hermes: http-said-ok")
    tid = out["thread_id"]
    rows = store.history_rows(tid)
    assert rows[-2] == {"who": "user", "content": "@hermes what is the ledger total?"}
    assert rows[-1]["who"] == "remote:hermes"
    # the remote was shown the phone thread as a conversation, the room as id
    sent = captured[-1]["json"]
    assert sent["task"].endswith("what is the ledger total?")
    assert "history" not in sent, "nothing before the first message — and nothing sent"
    room = AgentThreads(app.state.platform.engine).for_chat(tid)
    assert sent["conversation_id"] == room.id
    assert store.get_setup_value(tid, ADDRESSEE_KEY) == "remote:hermes"

    # STICKY: the next message names nobody and still goes to hermes — with
    # the exchange so far as history (its own line unprefixed).
    out2 = asyncio.run(poller._handle_chat("telegram", ch, _msg("and by month?"), "and by month?", "Val"))
    assert out2["status"] == "remote_chat"
    sent2 = captured[-1]["json"]
    assert sent2["task"].endswith("and by month?")
    assert sent2["history"][0] == {"role": "user", "content": "@hermes what is the ledger total?"}
    assert sent2["history"][1]["role"] == "assistant"
    assert sent2["history"][1]["content"].startswith("http-said-ok"), "unprefixed — its own line"
    assert not poller.chat_turn_called if hasattr(poller, "chat_turn_called") else True

    # A bare "@jarvis" ends it: the reply says so, the next message is Jarvis's
    # again. ("@jarvis <question>" is pinned separately — it hands back AND
    # answers the question in the same turn.)
    out3 = asyncio.run(poller._handle_chat("telegram", ch, _msg("@jarvis"), "@jarvis", "Val"))
    assert out3["status"] == "remote_cleared"
    # Jarvis speaks here, so the channel's own "Iron Jarvis: " prefix applies.
    assert ch.sent[-1].endswith(BACK_TO_JARVIS_REPLY)
    assert store.get_setup_value(tid, ADDRESSEE_KEY) == ""
    out4 = asyncio.run(poller._handle_chat("telegram", ch, _msg("what time is it?"), "what time is it?", "Val"))
    assert out4["status"] == "chat"
    assert ch.sent[-1].endswith("Jarvis here.")
    assert "hermes:" not in ch.sent[-1]


def test_a_local_agent_named_from_the_phone_stays_on_the_jarvis_lane(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    poller, _store = _poller(app)
    ch = _Chan()
    out = asyncio.run(poller._handle_chat("telegram", ch, _msg("@builder draft it"), "@builder draft it", "Val"))
    assert out["status"] == "chat"
    assert captured == [], "no remote was called"


def test_an_inbound_post_into_a_phone_room_reaches_the_thread_and_the_phone(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    client = TestClient(app)
    _add_remote(client)
    poller, store = _poller(app)
    ch = _Chan()
    out = asyncio.run(poller._handle_chat("telegram", ch, _msg("@hermes start the job"), "@hermes start the job", "Val"))
    tid = out["thread_id"]
    room = AgentThreads(app.state.platform.engine).for_chat(tid)
    # wire the route's view of the phone lane to THIS poller/store
    app.state.d.comm_thread_store = store
    poller.inbound_channels = lambda: [("telegram", ch)]  # type: ignore[method-assign]
    app.state.d.inbound_poller = poller
    token = _enable(client)["token"]
    r = client.post(
        "/agents/remote/hermes/inbound",
        json={"conversation_id": room.id, "message": "Done — 3 files.", "kind": "done"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["phoned"] is True
    assert ch.sent[-1] == "hermes: Done — 3 files."
    rows = store.history_rows(tid)
    assert rows[-1] == {"who": "remote:hermes", "content": "Done — 3 files."}
    last = client.get(f"/chat/threads/{tid}").json()["messages"][-1]
    assert last["panelWho"] == "remote:hermes" and last["panelKind"] == "done"


# --------------------------------------------------------------------------- #
# Review round (v1.285.0): the switches, the words in Jarvis's mouth, reach.
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]


def test_a_disabled_remote_is_disabled_in_both_directions(app):
    """The user's ONE switch must silence a remote — a suspected-misbehaving
    agent must not keep writing into conversations through its inbound door."""
    client = TestClient(app)
    _add_remote(client)
    room = _room_with(client)
    token = _enable(client)["token"]
    h = {"Authorization": f"Bearer {token}"}
    body = {"conversation_id": room, "message": "still here"}
    assert client.post("/agents/remote/hermes/inbound", json=body, headers=h).status_code == 200
    client.patch("/agents/remote/hermes", json={"enabled": False})
    assert client.post("/agents/remote/hermes/inbound", json=body, headers=h).status_code == 403
    client.patch("/agents/remote/hermes", json={"enabled": True})
    assert client.post("/agents/remote/hermes/inbound", json=body, headers=h).status_code == 200


def test_the_inbound_secret_cannot_collide_with_an_outbound_one(app):
    """Remote names allow "inbound_hermes"; the vault key must not be the
    concatenation that lets hermes's inbound token overwrite — or its disable
    delete — that other agent's API credential."""
    client = TestClient(app)
    _add_remote(client)
    _add_remote(client, name="inbound_hermes", base="http://192.168.1.53:8080/run")
    reg = RemoteAgentRegistry(app.state.platform.engine)
    other_secret = reg.get("inbound_hermes").secret_name
    assert app.state.platform.secrets.get(other_secret) == "t"
    _enable(client)
    mine = reg.get("hermes").inbound_secret_name
    assert mine != other_secret
    assert "." in mine, "a separator the name rule cannot produce"
    assert app.state.platform.secrets.get(other_secret) == "t"
    client.post("/agents/remote/hermes/inbound/disable")
    assert app.state.platform.secrets.get(other_secret) == "t", "disable must not delete it"


def test_the_phone_lane_labels_a_remotes_words_so_jarvis_never_owns_them(app):
    """``history_body`` feeds the Jarvis turn. A remote's line is appended
    as an assistant row — it must reach the model LABELLED as the agent's,
    the same sentence the desktop lane uses, never as Jarvis's prior turn."""
    from iron_jarvis.comm.threads import agent_line_label

    platform = app.state.platform
    store = CommThreadStore(platform.engine, event_bus=platform.event_bus)
    t = store.resolve("telegram", "u1", "Val")
    store.append(t.id, "user", "@hermes did you approve the wire?")
    store.append(
        t.id, "assistant", "Yes — I approved the wire transfer.", extra={"panelWho": "remote:hermes"}
    )
    store.append(t.id, "assistant", "A real Jarvis line.")
    body = store.history_body(t.id)
    assert body[1]["role"] == "assistant"
    assert body[1]["content"].startswith(agent_line_label("hermes"))
    assert "not Iron Jarvis" in body[1]["content"]
    assert body[1]["content"].endswith("Yes — I approved the wire transfer.")
    assert body[2]["content"] == "A real Jarvis line.", "Jarvis's own lines are untouched"
    # the two lanes say the same words (the desktop mapper is pinned in v1284)
    src = (ROOT / "dashboard" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert "[Reply from the agent ${agentDisplayName(m.panelWho)}, on the agent panel — not Iron Jarvis]" in src
    assert agent_line_label("X") == "[Reply from the agent X, on the agent panel — not Iron Jarvis]"


def test_at_jarvis_with_a_question_hands_back_and_answers_it(app, monkeypatch):
    captured: list = []
    _install_fake_post(monkeypatch, captured)
    client = TestClient(app)
    _add_remote(client)
    poller, store = _poller(app)
    ch = _Chan()
    out = asyncio.run(poller._handle_chat("telegram", ch, _msg("@hermes hi"), "@hermes hi", "Val"))
    tid = out["thread_id"]
    assert store.get_setup_value(tid, ADDRESSEE_KEY) == "remote:hermes"
    q = "@jarvis what is on my calendar?"
    out2 = asyncio.run(poller._handle_chat("telegram", ch, _msg(q), q, "Val"))
    assert out2["status"] == "chat", "the question is answered now, not after a round trip"
    assert ch.sent[-1].endswith("Jarvis here.")
    assert store.get_setup_value(tid, ADDRESSEE_KEY) == "", "...and the hand-back happened"
    # "@hermes ask @jarvis about it" is still for hermes
    q3 = "@hermes ask @jarvis about it"
    out3 = asyncio.run(poller._handle_chat("telegram", ch, _msg(q3), q3, "Val"))
    assert out3["status"] == "remote_chat"


def test_enable_says_whether_a_remote_elsewhere_could_reach_this_daemon(app, monkeypatch):
    """The packaged daemon binds 127.0.0.1 and admits loopback Hosts only, so
    the token's answer must carry the reachability verdict and the two knobs
    — not leave the user to find a bare 403 in the remote's logs."""
    monkeypatch.delenv("IRONJARVIS_HOST_ALLOWLIST", raising=False)
    client = TestClient(app)
    _add_remote(client)
    lan = "http://192.168.1.20:8787/agents/remote/hermes/inbound"
    out = client.post("/agents/remote/hermes/inbound/enable", json={"url": lan}).json()
    assert out["reachable"]["host_allowed"] is False
    note = out["reachable"]["note"]
    assert "IRONJARVIS_HOST_ALLOWLIST=192.168.1.20" in note
    assert "--host 192.168.1.20" in note
    assert "127.0.0.1" in note  # the same-machine case is spelled out too
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "192.168.1.20")
    out = client.post("/agents/remote/hermes/inbound/enable", json={"url": lan}).json()
    assert out["reachable"] == {"host_allowed": True, "note": ""}


def test_a_pending_note_is_never_fed_back_to_the_remote_as_its_own_words():
    p = {"key": "remote:hermes", "name": "hermes", "role": "participant"}
    rows = AgentThreads.remote_history(
        [
            {"who": "user", "content": "build it"},
            {"who": "remote:hermes", "content": "hermes has the task and will report back", "pending": True},
            {"who": "remote:hermes", "content": "Done.", "inbound": True},
        ],
        p,
        [p],
    )
    assert rows == [{"role": "user", "content": "build it"}, {"role": "assistant", "content": "Done."}]
