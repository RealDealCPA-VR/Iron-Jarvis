"""Tests for the webhooks module (inbound triggers + outbound deliveries).

Fully offline: inbound handlers are plain callables and outbound HTTP is an
injected recorder. Importing ``iron_jarvis.webhooks.models`` at the top registers
the ``WebhookRecord`` table on ``SQLModel.metadata`` before ``init_db`` runs.
"""

from __future__ import annotations

# Register the WebhookRecord table BEFORE init_db creates the schema.
import iron_jarvis.webhooks.models  # noqa: F401

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.events import Event, EventType
from iron_jarvis.webhooks.inbound import InboundWebhooks
from iron_jarvis.webhooks.models import WebhookRecord
from iron_jarvis.webhooks.outbound import OutboundWebhooks
from iron_jarvis.webhooks.security import canonical_bytes, sign, verify
from sqlmodel import select


def _engine(tmp_path):
    engine = make_engine(tmp_path / "webhooks.db")
    init_db(engine)
    return engine


# --- security: sign / verify roundtrip ---------------------------------------


def test_sign_verify_roundtrip():
    payload = b'{"hello":"world"}'
    secret = "s3cr3t"

    signature = sign(payload, secret)
    assert isinstance(signature, str) and len(signature) == 64  # hex sha256

    assert verify(payload, secret, signature) is True
    assert verify(payload, secret, "sha256=" + signature) is True  # prefix ok
    assert verify(payload, secret, "deadbeef") is False  # wrong sig
    assert verify(payload, secret, None) is False  # missing sig
    assert verify(b"tampered", secret, signature) is False  # wrong payload
    # No secret configured -> unauthenticated, accept anything.
    assert verify(payload, "", None) is True


# --- inbound ------------------------------------------------------------------


async def test_inbound_register_and_dispatch_returns_handler_result(tmp_path):
    inbound = InboundWebhooks(_engine(tmp_path))
    seen: list[dict] = []

    def handler(body: dict) -> dict:
        seen.append(body)
        return {"ok": True, "echo": body["x"]}

    inbound.register("greet", handler)
    result = await inbound.dispatch("greet", {"x": 42})

    assert result == {"ok": True, "echo": 42}
    assert seen == [{"x": 42}]


async def test_inbound_async_handler_is_awaited(tmp_path):
    inbound = InboundWebhooks(_engine(tmp_path))

    async def handler(body: dict) -> dict:
        return {"ok": True, "doubled": body["n"] * 2}

    inbound.register("async-hook", handler)
    result = await inbound.dispatch("async-hook", {"n": 21})
    assert result == {"ok": True, "doubled": 42}


async def test_inbound_unknown_slug(tmp_path):
    inbound = InboundWebhooks(_engine(tmp_path))
    result = await inbound.dispatch("nope", {})
    assert result["ok"] is False
    assert "unknown" in result["error"]


async def test_inbound_signature_valid_accepted_bad_or_missing_rejected(tmp_path):
    inbound = InboundWebhooks(_engine(tmp_path))
    inbound.register("secure", lambda body: {"ok": True, "got": body}, secret="topsecret")

    raw = b'{"hello":"world"}'
    body = {"hello": "world"}

    # Valid signature over the raw body -> handler runs.
    good = sign(raw, "topsecret")
    ok = await inbound.dispatch("secure", body, raw=raw, signature=good)
    assert ok == {"ok": True, "got": body}

    # Bad signature -> rejected, handler not run.
    bad = await inbound.dispatch("secure", body, raw=raw, signature="bad")
    assert bad["ok"] is False
    assert "signature" in bad["error"]

    # Missing signature -> rejected.
    missing = await inbound.dispatch("secure", body, raw=raw, signature=None)
    assert missing["ok"] is False
    assert "signature" in missing["error"]


async def test_inbound_register_persists_record(tmp_path):
    engine = _engine(tmp_path)
    inbound = InboundWebhooks(engine)
    inbound.register("greet", lambda b: {"ok": True})

    with session_scope(engine) as db:
        rows = db.exec(select(WebhookRecord).where(WebhookRecord.slug == "greet")).all()
    assert len(rows) == 1
    assert rows[0].direction == "inbound"
    assert rows[0].enabled is True


# --- outbound -----------------------------------------------------------------


def test_outbound_on_event_posts_url_payload_and_signature(tmp_path):
    posted: list[tuple] = []

    def http_post(url, payload, headers):
        posted.append((url, payload, headers))
        return {"status": 200}

    out = OutboundWebhooks(_engine(tmp_path), http_post)
    out.register(
        "notify", "https://example.com/hook", ["session.completed"], secret="abc"
    )

    event = Event(type=EventType.SESSION_COMPLETED, payload={"session_id": "s1"})
    deliveries = out.on_event(event)

    assert len(posted) == 1
    url, payload, headers = posted[0]
    assert url == "https://example.com/hook"
    assert payload["type"] == "session.completed"
    assert payload["payload"] == {"session_id": "s1"}

    # Signature header present and verifies against the posted payload.
    assert "X-IronJarvis-Signature" in headers
    assert verify(canonical_bytes(payload), "abc", headers["X-IronJarvis-Signature"])

    assert len(deliveries) == 1
    assert deliveries[0]["slug"] == "notify"
    assert deliveries[0]["signed"] is True


def test_outbound_no_secret_no_signature_header(tmp_path):
    posted: list[dict] = []
    out = OutboundWebhooks(_engine(tmp_path), lambda u, p, h: posted.append(h))
    out.register("plain", "https://example.com/h", ["session.completed"])

    out.on_event(Event(type=EventType.SESSION_COMPLETED))
    assert posted == [{}]  # called once, no signature header


def test_outbound_does_not_fire_for_non_matching_event(tmp_path):
    posted: list[str] = []
    out = OutboundWebhooks(_engine(tmp_path), lambda u, p, h: posted.append(u))
    out.register("notify", "https://example.com/hook", ["session.completed"])

    deliveries = out.on_event(Event(type=EventType.TOOL_EXECUTED, payload={}))
    assert posted == []
    assert deliveries == []
