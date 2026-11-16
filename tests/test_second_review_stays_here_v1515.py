"""Second review, group 15 part D (v1.515.0): four places where the app said
or assumed "this stays here" without checking.

* L1. Both phone doors ran the plain chat lane without the chat's id: the
  turn's usage row had no chat, so the chat's own usage total
  (``GET /chat/threads/{id}/usage``) stayed at zero, and its notes were
  recalled under "chat", the key every other phone conversation (and every
  unsaved chat) shares. Driven through the REAL inbound pipeline and the REAL
  plain lane (``run_chat_turn``) with a scripted model.
* L2. A reply typed at the desk into a phone conversation ran with no
  project. (The trust half is left to the lead: ``docs/rules/tools.md`` says
  the desk door stays full trust on purpose.)
* H12. Pi's or OpenCode's ``ollama/...`` model read "on this network" from
  Iron Jarvis's OWN Ollama address; the tool's own address is not read.
* The Settings "your own model server" slot (``custom_base_url`` and its
  key) refuses what the Model servers page refuses (a name, password, ``?``
  or ``#`` in the address; a key that would travel over plain http outside
  this network), with the same sentences, from the Settings page, from chat
  and from the key doors.

Names, addresses and keys here are made up.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import InboundMessage, MockChannel, Notifier
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, ChatThreadRecord, Project
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.fleet import (
    ADDRESS_HAS_A_NAME,
    ADDRESS_HAS_MORE,
    KEY_WOULD_TRAVEL_UNENCRYPTED,
)
from iron_jarvis.hosted import panes as PN
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

NOTE_A = "the garden shed key hangs behind the blue door"
NOTE_SHARED = "the garden shed key sits under the green mat"
ASK = "where is the garden shed key?"


class _PhoneChannel(MockChannel):
    supports_inbound = True

    def has_credentials(self) -> bool:
        return True


CHAT_CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777", "888"]}


def _script(platform: Any, seen: list[dict[str, Any]], reply: str = "Noted.") -> None:
    """Every model call answers ``reply``, with token use, and is recorded."""

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class=None, **_kw):
        names = []
        for t in tools or []:
            names.append(str(getattr(t, "name", "") or (t.get("name") if isinstance(t, dict) else "") or ""))
        seen.append({"system": system, "tools": names})
        return RouteResult(
            LLMResponse(text=reply, tool_calls=[], usage={"input_tokens": 12, "output_tokens": 7}),
            "mock", "mock",
        )

    platform.router.complete = fake_complete


def _real_turn(calls: list[tuple[Any, dict[str, Any], dict[str, Any]]]):
    """The REAL plain lane, passed through so a test can read what it was
    given and what it answered (it takes ``**kw``, so the door hands it the
    trust posture exactly as it hands the real one)."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    async def turn(platform, personas, body, **kw):
        out = await run_chat_turn(platform, personas, body, **kw)
        calls.append((body, kw, out))
        return out

    return turn


def _poller(app: Any, ch: Any, turn: Any) -> InboundPoller:
    platform = app.state.platform
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    return InboundPoller(
        notifier,
        Orchestrator(platform),
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=CommThreadStore(platform.engine),
        chat_turn=turn,
        personas=app.state.inbound_poller.personas,
        platform=platform,
    )


def _msg(text: str, sender: str, update_id: int) -> InboundMessage:
    return InboundMessage(sender_id=sender, text=text, update_id=update_id, reply_to=sender)


async def _write_note(platform: Any, tmp_path: Any, thread_id: str, text: str) -> None:
    """``memory_write`` through the real registry, as a chat turn in
    ``thread_id`` (``""`` = an unsaved chat, the key every phone turn used)."""
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.workmap.clients import TurnWall, wall

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="run-chat", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine, thread_id=thread_id)
    with wall.turn_scope(TurnWall()):
        r = await platform.registry.invoke(
            "memory_write", {"layer": "session", "key": f"shed-{thread_id or 'unsaved'}", "text": text},
            ctx, platform.permissions, allowed_names={"memory_write"},
        )
    assert r.ok, r.error


