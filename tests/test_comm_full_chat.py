"""Offline tests for FULL CHAT over comm (v1.136.0 messaging surfaces, Pair P).

A chat-enabled destination runs real conversational turns on a durable
daemon-owned thread: continuity, /new, command exchanges on the thread,
escalate ack + session summary, honest errors, the per-identity rate cap,
chunked replies, and the desktop reply fan-out endpoint. The turn service is
INJECTED (a fake async callable) — no model calls, no network. With chat OFF
the legacy one-shot behavior is pinned byte-equivalent.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import InboundMessage, MockChannel, Notifier
from iron_jarvis.comm.base import Channel, split_message
from iron_jarvis.comm.channels import TelegramChannel
from iron_jarvis.comm.inbound import (
    ESCALATE_ACK,
    NEW_THREAD_REPLY,
    RATE_LIMIT_REPLY,
    RATE_MAX_TURNS,
    InboundPoller,
)
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class ChatMockChannel(MockChannel):
    """MockChannel with a receive leg + credentials, for full-chat tests."""

    supports_inbound = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.updates: list[InboundMessage] = []

    def has_credentials(self) -> bool:  # no token needed offline
        return True

    def poll(self, offset: int = 0, *, timeout: int = 0):
        msgs = [
            m for m in self.updates if m.update_id is None or m.update_id >= offset
        ]
        nxt = offset
        for m in msgs:
            if isinstance(m.update_id, int):
                nxt = max(nxt, m.update_id + 1)
        return msgs, nxt


CHAT_CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}


def _msg(text: str, sender: str = "777", update_id: int = 1) -> InboundMessage:
    return InboundMessage(
        sender_id=str(sender), text=text, update_id=update_id, reply_to=sender
    )


def _fake_turn(reply: str = "All good.", **extra: Any):
    """A fake chat-turn service recording every ChatBody it was called with."""

    async def turn(platform, personas, body) -> dict[str, Any]:
        turn.calls.append(body)
        return {
            "reply": reply,
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": False,
            "escalate_reason": "",
            **extra,
        }

    turn.calls = []
    return turn


def _chat_poller(
    platform,
    ch,
    turn,
    *,
    clock=None,
    command_interpreter=None,
    reflex_router=None,
):
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    orch = Orchestrator(platform)
    store = CommThreadStore(platform.engine)
    poller = InboundPoller(
        notifier,
        orch,
        platform.engine,
        event_bus=platform.event_bus,
        command_interpreter=command_interpreter,
        reflex_router=reflex_router,
        thread_store=store,
        chat_turn=turn,
        personas={},
        platform=platform,
        clock=clock,
    )
    return poller, orch, store


# --------------------------------------------------------------------------- #
# split_message — the chunker every reply sender uses
# --------------------------------------------------------------------------- #
def test_split_message_short_and_exact_limit_stay_whole():
    assert split_message("", 10) == [""]
    assert split_message("hello", 10) == ["hello"]
    assert split_message("a" * 10, 10) == ["a" * 10]  # exact limit: one chunk


def test_split_message_prefers_paragraph_then_newline_boundary():
    assert split_message("aaa\n\nbbbb", 6) == ["aaa", "bbbb"]
    assert split_message("aaa\nbbbb", 6) == ["aaa", "bbbb"]


def test_split_message_never_cuts_mid_word_when_avoidable():
    assert split_message("alpha beta gamma delta", 10) == [
        "alpha beta",
        "gamma",
        "delta",
    ]


def test_split_message_hard_cuts_unbroken_runs():
    assert split_message("a" * 10, 4) == ["aaaa", "aaaa", "aa"]


def test_split_message_respects_limit_and_loses_no_content():
    text = ("word " * 50 + "\n\n") * 3 + "x" * 500
    chunks = split_message(text, 80)
    assert all(len(c) <= 80 for c in chunks)
    # Whitespace-insensitive equality: only separators are consumed by cuts.
    assert "".join(text.split()) == "".join("".join(c.split()) for c in chunks)


def test_chunk_limits_metadata():
    assert Channel.chunk_limit == 3500
    assert TelegramChannel.chunk_limit == 4096


def test_split_message_limit_one_pathological_terminates():
    # Worst case: one unbroken 1000-char word at limit 1 — must terminate,
    # every chunk within the limit, and reconstruct EXACTLY (hard cuts
    # consume no separators, so plain concatenation is the invariant).
    chunks = split_message("x" * 1000, 1)
    assert len(chunks) == 1000
    assert all(len(c) == 1 for c in chunks)
    assert "".join(chunks) == "x" * 1000


# --------------------------------------------------------------------------- #
# chat_enabled gate — chat implies inbound, fail-closed
# --------------------------------------------------------------------------- #
def test_chat_enabled_requires_inbound_opt_in():
    assert ChatMockChannel({"chat_enabled": True}).chat_enabled() is False
    assert ChatMockChannel(dict(CHAT_CFG)).chat_enabled() is True
    # An outbound-only type can never chat, whatever its config claims.
    assert MockChannel({"chat_enabled": True, "inbound_enabled": True}).chat_enabled() is False


# --------------------------------------------------------------------------- #
# security probe: an unauthorized sender reaches NOTHING chat-side
# --------------------------------------------------------------------------- #
async def test_unauthorized_sender_never_reaches_chat_state(platform):
    """Direct _handle probe on a CHAT-enabled channel: a sender off the
    allowlist must not touch the turn service, the thread store, the rate
    counter, the orchestrator — and must hear nothing back."""
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn()
    poller, orch, _store = _chat_poller(platform, ch, turn)

    res = await poller._handle("tg", ch, _msg("open the vault", sender="999"))

    assert res == {"channel": "tg", "status": "unauthorized", "sender": "999"}
    assert turn.calls == []  # the chat path never ran
    assert ch.sent == []  # no reply leaks to the stranger
    assert poller._turn_times == {}  # rate counter untouched
    assert orch.list_sessions() == []  # no session spawned
    with session_scope(platform.engine) as db:  # no thread minted
        assert list(db.exec(select(ChatThreadRecord))) == []


# --------------------------------------------------------------------------- #
# chat OFF => the one-shot behavior is byte-equivalent (pinned)
# --------------------------------------------------------------------------- #
async def test_chat_disabled_stays_legacy_one_shot(platform):
    ch = ChatMockChannel({"inbound_enabled": True, "allowed_senders": ["777"]})
    poller, orch, _store = _chat_poller(platform, ch, _fake_turn())

    res = await poller._handle("tg", ch, _msg("do the thing"))

    sessions = orch.list_sessions()
    assert len(sessions) == 1 and sessions[0].task == "do the thing"
    # EXACT legacy shape — no thread keys, no status drift.
    assert res == {
        "channel": "tg",
        "status": "handled",
        "session_id": sessions[0].id,
        "sent": True,
    }
    with session_scope(platform.engine) as db:  # and no thread rows minted
        assert list(db.exec(select(ChatThreadRecord))) == []


# --------------------------------------------------------------------------- #
# free-form full chat
# --------------------------------------------------------------------------- #
async def test_free_form_full_chat_happy_path(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("The answer is 42.")
    poller, orch, store = _chat_poller(platform, ch, turn)

    res = await poller._handle("tg", ch, _msg("what is the answer?"))

    assert res["status"] == "chat" and res["sent"] is True
    assert store.history_body(res["thread_id"]) == [
        {"role": "user", "content": "what is the answer?"},
        {"role": "assistant", "content": "The answer is 42."},
    ]
    assert ch.sent == ["Iron Jarvis: The answer is 42."]
    body = turn.calls[0]
    assert body.auto_tools is True
    assert [(m.role, m.content) for m in body.messages] == [
        ("user", "what is the answer?")
    ]
    assert orch.list_sessions() == []  # no one-shot session spawned


async def test_thread_continuity_across_messages(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("noted")
    poller, _orch, _store = _chat_poller(platform, ch, turn)

    r1 = await poller._handle("tg", ch, _msg("first", update_id=1))
    r2 = await poller._handle("tg", ch, _msg("second", update_id=2))

    assert r1["thread_id"] == r2["thread_id"]
    # The second turn SAW the first exchange.
    assert [(m.role, m.content) for m in turn.calls[1].messages] == [
        ("user", "first"),
        ("assistant", "noted"),
        ("user", "second"),
    ]


async def test_slash_new_appends_handoff_then_retires(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, _fake_turn())

    old = (await poller._handle("tg", ch, _msg("hello", update_id=1)))["thread_id"]
    res = await poller._handle("tg", ch, _msg("  /NEW ", update_id=2))

    assert res["status"] == "new_thread" and res["sent"] is True
    # The exchange landed on the OLD thread before retirement.
    assert store.history_body(old)[-2:] == [
        {"role": "user", "content": "/NEW"},
        {"role": "assistant", "content": NEW_THREAD_REPLY},
    ]
    assert any(NEW_THREAD_REPLY in s for s in ch.sent)
    # Retired: the next message mints a FRESH thread.
    fresh = await poller._handle("tg", ch, _msg("again", update_id=3))
    assert fresh["thread_id"] != old


async def test_command_exchange_lands_on_thread(platform):
    class FakeInterpreter:
        async def interpret(self, text: str):
            return "status: all good" if text.startswith("/status") else None

    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(
        platform, ch, _fake_turn(), command_interpreter=FakeInterpreter()
    )

    res = await poller._handle("tg", ch, _msg("/status"))

    assert res["status"] == "command" and res["sent"] is True
    thread = store.resolve("tg", "777")
    assert store.history_body(thread.id) == [
        {"role": "user", "content": "/status"},
        {"role": "assistant", "content": "status: all good"},
    ]


async def test_escalate_acks_then_delivers_session_summary(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("noted")
    poller, orch, store = _chat_poller(platform, ch, turn)
    await poller._handle("tg", ch, _msg("the color is red", update_id=1))

    async def escalating(platform_, personas, body):
        return {
            "reply": "this needs an agent",
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": True,
            "escalate_reason": "multi-step",
        }

    poller.chat_turn = escalating
    res = await poller._handle("tg", ch, _msg("build the report", update_id=2))

    assert res["status"] == "chat_escalated" and res["session_id"]
    task = orch.list_sessions()[-1].task
    assert task.startswith("Context from our recent conversation:")
    assert "user: the color is red" in task
    assert "assistant: noted" in task
    assert task.rstrip().endswith("Request: build the report")
    msgs = store.history_body(res["thread_id"])
    # user, reply, user, ack, summary — the summary is the LAST message.
    assert msgs[3] == {"role": "assistant", "content": ESCALATE_ACK}
    assert msgs[4]["role"] == "assistant" and msgs[4]["content"]
    assert any(ESCALATE_ACK in s for s in ch.sent)
    assert len(ch.sent) >= 3  # reply, ack, summary


async def test_http_exception_becomes_honest_reply(platform):
    async def failing(platform_, personas, body):
        raise HTTPException(status_code=404, detail="no skill named 'foo'")

    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, failing)

    res = await poller._handle("tg", ch, _msg("/x nope"))
    # "/x" is not a known command (no interpreter wired) so it falls through
    # to the chat turn, which 404s — the phone hears the honest detail.
    assert res["status"] == "chat_error" and res["sent"] is True
    assert store.history_body(res["thread_id"])[-1] == {
        "role": "assistant",
        "content": "I hit a problem: no skill named 'foo'",
    }
    assert "I hit a problem: no skill named 'foo'" in ch.sent[-1]


async def test_rate_cap_pauses_after_eight_turns_then_recovers(platform):
    now = [0.0]
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(
        platform, ch, _fake_turn("ok"), clock=lambda: now[0]
    )

    for i in range(RATE_MAX_TURNS):
        res = await poller._handle("tg", ch, _msg(f"m{i}", update_id=i))
        assert res["status"] == "chat"
    tid = res["thread_id"]
    before = len(store.history_body(tid, limit=100))

    limited = await poller._handle("tg", ch, _msg("m9", update_id=99))
    assert limited["status"] == "rate_limited"
    assert RATE_LIMIT_REPLY in ch.sent[-1]
    # NOTHING appended for the limited turn.
    assert len(store.history_body(tid, limit=100)) == before

    now[0] = 61.0  # the window rolls — turns flow again
    ok = await poller._handle("tg", ch, _msg("m10", update_id=100))
    assert ok["status"] == "chat"


async def test_reply_chunked_to_channel_limit(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    ch.chunk_limit = 40
    poller, _orch, _store = _chat_poller(platform, ch, _fake_turn("word " * 40))

    res = await poller._handle("tg", ch, _msg("talk a lot"))

    assert res["sent"] is True
    assert len(ch.sent) > 1  # a 200-char reply cannot fit one 40-char message
    assert all(len(s) <= 40 for s in ch.sent)
    joined = "".join("".join(s.split()) for s in ch.sent)
    assert "".join(("Iron Jarvis: " + "word " * 40).strip().split()) == joined


def test_safe_append_heals_vanished_thread(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, _fake_turn())
    tid = store.resolve("tg", "777", "Val").id
    with session_scope(platform.engine) as db:  # dashboard delete mid-flight
        db.delete(db.get(ChatThreadRecord, tid))
        db.commit()

    landed = poller._safe_append(tid, "user", "x", channel="tg", sender_id="777")

    assert landed and landed != tid  # re-resolved onto a FRESH thread
    assert store.history_body(landed) == [{"role": "user", "content": "x"}]


# --------------------------------------------------------------------------- #
# idle-loop re-arm: enabling two-way at runtime needs NO restart
# --------------------------------------------------------------------------- #
async def test_poller_picks_up_runtime_enablement_without_restart(platform):
    notifier = Notifier()
    orch = Orchestrator(platform)
    poller = InboundPoller(notifier, orch, platform.engine)
    assert poller.enabled() is False
    assert await poller.poll_once() == []  # the idle pass: no work, no network

    # The Channels page adds a two-way destination LIVE (no new poller).
    ch = ChatMockChannel({"inbound_enabled": True, "allowed_senders": ["777"]})
    ch.updates.append(_msg("do it", update_id=1))
    notifier.add_channel("tg", ch)

    assert poller.enabled() is True  # the loop's per-pass check now says go
    results = await poller.poll_once()
    assert results and results[0]["status"] == "handled"


# --------------------------------------------------------------------------- #
# routes: GET /comm/channels fields + POST chat_enabled parse
# --------------------------------------------------------------------------- #
def test_channel_rows_carry_two_way_fields(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        rows = client.get("/comm/channels").json()["channels"]
        assert rows  # mock + this-pc
        for row in rows:
            assert row["inbound_enabled"] is False
            assert row["chat_enabled"] is False
            assert row["allowed_senders_count"] == 0


def test_add_telegram_channel_parses_chat_fields(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post(
            "/comm/channels",
            json={
                "name": "tg",
                "type": "telegram",
                "config": {
                    "token": "123:abc",
                    "chat_id": "777",
                    "inbound_enabled": "true",
                    "chat_enabled": "true",
                    "allowed_senders": "777, 888",
                },
            },
        )
        assert r.status_code == 200
        row = next(
            c for c in client.get("/comm/channels").json()["channels"]
            if c["name"] == "tg"
        )
        assert row["inbound_enabled"] is True
        assert row["chat_enabled"] is True
        assert row["allowed_senders_count"] == 2
        # And the persisted config holds REAL booleans, not strings.
        cfg = client.app.state.platform.config.comm["channels"]["tg"]
        assert cfg["inbound_enabled"] is True and cfg["chat_enabled"] is True


def test_chat_enabled_true_normalizes_inbound_on(tmp_path):
    """Chat implies listening — POST normalizes chat=true → inbound=true so
    stored config always matches the EFFECTIVE state GET reports. Otherwise a
    chat=true/inbound=false save would read back chat OFF and the edit form
    (seeded from GET) would silently persist chat=false on the next save."""
    base = {"token": "123:abc", "chat_id": "777", "allowed_senders": "777"}
    with TestClient(create_app(str(tmp_path))) as client:
        # inbound_enabled ABSENT entirely.
        r = client.post(
            "/comm/channels",
            json={"name": "tga", "type": "telegram",
                  "config": {**base, "chat_enabled": "true"}},
        )
        assert r.status_code == 200
        # inbound_enabled explicitly FALSE — chat still wins.
        r2 = client.post(
            "/comm/channels",
            json={"name": "tgb", "type": "telegram",
                  "config": {**base, "chat_enabled": "true",
                             "inbound_enabled": "false"}},
        )
        assert r2.status_code == 200
        channels = client.app.state.platform.config.comm["channels"]
        for name in ("tga", "tgb"):
            assert channels[name]["chat_enabled"] is True
            assert channels[name]["inbound_enabled"] is True
        rows = {c["name"]: c for c in client.get("/comm/channels").json()["channels"]}
        for name in ("tga", "tgb"):
            assert rows[name]["chat_enabled"] is True
            assert rows[name]["inbound_enabled"] is True
        # chat_enabled false does NOT drag inbound on.
        r3 = client.post(
            "/comm/channels",
            json={"name": "tgc", "type": "telegram",
                  "config": {**base, "chat_enabled": "false"}},
        )
        assert r3.status_code == 200
        cfg3 = client.app.state.platform.config.comm["channels"]["tgc"]
        assert cfg3["chat_enabled"] is False
        assert "inbound_enabled" not in cfg3


# --------------------------------------------------------------------------- #
# POST /comm/threads/{id}/send — the desktop reply fan-out
# --------------------------------------------------------------------------- #
def _wire_chat_channel(client, turn):
    """Register a live chat channel + swap in the fake turn service."""
    app = client.app
    ch = ChatMockChannel(dict(CHAT_CFG))
    app.state.platform.notifier.add_channel("tg", ch)
    app.state.inbound_poller.chat_turn = turn
    return app.state.comm_thread_store, app.state.inbound_poller, ch


def test_desktop_send_runs_turn_and_fans_out(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, _fake_turn("Desk reply."))
        t = store.resolve("tg", "777", "Val")

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "hi from desk"})

        assert r.status_code == 200
        data = r.json()
        assert data["reply"] == "Desk reply."
        assert data["escalate"] is False
        assert data["sent"] is True
        assert store.history_body(t.id) == [
            {"role": "user", "content": "hi from desk"},
            {"role": "assistant", "content": "Desk reply."},
        ]
        assert ch.sent == ["Iron Jarvis: Desk reply."]  # the phone heard it too


def test_desktop_send_404_unknown_thread(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/comm/threads/nope/send", json={"text": "hi"})
        assert r.status_code == 404


def test_desktop_send_409_on_user_owned_thread(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        with session_scope(platform.engine) as db:
            rec = ChatThreadRecord(title="mine")  # owner defaults to "user"
            db.add(rec)
            db.commit()
            tid = rec.id
        r = client.post(f"/comm/threads/{tid}/send", json={"text": "hi"})
        assert r.status_code == 409
        # Pre-migration rows read owner as NULL/empty — the route coalesces
        # any falsy owner to "user" ((rec.owner or "user")). A fresh schema
        # enforces NOT NULL, so exercise the same falsy path with "".
        from sqlalchemy import text as _sql

        with session_scope(platform.engine) as db:
            db.execute(
                _sql("UPDATE chatthreadrecord SET owner = '' WHERE id = :i"),
                {"i": tid},
            )
            db.commit()
        assert client.post(f"/comm/threads/{tid}/send", json={"text": "hi"}).status_code == 409


def test_desktop_send_409_when_retired_or_channel_gone(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, _ch = _wire_chat_channel(client, _fake_turn())
        # Retired via /new: daemon-owned but unbound.
        t = store.resolve("tg", "777", "Val")
        store.retire("tg", "777")
        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "hi"})
        assert r.status_code == 409
        assert "no longer linked" in r.json()["detail"]
        # Bound to a channel that no longer exists in the notifier.
        ghost = store.resolve("ghost-channel", "1", "X")
        r2 = client.post(f"/comm/threads/{ghost.id}/send", json={"text": "hi"})
        assert r2.status_code == 409


def test_desktop_send_escalate_acks_and_backgrounds_session(tmp_path):
    async def escalating(platform_, personas, body):
        return {
            "reply": "needs an agent",
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": True,
            "escalate_reason": "real work",
        }

    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, escalating)
        t = store.resolve("tg", "777", "Val")

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "build it"})

        assert r.status_code == 200
        data = r.json()
        assert data["escalate"] is True
        assert data["reply"] == ESCALATE_ACK  # ack-shaped: the hand-off note
        assert data["sent"] is True and data["session_id"]
        # The session exists immediately (created before the response) …
        orch = client.app.state.orchestrator
        assert any(s.id == data["session_id"] for s in orch.list_sessions())
        # … and the SUMMARY arrives on the thread from the background task.
        deadline = time.time() + 10
        while time.time() < deadline:
            msgs = store.history_body(t.id)
            if len(msgs) >= 3:
                break
            time.sleep(0.05)
        assert len(msgs) >= 3
        assert msgs[1] == {"role": "assistant", "content": ESCALATE_ACK}
        assert msgs[2]["role"] == "assistant" and msgs[2]["content"]
        assert len(ch.sent) >= 2  # ack + summary both reached the phone


def test_desktop_send_escalate_failure_delivers_honest_error(tmp_path):
    """The BACKGROUND session path fails → the honest error still lands on the
    thread AND reaches the phone (no silent vanish behind the 200 ack)."""

    async def escalating(platform_, personas, body):
        return {
            "reply": "needs an agent",
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": True,
            "escalate_reason": "real work",
        }

    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, escalating)
        t = store.resolve("tg", "777", "Val")

        async def boom(session_id):
            raise RuntimeError("agent runtime melted")

        client.app.state.orchestrator.run_session = boom  # instance shadow

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "build it"})

        assert r.status_code == 200 and r.json()["escalate"] is True
        deadline = time.time() + 10
        msgs: list[dict[str, str]] = []
        while time.time() < deadline:
            msgs = store.history_body(t.id)
            if len(msgs) >= 3:
                break
            time.sleep(0.05)
        # user, ack, honest error — appended by the background task.
        assert msgs[2] == {
            "role": "assistant",
            "content": "I hit a problem: RuntimeError: agent runtime melted",
        }
        assert any("agent runtime melted" in s for s in ch.sent)


def test_desktop_send_shares_the_rate_cap(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        store, poller, _ch = _wire_chat_channel(client, _fake_turn())
        t = store.resolve("tg", "777", "Val")
        # The identity already burned its window (e.g. from the phone side).
        poller._clock = lambda: 100.0
        poller._turn_times[("tg", "777")] = deque([100.0] * RATE_MAX_TURNS)

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "hi"})

        assert r.status_code == 429
        assert RATE_LIMIT_REPLY in r.json()["detail"]
        assert store.history_body(t.id) == []  # nothing appended


def test_desktop_send_http_exception_becomes_honest_reply(tmp_path):
    async def failing(platform_, personas, body):
        raise HTTPException(status_code=502, detail="provider melted")

    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, failing)
        t = store.resolve("tg", "777", "Val")

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "hi"})

        assert r.status_code == 200  # the honest reply IS the turn's outcome
        data = r.json()
        assert data["reply"] == "I hit a problem: provider melted"
        assert data["error"] == "provider melted"
        assert store.history_body(t.id)[-1]["content"] == "I hit a problem: provider melted"
        assert any("provider melted" in s for s in ch.sent)
