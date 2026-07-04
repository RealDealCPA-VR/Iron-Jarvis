"""Offline tests for two-way comm — the inbound poller (receive leg).

No real network: the Telegram ``getUpdates`` long-poll and ``sendMessage`` both
go through injected recorders. Covers the security model: off-by-default, the
fail-closed sender allowlist, supervised-session spawning, and the durable offset
that dedupes across a restart.
"""

from __future__ import annotations

from typing import Any

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import (
    InboundPoller,
    Notifier,
    TelegramChannel,
    build_notifier,
)
from iron_jarvis.comm.models import InboundOffsetRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app


# --------------------------------------------------------------------------- #
# Fakes — a Telegram transport that honours the getUpdates offset semantics.
# --------------------------------------------------------------------------- #
class FakeTelegram:
    """Injected (url, params) transports for getUpdates (GET) + sendMessage (POST).

    Holds a list of pending updates; a ``getUpdates`` call with an ``offset``
    drops (confirms) every update with a lower id, exactly like the real API —
    so a persisted offset dedupes on the next poll.
    """

    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = list(updates)
        self.sent: list[dict[str, Any]] = []
        self.poll_calls = 0

    # GET getUpdates
    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self.poll_calls += 1
        assert "getUpdates" in url
        offset = int(params.get("offset", 0) or 0)
        if offset:
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
        return {"ok": True, "result": list(self.updates)}

    # POST sendMessage
    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert "sendMessage" in url
        self.sent.append(payload)
        return {"status_code": 200}


def _update(update_id: int, sender: int, text: str, *, is_bot: bool = False) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {"id": sender, "is_bot": is_bot},
            "chat": {"id": sender},
        },
    }


def _telegram(fake: FakeTelegram, config: dict[str, Any]) -> TelegramChannel:
    return TelegramChannel(
        config,
        http_post=fake.post,
        http_get=fake.get,
        secret_resolver=lambda n: "BOTTOKEN" if n == "tg_token" else None,
    )


def _poller(platform, channel: TelegramChannel) -> tuple[InboundPoller, Orchestrator]:
    notifier = Notifier()
    notifier.add_channel("tg", channel)
    orch = Orchestrator(platform)
    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus)
    return poller, orch


# --------------------------------------------------------------------------- #
# OFF BY DEFAULT — no channels configured => poller does nothing, no network.
# --------------------------------------------------------------------------- #
def test_poller_disabled_with_no_channels(platform):
    notifier = build_notifier(None)  # the default => a single MockChannel
    poller = InboundPoller(notifier, Orchestrator(platform), platform.engine)
    assert poller.enabled() is False
    assert poller.inbound_channels() == []


def test_poller_disabled_until_inbound_explicitly_enabled(platform):
    fake = FakeTelegram([])
    # Configured + credentialed, but inbound NOT enabled => still off.
    ch = _telegram(fake, {"token_secret": "tg_token", "allowed_senders": [1]})
    poller, _ = _poller(platform, ch)
    assert poller.enabled() is False


def test_poller_skips_inbound_channel_without_credentials(platform):
    fake = FakeTelegram([])
    ch = TelegramChannel(
        {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [1]},
        http_post=fake.post,
        http_get=fake.get,
        secret_resolver=lambda n: None,  # token does NOT resolve
    )
    poller, _ = _poller(platform, ch)
    assert poller.enabled() is False  # opted in but no credentials => skipped


def test_daemon_boots_with_no_inbound_and_makes_no_channels(tmp_path):
    """A default daemon boot creates no inbound poller and no network."""
    from fastapi.testclient import TestClient

    with TestClient(create_app(str(tmp_path))) as client:
        assert client.get("/health").json()["status"] == "ok"
        # default comm => the mock channel only; nothing inbound-enabled.
        # (/comm/channels returns {name,type} objects so the UI can label/delete.)
        channels = client.get("/comm/channels").json()["channels"]
        assert [c["name"] for c in channels] == ["mock"]


# --------------------------------------------------------------------------- #
# AUTHORIZED sender => spawns ONE supervised session + replies the summary.
# --------------------------------------------------------------------------- #
async def test_authorized_sender_spawns_supervised_session_and_replies(platform):
    fake = FakeTelegram([_update(10, sender=777, text="do the thing")])
    ch = _telegram(
        fake,
        {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]},
    )
    poller, orch = _poller(platform, ch)
    assert poller.enabled() is True

    results = await poller.poll_once()

    assert len(results) == 1 and results[0]["status"] == "handled"
    sessions = orch.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].agent_type.value == "supervisor"
    assert sessions[0].task == "do the thing"
    # Replied back to the sender's chat with the session summary.
    assert len(fake.sent) == 1
    assert fake.sent[0]["chat_id"] == 777
    assert fake.sent[0]["text"]


# --------------------------------------------------------------------------- #
# UNAUTHORIZED sender => spawns NOTHING, no reply, no leak.
# --------------------------------------------------------------------------- #
async def test_unauthorized_sender_spawns_nothing(platform):
    fake = FakeTelegram([_update(10, sender=999, text="rm -rf /")])
    ch = _telegram(
        fake,
        {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]},
    )
    poller, orch = _poller(platform, ch)

    results = await poller.poll_once()

    assert results == [{"channel": "tg", "status": "unauthorized", "sender": "999"}]
    assert orch.list_sessions() == []
    assert fake.sent == []