# --------------------------------------------------------------------------- #
# L1: a phone turn knows its chat
# --------------------------------------------------------------------------- #
async def test_a_phone_turn_is_billed_to_its_own_chat_and_saved_once(tmp_path):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    seen: list[dict[str, Any]] = []
    _script(platform, seen, reply="It is on the shelf.")
    calls: list = []
    ch = _PhoneChannel(dict(CHAT_CFG))
    poller = _poller(app, ch, _real_turn(calls))
    tid = poller.thread_store.resolve("tg", "777", "Val").id

    res = await poller._handle("tg", ch, _msg("where did I leave the ladder?", "777", 1))

    assert res["status"] == "chat", res
    body, kw, _out = calls[-1]
    assert body.thread_id == tid  # the lane is told which chat it answers
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(AgentRun).where(AgentRun.session_id == "chat")))
    assert rows and rows[-1].thread_id == tid, [r.thread_id for r in rows]
    usage = TestClient(app).get(f"/chat/threads/{tid}/usage").json()
    assert usage["turns"] == 1 and usage["input_tokens"] > 0, usage
    # The phone door saves the conversation itself: the reply is there ONCE.
    assert poller.thread_store.history_body(tid) == [
        {"role": "user", "content": "where did I leave the ladder?"},
        {"role": "assistant", "content": "It is on the shelf."},
    ]
    # Nothing is left for the chat page to wait on: the chat reads running
    # only while the turn runs, and no page-owned turn record was made.
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.core.models import ChatTurnRecord

    assert tid not in TURNS.thread_states()
    with session_scope(platform.engine) as db:
        assert list(db.exec(select(ChatTurnRecord))) == []


async def test_two_phone_conversations_keep_their_own_notes(tmp_path):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    seen: list[dict[str, Any]] = []
    _script(platform, seen)
    calls: list = []
    ch = _PhoneChannel(dict(CHAT_CFG))
    poller = _poller(app, ch, _real_turn(calls))
    tid_a = poller.thread_store.resolve("tg", "777", "Val").id
    tid_b = poller.thread_store.resolve("tg", "888", "Sam").id
    assert tid_a != tid_b
    # A note kept in conversation A, and one kept under the shared key that
    # every phone conversation (and every unsaved chat) was grounded with.
    await _write_note(platform, tmp_path, tid_a, NOTE_A)
    await _write_note(platform, tmp_path, "", NOTE_SHARED)

    await poller._handle("tg", ch, _msg(ASK, "888", 1))
    b_system = seen[-1]["system"]
    await poller._handle("tg", ch, _msg(ASK, "777", 2))
    a_system = seen[-1]["system"]

    assert NOTE_A not in b_system  # B is never told A's note
    assert NOTE_SHARED not in b_system
    assert NOTE_A in a_system  # A recalls its own
    assert NOTE_SHARED not in a_system
    # A phone turn is low trust by default: the memory writers are never
    # offered to it, so a note can only reach a phone conversation's key
    # through a full-trust door; the recall key above is what keeps them apart.
    assert all(kw.get("trust") == "low" for _b, kw, _o in calls)
    assert not any(set(s["tools"]) & set(LOW_TRUST_DENY) for s in seen)
    assert {"memory_write", "remember_preference"} <= set(LOW_TRUST_DENY)


