"""CX-05 — inbound Slack TRIGGERS agent work (the world starts work).

Two edited surfaces, both proven offline against a real daemon on a temp root:

  * HTTP Events receiver ``POST /comm/slack/events/{name}`` (routes/comm.py) —
    a signed ``event_callback`` now reaches the pipeline instead of dead-ending:
    a channel message / ``@mention`` from an ALLOWED sender fires the channel's
    ``slack`` reflex rules; a DM runs the full ``_handle`` pipeline. The Slack v0
    signature stays the auth (unsigned/wrong-secret => 403) and the
    ``url_verification`` challenge still echoes.
  * Socket Mode ``process_envelope`` (comm/slack_socket.py) — the DM-only filter
    is relaxed: DMs keep ``_handle``; channel ``@mentions`` from an authorized
    sender fire ``on_slack``. Bot loop-protection + fail-closed allowlist hold.

Security invariants asserted here: FAIL-CLOSED allowlist (a non-allowlisted
channel sender fires nothing), LOOP PROTECTION (a ``bot_id`` message is ignored),
and NO auto-reply into a shared channel (channel messages fire reflex only).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.channels import SlackChannel
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.slack_socket import SlackSocketMode
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.comm import _channel_config_problem

SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _add_slack(client, name="team", allowed="U1"):
    """A two-way slack channel: bot token + channel (delivery), signing secret
    (event auth), inbound enabled, and a fail-closed allowlist."""
    r = client.post(
        "/comm/channels",
        json={
            "type": "slack",
            "name": name,
            "config": {
                "token": "xoxb-test",
                "channel": "#general",
                "signing_secret": SIGNING,
                "inbound_enabled": "true",
                "allowed_senders": allowed,
            },
        },
    )
    assert r.status_code == 200, r.text


def _signed_headers(body: bytes, secret: str = SIGNING, ts: str | None = None):
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def _event(**event: Any) -> bytes:
    return json.dumps({"type": "event_callback", "event": event}).encode()


class _ReflexRecorder:
    """Stand-in for ``app.state.reflex_router`` capturing on_slack calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def on_slack(self, *, text: str, channel: str = "", sender: str = "") -> list:
        self.calls.append({"text": text, "channel": channel, "sender": sender})
        return []


# --------------------------------------------------------------------------- #
# 1. AUTH is preserved: unsigned / wrong-secret / stale => 403; challenge echoes.
# --------------------------------------------------------------------------- #
def test_signature_is_the_auth_and_challenge_echoes(tmp_path):
    client = _client(tmp_path)
    _add_slack(client)

    # url_verification still echoes the challenge (correctly signed).
    body = json.dumps({"type": "url_verification", "challenge": "c-xyz"}).encode()
    r = client.post("/comm/slack/events/team", content=body, headers=_signed_headers(body))
    assert r.status_code == 200 and r.json()["challenge"] == "c-xyz"

    evt = _event(type="app_mention", text="hi", user="U1", channel="C1")
    # UNSIGNED (no signature headers at all) is rejected.
    assert client.post("/comm/slack/events/team", content=evt).status_code == 403
    # Wrong signing secret is rejected.
    assert client.post(
        "/comm/slack/events/team", content=evt, headers=_signed_headers(evt, secret="nope")
    ).status_code == 403
    # Stale timestamp (outside the ±5min replay window) is rejected.
    assert client.post(
        "/comm/slack/events/team",
        content=evt,
        headers=_signed_headers(evt, ts=str(int(time.time()) - 4000)),
    ).status_code == 403


# --------------------------------------------------------------------------- #
# 2. CHANNEL @mention from an ALLOWED sender reaches the reflex pipeline.
# --------------------------------------------------------------------------- #
def test_channel_mention_reaches_reflex_pipeline(tmp_path):
    client = _client(tmp_path)
    _add_slack(client, allowed="U1")
    rec = _ReflexRecorder()
    client.app.state.reflex_router = rec  # same object the handler reads

    evt = _event(type="app_mention", text="hey jarvis deploy", user="U1", channel="C42")
    r = client.post("/comm/slack/events/team", content=evt, headers=_signed_headers(evt))
    assert r.status_code == 200 and r.json()["ok"] is True

    assert rec.calls == [{"text": "hey jarvis deploy", "channel": "C42", "sender": "U1"}]


