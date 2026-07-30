"""The Notifications ease batch (v1.118.0).

REQUESTED: make it extremely easy to connect to the desired destination type.
Four backend pieces carry that: a zero-config "This PC" destination that exists
before any setup; Telegram chat-id auto-detection (the classic wall); test
results that PERSIST so green provably means working; and per-destination event
routing — which also fixes a real fan-out bug (auto-alerts only ever reached
the DEFAULT channel; a second connected destination silently got nothing).
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import Notifier
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


@pytest.fixture(autouse=True)
def _clean_sink():
    """The sink is process-global (class attribute) — never leak between tests."""
    old = DesktopChannel.sink
    yield
    DesktopChannel.sink = old


# --- N1: This PC -------------------------------------------------------------


def test_this_pc_exists_before_any_setup(client):
    rows = client.get("/comm/channels").json()["channels"]
    row = next((r for r in rows if r["name"] == "this-pc"), None)
    assert row is not None
    assert row["builtin"] is True
    assert row["type"] == "desktop"


def test_the_offline_default_channel_did_not_move(client):
    """this-pc is ADDED, not promoted: mock stays the default so nothing that
    relied on the old default silently reroutes."""
    assert client.app.state.platform.notifier.default_channel == "mock"


def test_desktop_send_fails_honestly_without_the_daemon_sink():
    """'Configured' must never silently mean 'sent nowhere' — CLI runs and unit
    tests have no sink, and the channel says so instead of pretending."""
    DesktopChannel.sink = None
    r = DesktopChannel({"type": "desktop"}).send("hello")
    assert r["ok"] is False
    assert "daemon" in r["detail"]


def test_desktop_send_delivers_through_the_sink():
    got = []
    DesktopChannel.sink = staticmethod(lambda title, msg: got.append((title, msg)))
    r = DesktopChannel({"type": "desktop"}).send("review waiting", title="Iron Jarvis")
    assert r["ok"] is True
    assert got == [("Iron Jarvis", "review waiting")]


# --- N5: per-destination routing (and the fan-out fix) ------------------------


def _notifier_with(channels: dict[str, MockChannel]) -> Notifier:
    n = Notifier(event_types={"review.requested", "workflow.completed"})
    for name, ch in channels.items():
        n.add_channel(name, ch)
    return n


def test_alerts_fan_out_to_every_destination_not_just_the_default():
    a, b = MockChannel(), MockChannel()
    n = _notifier_with({"a": a, "b": b})
    n.on_event({"type": "review.requested", "payload": {}})
    assert len(a.sent) == 1
    assert len(b.sent) == 1  # before v1.118.0 this was 0 — the silent gap


def test_a_destination_can_narrow_what_it_receives():
    phone, office = MockChannel({"events": ["review.requested"]}), MockChannel()
    n = _notifier_with({"phone": phone, "office": office})
    n.on_event({"type": "workflow.completed", "payload": {}})
    n.on_event({"type": "review.requested", "payload": {}})
    assert len(phone.sent) == 1  # only the approval ping
    assert len(office.sent) == 2  # empty events = everything (old behaviour)


def test_unknown_event_names_are_rejected_at_add_time(client):
    r = client.post(
        "/comm/channels",
        json={
            "name": "team",
            "type": "discord",
            "config": {"webhook_url": "https://discord.com/api/webhooks/x", "events": ["nope"]},
        },
    )
    assert r.status_code == 400
    assert "unknown alert event" in r.json()["detail"]


def test_valid_events_persist_and_are_listed(client):
    r = client.post(
        "/comm/channels",
        json={
            "name": "team",
            "type": "discord",
            "config": {
                "webhook_url": "https://discord.com/api/webhooks/x",
                "events": ["review.requested"],
            },
        },
    )
    assert r.status_code == 200
    row = next(x for x in client.get("/comm/channels").json()["channels"] if x["name"] == "team")
    assert row["events"] == ["review.requested"]


# --- N4: test results persist -------------------------------------------------


def test_a_test_result_sticks_to_the_configured_row(client, monkeypatch):
    client.post(
        "/comm/channels",
        json={"name": "team", "type": "discord",
              "config": {"webhook_url": "https://discord.com/api/webhooks/x"}},
    )
    # The discord channel would POST for real — make it succeed offline.
    ch = client.app.state.platform.notifier.get("team")
    monkeypatch.setattr(ch, "send", lambda msg, **kw: {"ok": True, "detail": "sent"})
    assert client.post("/comm/channels/team/test").json()["ok"] is True
    row = next(x for x in client.get("/comm/channels").json()["channels"] if x["name"] == "team")
    assert row["last_test_ok"] is True
    assert row["last_test_at"]


def test_a_failed_test_is_recorded_too(client, monkeypatch):
    client.post(
        "/comm/channels",
        json={"name": "team", "type": "discord",
              "config": {"webhook_url": "https://discord.com/api/webhooks/x"}},
    )
    ch = client.app.state.platform.notifier.get("team")
    monkeypatch.setattr(ch, "send", lambda msg, **kw: {"ok": False, "detail": "404"})
    client.post("/comm/channels/team/test")
    row = next(x for x in client.get("/comm/channels").json()["channels"] if x["name"] == "team")
    assert row["last_test_ok"] is False


# --- N3: telegram chat-id auto-detect ----------------------------------------


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_detect_chat_lists_who_messaged_the_bot(client, monkeypatch):
    import iron_jarvis.daemon.routes.comm as comm_routes  # noqa: F401

    def fake_get(url, params):
        assert "getUpdates" in url
        return _Resp({
            "ok": True,
            "result": [
                {"message": {"chat": {"id": 111, "first_name": "V", "last_name": "R"}}},
                {"message": {"chat": {"id": 111, "first_name": "V", "last_name": "R"}}},
                {"message": {"chat": {"id": -222, "title": "Firm group"}}},
            ],
        })

    monkeypatch.setattr("iron_jarvis.comm.base.httpx_get", fake_get)
    monkeypatch.setattr("iron_jarvis.comm.httpx_get", fake_get)
    r = client.post("/comm/telegram/detect-chat", json={"token": "123:abc"})
    assert r.status_code == 200
    chats = r.json()["chats"]
    assert {"id": 111, "label": "V R"} in chats
    assert {"id": -222, "label": "Firm group"} in chats
    assert len(chats) == 2  # deduped


def test_detect_chat_rejects_a_bad_token_with_botfather_pointer(client, monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.comm.httpx_get",
        lambda url, params: _Resp({"ok": False, "description": "Unauthorized"}),
    )
    r = client.post("/comm/telegram/detect-chat", json={"token": "bad"})
    assert r.status_code == 400
    assert "@BotFather" in r.json()["detail"]


def test_detect_chat_requires_a_token(client):
    assert client.post("/comm/telegram/detect-chat", json={}).status_code == 400