# --------------------------------------------------------------------------- #
# L2: a desk reply into a phone conversation keeps its project
# --------------------------------------------------------------------------- #
def test_a_desk_reply_into_a_phone_conversation_keeps_its_project_and_chat(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        app = client.app
        platform = app.state.platform
        seen: list[dict[str, Any]] = []
        _script(platform, seen, reply="Desk reply.")
        calls: list = []
        ch = _PhoneChannel(dict(CHAT_CFG))
        platform.notifier.add_channel("tg", ch)
        app.state.inbound_poller.chat_turn = _real_turn(calls)
        store = app.state.comm_thread_store
        tid = store.resolve("tg", "777", "Val").id
        with session_scope(platform.engine) as db:
            proj = Project(name="Garden", instructions="PROJECT-MARKER: answer in one line.")
            db.add(proj)
            db.commit()
            pid = proj.id
            rec = db.get(ChatThreadRecord, tid)
            rec.project_id = pid
            db.add(rec)
            db.commit()

        r = client.post(f"/comm/threads/{tid}/send", json={"text": "and the hose?"})

        assert r.status_code == 200, r.text
        body, _kw, _out = calls[-1]
        assert body.project_id == pid  # the same project the phone door passes
        assert body.thread_id == tid
        assert "PROJECT-MARKER" in seen[-1]["system"]
        assert store.history_body(tid)[-2:] == [
            {"role": "user", "content": "and the hose?"},
            {"role": "assistant", "content": "Desk reply."},
        ]


# --------------------------------------------------------------------------- #
# H12: "on this network" is never judged from Iron Jarvis's own address
# --------------------------------------------------------------------------- #
def _row(harness: str, model: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(folder="", harness=harness, model=model)


def test_a_tools_own_ollama_is_not_known_even_when_iron_jarviss_is_local():
    here = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url="http://127.0.0.1:11434"))
    assert PN.where_view(_row("pi", "ollama/llama3"), here) == {
        "on_this_network": None, "words": "Iron Jarvis cannot tell where Pi sends this model.",
    }
    assert PN.where_view(_row("opencode", "ollama/qwen3"), here) == {
        "on_this_network": None, "words": "Iron Jarvis cannot tell where OpenCode sends this model.",
    }
    # Iron Jarvis's own Ollama away from this network says nothing either.
    away = types.SimpleNamespace(providers=None, config=types.SimpleNamespace(ollama_base_url="https://ollama.example.com"))
    assert PN.where_view(_row("pi", "ollama/llama3"), away)["on_this_network"] is None
    # A cloud prefix is still the cloud.
    assert PN.where_view(_row("pi", "anthropic/claude-sonnet-5"), here) == {"on_this_network": False, "words": "cloud"}


# --------------------------------------------------------------------------- #
# The Settings "your own model server" slot
# --------------------------------------------------------------------------- #
PUBLIC_HTTP = "http://models.example.com/v1"
PRIVATE_HTTP = "http://192.168.1.20:1234/v1"
PUBLIC_HTTPS = "https://models.example.com/v1"


def _put(client: TestClient, values: dict[str, Any]):
    return client.put("/settings/values", json={"values": values})


