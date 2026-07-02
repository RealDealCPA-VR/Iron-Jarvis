"""Wave B: add comm channels + custom integrations (offline)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import CHANNEL_TYPES, EmailChannel


def _client(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path)))


# --- channels ----------------------------------------------------------------


def test_email_channel_registered():
    assert "email" in CHANNEL_TYPES
    assert CHANNEL_TYPES["email"] is EmailChannel


def test_email_channel_validates_config():
    ch = EmailChannel({"host": "", "from_addr": "a@b.c", "to_addr": "d@e.f"})
    r = ch.send("hi")
    assert r["ok"] is False and "host" in r["detail"]


def test_channel_types_endpoint(tmp_path):
    types = {t["type"] for t in _client(tmp_path).get("/comm/channel-types").json()["types"]}
    assert {"slack", "discord", "telegram", "email"} <= types


def test_add_slack_channel_live_and_persisted(tmp_path):
    client = _client(tmp_path)
    r = client.post("/comm/channels", json={
        "name": "team", "type": "slack",
        "config": {"webhook_url": "https://hooks.slack.com/services/x/y/z"},
    })
    assert r.status_code == 200 and r.json()["added"] is True
    live = {c["name"]: c for c in client.get("/comm/channels").json()["channels"]}
    assert "team" in live and live["team"]["type"] == "slack"


def test_add_telegram_channel_stores_token_in_vault(tmp_path):
    client = _client(tmp_path)
    r = client.post("/comm/channels", json={
        "name": "tg", "type": "telegram",
        "config": {"token": "123:ABC-secret", "chat_id": "999"},
    })
    assert r.status_code == 200
    # The token was routed to the vault (never left in the channel config).
    secrets = {s["name"] for s in client.get("/secrets").json()["secrets"]}
    assert "channel_tg_token" in secrets


def test_add_channel_rejects_unknown_type(tmp_path):
    r = _client(tmp_path).post("/comm/channels", json={"name": "x", "type": "carrier_pigeon", "config": {}})
    assert r.status_code == 400


def test_delete_channel(tmp_path):
    client = _client(tmp_path)
    client.post("/comm/channels", json={"name": "d", "type": "discord",
                                        "config": {"webhook_url": "https://discord.com/api/webhooks/1/2"}})
    assert client.delete("/comm/channels/d").json()["removed"] is True
    assert "d" not in {c["name"] for c in client.get("/comm/channels").json()["channels"]}


# --- integrations ------------------------------------------------------------


def test_add_custom_integration_appears_and_tests(tmp_path):
    client = _client(tmp_path)
    r = client.post("/integrations", json={
        "name": "My API", "base_url": "https://api.example.com", "description": "internal"})
    assert r.status_code == 200 and r.json()["id"] == "my_api"
    ids = {i["id"] for i in client.get("/integrations").json()["integrations"]}
    assert "my_api" in ids


def test_add_integration_requires_name_and_url(tmp_path):
    client = _client(tmp_path)
    assert client.post("/integrations", json={"name": "", "base_url": "x"}).status_code == 400
    assert client.post("/integrations", json={"name": "n", "base_url": ""}).status_code == 400


def test_custom_integration_survives_restart(tmp_path):
    client = _client(tmp_path)
    client.post("/integrations", json={"name": "Persisted", "base_url": "https://p.example.com"})
    # A fresh app on the SAME home re-registers the custom integration at boot.
    fresh = _client(tmp_path)
    ids = {i["id"] for i in fresh.get("/integrations").json()["integrations"]}
    assert "persisted" in ids
