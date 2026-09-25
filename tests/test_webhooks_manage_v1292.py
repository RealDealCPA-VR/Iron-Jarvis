"""v1.292.0 (platform-06) — an outbound webhook with no event types is refused,
and a webhook can be removed.

Before: ``POST /webhooks`` accepted an outbound registration with
``event_types: []`` (the page even said "Leave blank for all events" and listed
the row as "all") while ``OutboundWebhooks.on_event`` needs ``event.type in
types``, so the integration silently received nothing. And nothing could be
taken back out: ``DELETE /webhooks/{slug}`` answered 405, the inbound handler
and the outbound secret cache lived for good.

Pinned here through the REAL ``create_app`` (real routes, real registries, the
real event bus; only outbound HTTP and the SSRF DNS check are stubbed):

 - POST outbound + empty/blank event types -> 400 with a sentence a
   non-programmer can act on, and NO row is saved;
 - the ``webhook_add`` tool refuses the same way (``ok: False``);
 - DELETE removes the row from GET, pops the inbound in-memory handler so a
   later POST to that slug is a 404, pops the outbound ``_secrets`` cache entry
   and stops delivery — and leaves the vault secret alone;
 - DELETE on an unknown slug -> 404.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    return client, platform


def _slugs(client) -> set[str]:
    return {w["slug"] for w in client.get("/webhooks").json()["webhooks"]}


# ---- empty event types are refused ----------------------------------------


@pytest.mark.parametrize("types", [[], ["", "  "]])
def test_outbound_with_no_event_types_is_refused_with_a_plain_sentence(tmp_path, types):
    client, _platform = _client(tmp_path)
    r = client.post(
        "/webhooks",
        json={
            "slug": "zap",
            "direction": "outbound",
            "target_url": "https://example.com/hook",
            "event_types": types,
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    # A sentence, not a code: names what to do and gives an example.
    assert "at least one event type" in detail
    assert "session.completed" in detail
    assert "nothing would ever be sent" in detail
    # Nothing was saved: the page must not list a dead row.
    assert "zap" not in _slugs(client)


async def test_webhook_add_tool_refuses_empty_event_types_too(tmp_path):
    """The agent lane (``webhook_add``) goes through the same ``register`` and
    must refuse the same way, not save a row that never fires."""
    from iron_jarvis.webhooks.tools import WebhookAddTool

    _client_, platform = _client(tmp_path)
    tool = WebhookAddTool(platform)
    res = await tool.execute(
        {"slug": "agent-out", "direction": "outbound", "target_url": "https://example.com/h"},
        None,
    )
    assert res.ok is False
    assert "at least one event type" in (res.error or "")
    assert "agent-out" not in _slugs(_client_)


def test_outbound_with_an_event_type_still_registers_and_delivers(tmp_path, monkeypatch):
    """Anti-vacuity control: the refusal is only for the EMPTY list."""
    client, platform = _client(tmp_path)
    posted: list[str] = []
    platform.outbound_webhooks.http_post = (
        lambda url, payload, headers: posted.append(payload["type"]) or {"ok": True}
    )
    monkeypatch.setattr(
        "iron_jarvis.webhooks.outbound.assert_safe_webhook_url", lambda *a, **k: None
    )
    r = client.post(
        "/webhooks",
        json={
            "slug": "zap",
            "direction": "outbound",
            "target_url": "https://example.com/hook",
            "event_types": ["session.completed"],
        },
    )
    assert r.status_code == 200, r.text
    asyncio.run(platform.event_bus.publish("session.completed", {"status": "completed"}))
    assert posted == ["session.completed"]


# ---- DELETE /webhooks/{slug} -------------------------------------------------


def test_delete_inbound_removes_row_and_live_handler(tmp_path):
    client, platform = _client(tmp_path)
    platform.secrets.set("gh_secret", "shh")
    assert client.post(
        "/webhooks", json={"slug": "gh", "direction": "inbound", "secret_name": "gh_secret"}
    ).status_code == 200
    assert "gh" in _slugs(client)
    assert "gh" in platform.inbound_webhooks._handlers

    r = client.delete("/webhooks/gh")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "slug": "gh", "direction": "inbound"}

    # Gone from the listing AND from the in-memory registry...
    assert "gh" not in _slugs(client)
    assert "gh" not in platform.inbound_webhooks._handlers
    # ...so a later inbound POST is a 404, not a 200 into a dead handler.
    r2 = client.post("/webhooks/gh", json={"ping": 1})
    assert r2.status_code == 404, r2.text
    assert r2.json()["rejected"] == "unknown_slug"
    # The vault secret is NOT touched (secret_name may be shared).
    assert platform.secrets.get("gh_secret") == "shh"


def test_delete_outbound_pops_secret_cache_and_stops_delivery(tmp_path, monkeypatch):
    client, platform = _client(tmp_path)
    posted: list[str] = []
    platform.outbound_webhooks.http_post = (
        lambda url, payload, headers: posted.append(payload["type"]) or {"ok": True}
    )
    monkeypatch.setattr(
        "iron_jarvis.webhooks.outbound.assert_safe_webhook_url", lambda *a, **k: None
    )
    platform.secrets.set("hook_secret", "shh")
    assert client.post(
        "/webhooks",
        json={
            "slug": "out",
            "direction": "outbound",
            "target_url": "https://example.com/hook",
            "event_types": ["session.completed"],
            "secret_name": "hook_secret",
        },
    ).status_code == 200
    assert platform.outbound_webhooks._secrets.get("out") == "shh"

    r = client.delete("/webhooks/out")
    assert r.status_code == 200, r.text
    assert r.json()["direction"] == "outbound"
    assert "out" not in _slugs(client)
    assert "out" not in platform.outbound_webhooks._secrets
    assert platform.secrets.get("hook_secret") == "shh"

    # Nothing is delivered to a removed webhook.
    asyncio.run(platform.event_bus.publish("session.completed", {"status": "completed"}))
    assert posted == []


def test_delete_unknown_slug_is_404(tmp_path):
    client, _platform = _client(tmp_path)
    r = client.delete("/webhooks/nope")
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]