def test_the_slot_refuses_a_name_password_query_or_fragment(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        r = _put(client, {"custom_base_url": "http://user:pass@192.168.1.20:1234/v1"})
        assert r.status_code == 400 and ADDRESS_HAS_A_NAME in r.json()["detail"], r.text
        r = _put(client, {"custom_base_url": "http://192.168.1.20:1234/v1?next=1"})
        assert r.status_code == 400 and ADDRESS_HAS_MORE in r.json()["detail"], r.text
        assert not (client.app.state.platform.config.custom_base_url or "")
        # A plain address on this network is saved as before.
        assert _put(client, {"custom_base_url": PRIVATE_HTTP}).status_code == 200


def test_a_key_for_the_slot_is_refused_over_plain_http_outside_this_network(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        assert _put(client, {"custom_base_url": PUBLIC_HTTP}).status_code == 200
        r = client.post("/connections/custom/key", json={"key": "test-key-value"})
        assert r.status_code == 400 and r.json()["detail"] == KEY_WOULD_TRAVEL_UNENCRYPTED, r.text
        assert not platform.connections.has_credential("custom")
        # Chat's secure card goes through the same refusal.
        from iron_jarvis.settings.credentials import CredentialError, CredentialStore

        with pytest.raises(CredentialError) as exc:
            CredentialStore(types.SimpleNamespace(platform=platform)).store("connection.custom", "test-key-value")
        assert str(exc.value) == KEY_WOULD_TRAVEL_UNENCRYPTED
        assert not platform.connections.has_credential("custom")
        # On this network, or over https, the key is saved.
        assert _put(client, {"custom_base_url": PRIVATE_HTTP}).status_code == 200
        assert client.post("/connections/custom/key", json={"key": "test-key-value"}).status_code == 200
        assert _put(client, {"custom_base_url": PUBLIC_HTTPS}).status_code == 200
        assert client.post("/connections/custom/key", json={"key": "test-key-value"}).status_code == 200
        assert platform.connections.has_credential("custom")


def test_a_keyed_slot_cannot_be_moved_to_plain_http_outside_this_network(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        assert _put(client, {"custom_base_url": PUBLIC_HTTPS}).status_code == 200
        assert client.post("/connections/custom/key", json={"key": "test-key-value"}).status_code == 200
        r = _put(client, {"custom_base_url": PUBLIC_HTTP})
        assert r.status_code == 400 and KEY_WOULD_TRAVEL_UNENCRYPTED in r.json()["detail"], r.text
        assert platform.config.custom_base_url == PUBLIC_HTTPS
        assert _put(client, {"custom_base_url": PRIVATE_HTTP}).status_code == 200


async def test_chats_setting_tool_gets_the_same_refusal(tmp_path):
    from iron_jarvis.tools.base import ToolContext

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    tool = platform.registry.get("config_change")
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="run-chat", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    out = await tool.execute({"key": "custom_base_url", "value": "http://user:pass@192.168.1.20:1234/v1"}, ctx)
    assert not out.ok and ADDRESS_HAS_A_NAME in (out.error or ""), out
    assert not (platform.config.custom_base_url or "")


def test_an_address_already_saved_is_not_judged_again(tmp_path):
    """A slot saved before this rule keeps working: start-up does not judge
    it, and a Settings save that sends the same value back is not refused."""
    from iron_jarvis.core.config import persist_config_values

    home = tmp_path / "home"
    app = create_app(str(home))
    persist_config_values(app.state.platform.config.home, {"custom_base_url": "http://user:pass@192.168.1.20:1234/v1"})
    with TestClient(create_app(str(home))) as client:
        cfg = client.app.state.platform.config
        assert cfg.custom_base_url == "http://user:pass@192.168.1.20:1234/v1"
        r = _put(client, {"custom_base_url": cfg.custom_base_url, "custom_model": "qwen3"})
        assert r.status_code == 200, r.text


async def test_fetch_available_models_never_sends_a_key_over_plain_http_outside_this_network(monkeypatch):
    """Lead review: the "Fetch available models" door (also chat's way of
    adding a model server) sends the typed or saved key in its request."""
    from types import SimpleNamespace

    import pytest as _pytest
    from fastapi import HTTPException

    from iron_jarvis.daemon.routes import connections as C
    from iron_jarvis.daemon.routes.fleet import KEY_WOULD_TRAVEL_UNENCRYPTED

    sent: list[tuple] = []
    monkeypatch.setattr(C, "_saved_endpoint_auth", lambda d, url, key: (key, None))
    monkeypatch.setattr(C, "_probe_endpoint_models", lambda url, key, **kw: sent.append((url, key)) or {"models": []})
    d = SimpleNamespace()
    with _pytest.raises(HTTPException) as refused:
        await C.fetch_endpoint_models(d, "http://gateway.example.com:4000/v1", api_key="test-key-value")
    assert refused.value.status_code == 400 and refused.value.detail == KEY_WOULD_TRAVEL_UNENCRYPTED
    assert sent == [], "the key was sent"
    # https anywhere, plain http on this network, and no key at all: as before
    await C.fetch_endpoint_models(d, "https://gateway.example.com/v1", api_key="test-key-value")
    await C.fetch_endpoint_models(d, "http://192.168.1.20:11434/v1", api_key="test-key-value")
    await C.fetch_endpoint_models(d, "http://gateway.example.com:4000/v1")
    assert [u for u, _ in sent] == [
        "https://gateway.example.com/v1", "http://192.168.1.20:11434/v1", "http://gateway.example.com:4000/v1",
    ]
