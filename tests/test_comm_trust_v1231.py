"""Two-way comm you can trust (v1.231.0, audit Wave 5, AE8 + AE14).

Converted from ``tests/_audit_20260904/test_a4_telegram.py``. Fakes mirror
``tests/test_comm_inbound.py`` (no network).

* AE8 — a REVOKED bot token (Telegram 401/403) or a refused IMAP login is
  ``ChannelAuthError``: ``poll_once`` records ``{channel, status: "error",
  detail}`` + ``poller.poll_errors[name]``, ``poll_verdict`` gives the
  lifespan loop ``ok=False`` with the words, and ``GET /comm/channels``
  carries ``last_poll_error`` on the row. A poll that comes back clears it.
* AE14 — at-most-once is KEPT (the offset is persisted before handling), but
  the update in flight is persisted with it: a restart mid-handling tells
  that chat "please resend", publishes ``comm.dropped``, and clears the
  marker; a normal handling clears it too, so a healthy restart says nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import (
    ChannelAuthError,
    InboundMessage,
    InboundPoller,
    MockChannel,
    Notifier,
    TelegramChannel,
)
from iron_jarvis.comm import channels as channels_mod
from iron_jarvis.comm.channels import EmailChannel
from iron_jarvis.comm.inbound import DROPPED_REPLY
from iron_jarvis.comm.models import InboundOffsetRecord
from iron_jarvis.comm.prompts import (
    APPROVAL_GONE_REPLY,
    APPROVAL_KIND,
    PendingPromptStore,
    approval_question,
)
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app

CFG = {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]}


class FakeTelegram:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = list(updates)
        self.sent: list[dict[str, Any]] = []
        self.poll_calls = 0

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self.poll_calls += 1
        offset = int(params.get("offset", 0) or 0)
        if offset:
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
        return {"ok": True, "result": list(self.updates)}

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(payload)
        return {"status_code": 200}


class RevokedToken(FakeTelegram):
    """What api.telegram.org answers once the bot token is revoked/rotated."""

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self.poll_calls += 1
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}


class _HttpxLike:
    """An httpx-shaped 401 (the production transport) — status on the object."""

    status_code = 401
    text = '{"ok":false,"error_code":401,"description":"Unauthorized"}'

    def json(self) -> dict[str, Any]:
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}


def _update(update_id: int, sender: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {"text": text, "from": {"id": sender, "is_bot": False}, "chat": {"id": sender}},
    }


def _telegram(fake: FakeTelegram, config: dict[str, Any]) -> TelegramChannel:
    return TelegramChannel(
        config,
        http_post=fake.post,
        http_get=fake.get,
        secret_resolver=lambda n: "BOTTOKEN" if n == "tg_token" else None,
    )


def _poller(platform, channel) -> tuple[InboundPoller, Orchestrator]:
    notifier = Notifier()
    notifier.add_channel("tg", channel)
    orch = Orchestrator(platform)
    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus)
    return poller, orch


# --------------------------------------------------------------------------- #
# AE8 — a revoked token is an ERROR the loop can see, not an empty batch.
# --------------------------------------------------------------------------- #


async def test_revoked_bot_token_is_not_reported_as_a_healthy_poll(platform):
    fake = RevokedToken([])
    poller, orch = _poller(platform, _telegram(fake, CFG))
    assert poller.enabled() is True  # the loop WILL poll this channel every 15s
    results = await poller.poll_once()
    assert fake.poll_calls == 1
    assert results and results[0]["status"] == "error", results
    assert results[0]["channel"] == "tg"
    assert "401" in results[0]["detail"] and "BotFather" in results[0]["detail"]
    # The row's field and the loop's verdict read the same record.
    assert poller.poll_errors["tg"]["detail"] == results[0]["detail"]
    assert poller.poll_errors["tg"]["at"]
    ok, why = poller.poll_verdict(results)
    assert ok is False and "tg:" in str(why) and "401" in str(why)
    assert orch.list_sessions() == []


def test_telegram_401_on_the_httpx_transport_shape_is_an_auth_error():
    """The production transport hands back a response OBJECT; a 401 status
    used to be flattened to None by ``interpret_json`` before anyone saw it."""
    ch = TelegramChannel(
        CFG,
        http_post=lambda u, p: {"status_code": 200},
        http_get=lambda u, p: _HttpxLike(),
        secret_resolver=lambda n: "BOTTOKEN",
    )
    with pytest.raises(ChannelAuthError, match="HTTP 401"):
        ch.poll(0)


def test_telegram_transport_blip_is_still_an_empty_batch():
    """Only a REFUSED credential raises; a 5xx / non-JSON answer keeps the
    old fail-safe ``([], offset)`` so a flaky hour is not a dead-token alarm."""

    class _Down:
        status_code = 502
        text = "bad gateway"

        def json(self):
            raise ValueError("not json")

    ch = TelegramChannel(
        CFG,
        http_post=lambda u, p: {"status_code": 200},
        http_get=lambda u, p: _Down(),
        secret_resolver=lambda n: "BOTTOKEN",
    )
    assert ch.poll(7) == ([], 7)


async def test_a_poll_that_comes_back_clears_the_error(platform):
    revoked = RevokedToken([])
    fixed = FakeTelegram([])
    ch = _telegram(revoked, CFG)
    poller, _ = _poller(platform, ch)
    await poller.poll_once()
    assert "tg" in poller.poll_errors
    ch._http_get = fixed.get  # the token was rotated in the vault
    assert await poller.poll_once() == []
    assert "tg" not in poller.poll_errors
    assert poller.poll_verdict([]) == (True, None)


def test_imap_login_refused_is_an_auth_error(monkeypatch):
    class _Conn:
        def login(self, user, pw):
            raise RuntimeError("[AUTHENTICATIONFAILED] Invalid credentials")

        def logout(self):
            pass

    monkeypatch.setattr(channels_mod, "_imap_connect", lambda host, port: _Conn())
    ch = EmailChannel(
        {
            "username": "me@example.com",
            "password_secret": "mail_pw",
            "imap_host": "imap.example.com",
            "inbound_enabled": True,
        },
        http_post=lambda u, p: {"status_code": 200},
        secret_resolver=lambda n: "hunter2",
    )
    with pytest.raises(ChannelAuthError, match="IMAP login refused for me@example.com"):
        ch.poll(0)


def test_imap_other_failures_stay_an_empty_batch(monkeypatch):
    """The lazy ``select``/search blow-ups keep the pre-AE8 fail-safe."""

    class _Conn:
        def login(self, user, pw):
            return "OK"

        def select(self, mailbox):
            raise OSError("connection reset")

        def logout(self):
            pass

    monkeypatch.setattr(channels_mod, "_imap_connect", lambda host, port: _Conn())
    ch = EmailChannel(
        {"username": "me@example.com", "password_secret": "mail_pw", "imap_host": "h"},
        http_post=lambda u, p: {"status_code": 200},
        secret_resolver=lambda n: "hunter2",
    )
    assert ch.poll(3) == ([], 3)


def test_channel_row_carries_last_poll_error(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    poller = client.app.state.inbound_poller
    rows = {r["name"]: r for r in client.get("/comm/channels").json()["channels"]}
    assert "this-pc" in rows
    assert rows["this-pc"]["last_poll_error"] is None  # the field is ALWAYS present
    poller.poll_errors["this-pc"] = {
        "detail": "telegram: getUpdates refused (HTTP 401: Unauthorized)",
        "at": "2026-09-05T03:00:00+00:00",
    }
    row = next(
        r for r in client.get("/comm/channels").json()["channels"] if r["name"] == "this-pc"
    )
    assert row["last_poll_error"] == "telegram: getUpdates refused (HTTP 401: Unauthorized)"
    assert row["last_poll_error_at"] == "2026-09-05T03:00:00+00:00"


# --------------------------------------------------------------------------- #
# AE14 — at-most-once KEPT; the dropped message leaves a trace and a notice.
# --------------------------------------------------------------------------- #


async def test_message_in_flight_at_restart_is_dropped_with_a_trace(platform):
    fake = FakeTelegram([_update(10, 777, "rename the K-1s in the intake folder")])
    poller, orch = _poller(platform, _telegram(fake, CFG))

    async def killed_mid_handle(name, ch, msg):
        raise asyncio.CancelledError  # lifespan cancels inbound_task at shutdown

    poller._handle = killed_mid_handle
    with pytest.raises(asyncio.CancelledError):
        await poller.poll_once()
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
        assert rec.offset == 11  # at-most-once, unchanged
        assert rec.inflight_update_id == 10 and rec.inflight_chat_id == "777"

    # The restart: fresh poller + orchestrator over the same DB and transport.
    poller2, orch2 = _poller(platform, _telegram(fake, CFG))
    results = await poller2.poll_once()
    assert results == [{"channel": "tg", "status": "dropped", "update_id": 10, "notified": True}]
    assert orch2.list_sessions() == []  # the message itself is gone (by design)
    # The phone hears it, in the poller's voice, in the same chat.
    assert fake.sent == [{"chat_id": "777", "text": f"Iron Jarvis: {DROPPED_REPLY}"}]
    # The desktop timeline has it.
    dropped = [e for e in platform.event_bus.history if e.type == "comm.dropped"]
    assert len(dropped) == 1
    assert dropped[0].payload["channel"] == "tg"
    assert dropped[0].payload["update_id"] == 10
    assert dropped[0].payload["chat_id"] == "777"
    assert dropped[0].payload["notified"] is True
    # The marker is cleared: a second pass (and the next boot) says nothing.
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
        assert rec.inflight_update_id is None and rec.inflight_chat_id == ""
    assert await poller2.poll_once() == []
    assert len(fake.sent) == 1
    assert len([e for e in platform.event_bus.history if e.type == "comm.dropped"]) == 1


async def test_a_handled_message_clears_the_inflight_marker(platform):
    """A normal exchange must not leave the marker behind, or the next boot
    would apologise for a message that was answered."""
    fake = FakeTelegram([_update(10, 999, "hello")])  # not allowlisted: handled = rejected
    poller, _ = _poller(platform, _telegram(fake, CFG))
    seen: dict[str, Any] = {}

    real_handle = poller._handle

    async def spy(name, ch, msg):
        with session_scope(platform.engine) as db:
            rec = db.get(InboundOffsetRecord, name)
            seen["during"] = (rec.inflight_update_id, rec.inflight_chat_id)
        return await real_handle(name, ch, msg)

    poller._handle = spy
    results = await poller.poll_once()
    assert results == [{"channel": "tg", "status": "unauthorized", "sender": "999"}]
    assert seen["during"] == (10, "999")  # set BEFORE handling, in the offset's write
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
        assert rec.offset == 11
        assert rec.inflight_update_id is None and rec.inflight_chat_id == ""
    # Nothing to recover on the next pass.
    poller2, _ = _poller(platform, _telegram(fake, CFG))
    assert await poller2.poll_once() == []
    assert fake.sent == []
    assert [e.type for e in platform.event_bus.history if e.type == "comm.dropped"] == []


async def test_a_handling_exception_clears_the_marker_and_is_an_error_row(platform):
    fake = FakeTelegram([_update(10, 777, "hi")])
    poller, _ = _poller(platform, _telegram(fake, CFG))

    async def boom(name, ch, msg):
        raise RuntimeError("thread store exploded")

    poller._handle = boom
    results = await poller.poll_once()
    assert results[0]["status"] == "error"
    assert "thread store exploded" in results[0]["detail"]
    assert poller.poll_verdict(results)[0] is False
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
        assert rec.inflight_update_id is None  # handled (badly) in-process: not a drop


# --------------------------------------------------------------------------- #
# CONFIRM (kept from the audit): a non-allowlisted chat id is dropped — no
# model call, no session, no reply; and an approval reply from the phone
# resolves exactly once.
# --------------------------------------------------------------------------- #


async def test_confirm_non_allowlisted_sender_is_dropped_without_a_model_call(platform):
    fake = FakeTelegram([_update(10, 999, "delete everything in C:\\Users")])
    poller, orch = _poller(platform, _telegram(fake, CFG))
    results = await poller.poll_once()
    assert results == [{"channel": "tg", "status": "unauthorized", "sender": "999"}]
    assert orch.list_sessions() == [] and fake.sent == []
    assert [e.type for e in platform.event_bus.history if e.type.startswith("comm.")] == [
        "comm.rejected"
    ]


class ChatMockChannel(MockChannel):
    supports_inbound = True

    def has_credentials(self) -> bool:
        return True


class FakeApprovals:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.open = {"apr_1"}

    def resolve(self, approval_id: str, decision: str) -> bool:
        self.calls.append((approval_id, decision))
        return self.open.discard(approval_id) is None and approval_id not in self.calls[:-1]


def _msg(text: str, update_id: int) -> InboundMessage:
    return InboundMessage(sender_id="777", text=text, update_id=update_id, reply_to="777")


async def test_confirm_two_approve_replies_resolve_the_approval_once(platform):
    approvals = FakeApprovals()
    platform.approvals = approvals
    ch = ChatMockChannel({"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]})
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    orch = Orchestrator(platform)
    pstore = PendingPromptStore(platform.engine)

    async def turn(platform_, personas, body):
        return {"reply": "chat", "provider": "mock", "model": "m", "tools_used": [], "escalate": False}

    poller = InboundPoller(
        notifier,
        orch,
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=CommThreadStore(platform.engine),
        chat_turn=turn,
        personas={},
        platform=platform,
        prompt_store=pstore,
    )
    pstore.register(APPROVAL_KIND, "apr_1", approval_question("shell"), ["approve", "deny"], "tg", "777", "")

    first = await poller._handle("tg", ch, _msg("approve", 1))
    second = await poller._handle("tg", ch, _msg("approve", 2))
    assert first["status"] == "approval_answered"
    assert second["status"] in ("chat", "approval_expired")  # no second resolve
    assert [c for c in approvals.calls if c[1] == "once"] == [("apr_1", "once")]
    assert orch.list_sessions() == []
    if second["status"] == "approval_expired":
        assert APPROVAL_GONE_REPLY in ch.sent[-1]