# --------------------------------------------------------------------------- #
# 3. A REAL "slack" reflex rule actually fires end-to-end (REFLEX_FIRED + the
#    rule's fire_count) — proving the world can start a supervised session.
# --------------------------------------------------------------------------- #
def test_real_slack_rule_fires_end_to_end(tmp_path):
    client = _client(tmp_path)
    p = client.app.state.platform
    router = client.app.state.reflex_router
    # Neutralise the LAUNCH so the reflex creates its session record + marks fired
    # without us waiting on a full mock run.
    router.spawn_bg = lambda sid, coro: coro.close()

    rule = p.reflex.add(
        name="deploy-reflex", source="slack", match="deploy",
        action="session", task_template="Handle slack: {text}",
    )
    events: list[tuple[str, dict]] = []
    p.event_bus.add_handler(lambda e: events.append((e.type, e.payload)))
    _add_slack(client, allowed="U1")

    evt = _event(type="app_mention", text="please deploy now", user="U1", channel="C1")
    r = client.post("/comm/slack/events/team", content=evt, headers=_signed_headers(evt))
    assert r.status_code == 200 and r.json()["ok"] is True

    types = [t for t, _ in events]
    assert "comm.received" in types  # the endpoint published COMM_RECEIVED
    fired = [pl for t, pl in events if t == "reflex.fired"]
    assert any(pl.get("source") == "slack" and pl.get("ok") for pl in fired)
    assert (p.reflex.get(rule.id).fire_count or 0) >= 1


# --------------------------------------------------------------------------- #
# 4. FAIL-CLOSED: a non-allowlisted channel sender fires NOTHING.
# --------------------------------------------------------------------------- #
def test_unauthorized_channel_sender_fires_nothing(tmp_path):
    client = _client(tmp_path)
    _add_slack(client, allowed="U1")
    rec = _ReflexRecorder()
    client.app.state.reflex_router = rec

    evt = _event(type="app_mention", text="deploy", user="U999", channel="C1")
    r = client.post("/comm/slack/events/team", content=evt, headers=_signed_headers(evt))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert rec.calls == []  # U999 is not on the allowlist


# --------------------------------------------------------------------------- #
# 5. LOOP PROTECTION: a bot's own message is ignored (never fires).
# --------------------------------------------------------------------------- #
def test_bot_message_is_ignored(tmp_path):
    client = _client(tmp_path)
    _add_slack(client, allowed="U1")
    rec = _ReflexRecorder()
    client.app.state.reflex_router = rec

    evt = _event(type="message", text="deploy", user="U1", channel="C1", bot_id="B1")
    r = client.post("/comm/slack/events/team", content=evt, headers=_signed_headers(evt))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert rec.calls == []  # bot_id => loop protection


# --------------------------------------------------------------------------- #
# 6. DM runs the FULL inbound pipeline (_handle) with the right message.
# --------------------------------------------------------------------------- #
def test_dm_runs_full_pipeline(tmp_path, monkeypatch):
    client = _client(tmp_path)
    _add_slack(client, allowed="U1")

    ran = threading.Event()
    captured: dict[str, Any] = {}

    async def _fake_handle(self, name, ch, msg):  # noqa: ANN001
        captured["name"] = name
        captured["msg"] = msg
        ran.set()
        return {"channel": name, "status": "handled"}

    monkeypatch.setattr(InboundPoller, "_handle", _fake_handle)

    evt = _event(type="message", channel_type="im", text="what's up", user="U1", channel="D9")
    r = client.post("/comm/slack/events/team", content=evt, headers=_signed_headers(evt))
    assert r.status_code == 200 and r.json()["ok"] is True

    assert ran.wait(timeout=5), "DM path never invoked the inbound _handle pipeline"
    assert captured["name"] == "team"
    m = captured["msg"]
    assert m.sender_id == "U1"
    assert m.text == "what's up"
    # A DM replies to the SENDER's own 1:1 chat (reply_to == sender_id passes the
    # poller's private-chat guard), mirroring the Socket Mode + Telegram legs.
    assert m.reply_to == "U1"


