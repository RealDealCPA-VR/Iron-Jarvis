"""Slack full-credential setup + signed Events API receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _add_slack(client, name="team"):
    r = client.post(
        "/comm/channels",
        json={
            "type": "slack",
            "name": name,
            "config": {
                "token": "xoxb-test",
                "channel": "#general",
                "signing_secret": SIGNING,
                "app_id": "A0TEST",
                "client_secret": "cs-test",
                "app_token": "xapp-test",
            },
        },
    )
    assert r.status_code == 200, r.text


def _signed_headers(body: bytes, secret: str = SIGNING, ts: str | None = None):
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def test_slack_form_offers_all_credentials(tmp_path):
    client = _client(tmp_path)
    slack = next(
        t for t in client.get("/comm/channel-types").json()["types"] if t["type"] == "slack"
    )
    keys = {f["key"] for f in slack["fields"]}
    assert {
        "webhook_url", "token", "channel", "signing_secret", "app_id",
        "client_id", "client_secret", "verification_token", "app_token",
    } <= keys
    # Sensitive ones are vault-bound.
    secret_keys = {f["key"] for f in slack["fields"] if f["secret"]}
    assert {"token", "signing_secret", "client_secret", "verification_token", "app_token"} <= secret_keys


def test_url_verification_challenge(tmp_path):
    client = _client(tmp_path)
    _add_slack(client)
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()
    r = client.post("/comm/slack/events/team", content=body, headers=_signed_headers(body))
    assert r.status_code == 200 and r.json()["challenge"] == "c-123"


def test_bad_signature_403_and_stale_ts_403(tmp_path):
    client = _client(tmp_path)
    _add_slack(client)
    body = b'{"type":"event_callback","event":{"type":"message","text":"hi"}}'
    bad = _signed_headers(body, secret="wrong-secret")
    assert client.post("/comm/slack/events/team", content=body, headers=bad).status_code == 403
    stale = _signed_headers(body, ts=str(int(time.time()) - 4000))
    assert client.post("/comm/slack/events/team", content=body, headers=stale).status_code == 403


def test_event_accepted_with_valid_signature(tmp_path):
    client = _client(tmp_path)
    _add_slack(client)
    body = json.dumps(
        {"type": "event_callback", "event": {"type": "app_mention", "text": "hello jarvis", "user": "U1"}}
    ).encode()
    r = client.post("/comm/slack/events/team", content=body, headers=_signed_headers(body))
    assert r.status_code == 200 and r.json()["ok"] is True


def test_no_signing_secret_fails_closed(tmp_path):
    client = _client(tmp_path)
    client.post(
        "/comm/channels",
        json={"type": "slack", "name": "bare", "config": {"webhook_url": "https://hooks.slack.com/x"}},
    )
    body = b"{}"
    r = client.post("/comm/slack/events/bare", content=body, headers=_signed_headers(body))
    assert r.status_code == 403
    assert client.post("/comm/slack/events/ghost", content=body).status_code == 404
