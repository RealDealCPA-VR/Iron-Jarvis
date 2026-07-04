"""Slack Socket Mode: two-way with zero internet exposure (offline tests)."""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.comm.slack_socket import SlackSocketMode
from iron_jarvis.daemon.app import create_app


def _app_with_slack(tmp_path, *, inbound=True, allow="U111", app_token=True):
    client = TestClient(create_app(str(tmp_path)))
    cfg = {
        "token": "xoxb-x",
        "channel": "#general",
        "inbound_enabled": "true" if inbound else "false",
        "allowed_senders": allow,
    }
    if app_token:
        cfg["app_token"] = "xapp-secret"
    r = client.post("/comm/channels", json={"type": "slack", "name": "hq", "config": cfg})
    assert r.status_code == 200, r.text
    return client


def _socket_for(client, handler_log):
    platform = client.app.state.platform

    class _FakePoller:
        async def _handle(self, name, ch, msg):
            handler_log.append((name, msg.sender_id, msg.text, msg.reply_to, msg.is_bot))
            return {"status": "handled"}

    return SlackSocketMode(
        _FakePoller(),
        platform.notifier,
        platform.secrets.get,
        lambda: platform.config.comm or {},
    )


def _dm_envelope(user="U111", text="hello", **event_extra):
    return {
        "type": "events_api",
        "envelope_id": "env-1",
        "payload": {
            "event": {
                "type": "message",
                "channel_type": "im",
                "user": user,
                "text": text,
                "channel": "D0AA",
                **event_extra,
            }
        },
    }


def test_discovery_requires_optin_allowlist_and_token(tmp_path):
    c1 = _app_with_slack(tmp_path / "a")
    log: list = []
    assert _socket_for(c1, log).enabled() is True

    c2 = _app_with_slack(tmp_path / "b", inbound=False)
    assert _socket_for(c2, []).enabled() is False

    c3 = _app_with_slack(tmp_path / "c", allow="")
    assert _socket_for(c3, []).enabled() is False  # fail-closed: empty allowlist

    c4 = _app_with_slack(tmp_path / "d", app_token=False)
    assert _socket_for(c4, []).enabled() is False


@pytest.mark.asyncio
async def test_dm_envelope_reaches_pipeline_with_dm_reply_routing(tmp_path):
    client = _app_with_slack(tmp_path)
    log: list = []
    sock = _socket_for(client, log)
    res = await sock.process_envelope("hq", _dm_envelope())
    assert res == {"status": "handled"}
    name, sender, text, reply_to, is_bot = log[0]
    assert (name, sender, text) == ("hq", "U111", "hello")
    assert reply_to == "U111"  # DM reply goes back to the SENDER
    assert is_bot is False


@pytest.mark.asyncio
async def test_non_dm_bot_and_subtype_events_ignored(tmp_path):
    client = _app_with_slack(tmp_path)
    log: list = []
    sock = _socket_for(client, log)
    assert await sock.process_envelope("hq", _dm_envelope(channel_type="channel")) is None
    assert await sock.process_envelope(
        "hq", _dm_envelope(subtype="message_changed")
    ) is None
    assert await sock.process_envelope("hq", {"type": "hello"}) is None
    # A bot's own message still reaches _handle, which ignores it there — but
    # is_bot must be carried so the loop-guard fires.
    await sock.process_envelope("hq", _dm_envelope(bot_id="B9"))
    assert log[-1][4] is True
    assert len(log) == 1


def test_channel_add_coerces_inbound_fields(tmp_path):
    client = _app_with_slack(tmp_path, allow="U111, U222 ,")
    platform = client.app.state.platform
    cfg = (platform.config.comm or {})["channels"]["hq"]
    assert cfg["inbound_enabled"] is True
    assert cfg["allowed_senders"] == ["U111", "U222"]
    ch = platform.notifier.get("hq")
    assert ch.inbound_enabled() and ch.is_authorized("U222")
    assert not ch.is_authorized("U999")


@pytest.mark.asyncio
async def test_pump_acks_envelopes(tmp_path):
    """The run_channel pump ACKs each envelope over the socket (3s contract)."""
    import asyncio

    client = _app_with_slack(tmp_path)
    log: list = []
    sock = _socket_for(client, log)
    sent: list[str] = []

    class _FakeWS:
        def __init__(self):
            self._msgs = [json.dumps(_dm_envelope())]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._msgs:
                return self._msgs.pop(0)
            stop.set()
            raise StopAsyncIteration

        async def send(self, data):
            sent.append(data)

    sock._open_ws = lambda token: "wss://fake"
    sock._ws_connect = lambda url: _FakeWS()
    stop = asyncio.Event()
    task = asyncio.create_task(sock.run_channel("hq", "xapp-x", stop=stop))
    await asyncio.wait_for(stop.wait(), timeout=5)
    stop.set()
    await asyncio.sleep(0)
    task.cancel()
    assert any(json.loads(s)["envelope_id"] == "env-1" for s in sent)
    assert log and log[0][1] == "U111"