# --------------------------------------------------------------------------- #
# 7. SOCKET MODE process_envelope: DM => _handle; @mention => reflex; guards.
# --------------------------------------------------------------------------- #
class _FakePoller:
    def __init__(self, reflex_router=None) -> None:
        self.reflex_router = reflex_router
        self.handled: list[tuple[str, Any]] = []

    async def _handle(self, name, ch, msg):  # noqa: ANN001
        self.handled.append((name, msg))
        return {"channel": name, "status": "handled"}


class _FakeNotifier:
    def __init__(self, ch) -> None:
        self._ch = ch

    def get(self, name):  # noqa: ANN001
        return self._ch


def _socket(ch, reflex_router=None):
    poller = _FakePoller(reflex_router)
    sm = SlackSocketMode(poller, _FakeNotifier(ch), lambda n: None, lambda: {})
    return sm, poller


def _envelope(**event: Any) -> dict:
    return {"type": "events_api", "payload": {"event": event}}


def test_socket_dm_uses_handle():
    ch = SlackChannel({"allowed_senders": ["U1"], "inbound_enabled": True})
    sm, poller = _socket(ch)
    env = _envelope(type="message", channel_type="im", text="hi", user="U1")
    res = asyncio.run(sm.process_envelope("team", env))
    assert res == {"channel": "team", "status": "handled"}
    assert len(poller.handled) == 1 and poller.handled[0][1].sender_id == "U1"


def test_socket_channel_mention_fires_reflex():
    ch = SlackChannel({"allowed_senders": ["U1"], "inbound_enabled": True})
    rec = _ReflexRecorder()
    sm, poller = _socket(ch, reflex_router=rec)
    env = _envelope(type="app_mention", text="deploy pls", user="U1", channel="C7")
    res = asyncio.run(sm.process_envelope("team", env))
    assert res == {"channel": "team", "status": "reflex", "sender": "U1"}
    assert rec.calls == [{"text": "deploy pls", "channel": "C7", "sender": "U1"}]
    assert poller.handled == []  # no full session/reply into a shared channel


def test_socket_channel_unauthorized_ignored():
    ch = SlackChannel({"allowed_senders": ["U1"], "inbound_enabled": True})
    rec = _ReflexRecorder()
    sm, _poller = _socket(ch, reflex_router=rec)
    env = _envelope(type="app_mention", text="deploy", user="U999", channel="C7")
    assert asyncio.run(sm.process_envelope("team", env)) is None
    assert rec.calls == []  # fail-closed allowlist


def test_socket_channel_bot_message_ignored():
    # A bot posting into a shared channel must not fire reflex (loop protection).
    # (A bot DM instead delegates to _handle, which drops it there — covered by
    # the Socket Mode suite's own bot-DM test.)
    ch = SlackChannel({"allowed_senders": ["U1", "B1"], "inbound_enabled": True})
    rec = _ReflexRecorder()
    sm, poller = _socket(ch, reflex_router=rec)
    env = _envelope(type="app_mention", text="deploy", user="U1", channel="C7", bot_id="B1")
    assert asyncio.run(sm.process_envelope("team", env)) is None
    assert poller.handled == [] and rec.calls == []


# --------------------------------------------------------------------------- #
# 8. EMAIL two-way validation: inbound_enabled now requires IMAP host + password.
# --------------------------------------------------------------------------- #
def test_email_inbound_requires_imap_and_password():
    base = {"host": "smtp.x", "from_addr": "a@x", "to_addr": "b@x"}
    # Outbound-only email is unaffected.
    assert _channel_config_problem("email", dict(base)) is None
    # Missing SMTP essentials still reported.
    assert _channel_config_problem("email", {"host": "smtp.x"}) is not None
    # inbound on but no IMAP host / password => actionable problem.
    prob = _channel_config_problem("email", {**base, "inbound_enabled": True})
    assert prob is not None and "IMAP" in prob
    # inbound on WITH imap host + a password secret => good to go.
    assert _channel_config_problem(
        "email", {**base, "inbound_enabled": True, "imap_host": "imap.x", "password_secret": "s"}
    ) is None
    # Other channel types are unchanged (slack still needs a delivery method).
    assert _channel_config_problem("slack", {"webhook_url": "https://h"}) is None