async def test_empty_allowlist_authorizes_nobody(platform):
    """Fail-closed: an empty/missing allowlist rejects every sender."""
    fake = FakeTelegram([_update(10, sender=777, text="hello")])
    ch = _telegram(
        fake, {"token_secret": "tg_token", "inbound_enabled": True}  # no allowed_senders
    )
    poller, orch = _poller(platform, ch)

    results = await poller.poll_once()

    assert results[0]["status"] == "unauthorized"
    assert orch.list_sessions() == []
    assert fake.sent == []


async def test_bot_messages_are_ignored(platform):
    """Loop protection: the bot's own / other bots' messages never act."""
    fake = FakeTelegram([_update(10, sender=777, text="echo", is_bot=True)])
    ch = _telegram(
        fake,
        {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]},
    )
    poller, orch = _poller(platform, ch)

    results = await poller.poll_once()

    assert results[0]["status"] == "ignored_bot"
    assert orch.list_sessions() == []


# --------------------------------------------------------------------------- #
# DURABLE OFFSET — advances + dedupes across a restart (fresh poller/orch).
# --------------------------------------------------------------------------- #
async def test_offset_advances_and_dedupes_on_restart(platform):
    fake = FakeTelegram([_update(42, sender=777, text="task one")])
    cfg = {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]}

    # First boot: process the one message.
    poller1, orch1 = _poller(platform, _telegram(fake, cfg))
    await poller1.poll_once()
    assert len(orch1.list_sessions()) == 1

    # The durable offset advanced to update_id + 1.
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
    assert rec is not None and rec.offset == 43

    # Simulate a restart: a brand-new poller + orchestrator over the SAME engine
    # and the SAME transport (still holding update 42). It must NOT reprocess.
    # (Both orchestrators share the DB, so the count must STAY at 1, not grow.)
    poller2, orch2 = _poller(platform, _telegram(fake, cfg))
    results = await poller2.poll_once()
    assert results == []  # offset-confirmed => getUpdates returns nothing new
    assert len(orch2.list_sessions()) == 1  # no duplicate session spawned


async def test_offset_persists_across_multiple_messages(platform):
    fake = FakeTelegram(
        [
            _update(1, sender=777, text="first"),
            _update(2, sender=777, text="second"),
        ]
    )
    cfg = {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]}
    poller, orch = _poller(platform, _telegram(fake, cfg))

    await poller.poll_once()

    assert len(orch.list_sessions()) == 2
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
    assert rec.offset == 3


# --------------------------------------------------------------------------- #
# build_notifier plumbs the two-way config onto the channel.
# --------------------------------------------------------------------------- #
def test_build_notifier_preserves_inbound_config():
    fake = FakeTelegram([])
    cfg = {
        "channels": {
            "tg": {
                "type": "telegram",
                "token_secret": "tg_token",
                "inbound_enabled": True,
                "allowed_senders": [777, "888"],
            }
        }
    }
    notifier = build_notifier(
        cfg,
        secret_resolver=lambda n: "TOK",
        http_post=fake.post,
        http_get=fake.get,
    )
    ch = notifier.get("tg")
    assert ch.inbound_enabled() is True
    assert ch.allowed_senders() == {"777", "888"}
    assert ch.is_authorized(777) is True  # int id matches stringified allowlist
    assert ch.is_authorized(123) is False


def test_non_inbound_channel_types_never_poll():
    """Outbound-only channels report no inbound leg and yield nothing on poll.

    Slack moved OUT of this set in v1.16: it is inbound-capable via SOCKET MODE
    (an outbound WebSocket — see comm/slack_socket.py), though its HTTP poll
    leg stays a no-op (the socket, not the poller, delivers its messages).
    """
    from iron_jarvis.comm import DiscordChannel, SlackChannel

    ch = DiscordChannel({"webhook_url": "u", "inbound_enabled": True, "allowed_senders": [1]})
    assert ch.supports_inbound is False
    assert ch.inbound_enabled() is False  # type can't receive, so stays off
    assert ch.poll(0) == ([], 0)

    slack = SlackChannel(
        {"webhook_url": "u", "inbound_enabled": True, "allowed_senders": ["U1"]}
    )
    assert slack.supports_inbound is True  # Socket Mode receive leg
    assert slack.poll(0) == ([], 0)  # but polling delivers nothing — the socket does


async def test_group_chat_message_is_refused(platform):
    # chat.id != sender id => a group/non-private chat. Even from an allowlisted
    # sender it must NOT run, since replying would broadcast output to the group.
    upd = {
        "update_id": 11,
        "message": {"text": "do it", "from": {"id": 777, "is_bot": False}, "chat": {"id": -100999}},
    }
    fake = FakeTelegram([upd])
    ch = _telegram(
        fake,
        {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]},
    )
    poller, orch = _poller(platform, ch)
    results = await poller.poll_once()
    assert results[0]["status"] == "non_private"
    assert orch.list_sessions() == []  # nothing ran
    assert fake.sent == []  # nothing broadcast
