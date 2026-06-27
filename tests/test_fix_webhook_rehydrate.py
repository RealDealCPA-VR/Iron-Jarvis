"""Regression tests: webhooks survive a daemon restart.

A restart drops all in-memory state (registered handlers + the cached secret
values) but keeps the SQLite registry. These tests build a *second*
``InboundWebhooks``/``OutboundWebhooks`` on the SAME engine to simulate that
restart and assert that:

  * dispatch fails ("unknown webhook") until :meth:`InboundWebhooks.rehydrate`
    re-arms the handlers from the durable rows, then succeeds;
  * ``register`` persists the caller's REAL ``secret_name`` (the vault key), not
    the slug;
  * with a ``secret_resolver`` injected, a fresh instance verifies inbound
    signatures and signs outbound deliveries — proving the secret is resolved
    from the persisted ``secret_name`` rather than memory.

Fully offline. Importing ``iron_jarvis.webhooks.models`` registers the
``WebhookRecord`` table before ``init_db`` creates the schema.
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


def _make_default_handler(seen: list[dict]):
    """Mirror the daemon's factory: build a webhook.received-style handler.

    Returns a ``make_default_handler(slug) -> handler`` factory; the handler
    records the body (standing in for ``event_bus.publish`` in this offline
    test) and returns ``{"ok": True}`` like the real default handler.
    """

    def make(slug: str):
        async def handler(body, _slug=slug):
            seen.append({"slug": _slug, "body": body})
            return {"ok": True}

        return handler

    return make


# --- rehydrate after restart --------------------------------------------------


async def test_dispatch_fails_until_rehydrate_then_succeeds(tmp_path):
    engine = _engine(tmp_path)

    # First boot: register a handler.
    first = InboundWebhooks(engine)
    first.register("greet", lambda b: {"ok": True, "echo": b["x"]})

    # Restart: a brand-new instance on the SAME engine has no live handler.
    second = InboundWebhooks(engine)
    before = await second.dispatch("greet", {"x": 1})
    assert before["ok"] is False
    assert "unknown" in before["error"]

    # Rehydrate from the durable rows re-arms the handler.
    seen: list[dict] = []
    count = second.rehydrate(_make_default_handler(seen))
    assert count == 1

    after = await second.dispatch("greet", {"x": 1})
    assert after == {"ok": True}
    assert seen == [{"slug": "greet", "body": {"x": 1}}]


async def test_rehydrate_skips_disabled_and_outbound_rows(tmp_path):
    engine = _engine(tmp_path)
    inbound = InboundWebhooks(engine)
    inbound.register("on", lambda b: {"ok": True})
    inbound.register("off", lambda b: {"ok": True})

    # Disable one inbound row, and add an outbound row that must be ignored.
    with session_scope(engine) as db:
        rec = db.exec(
            select(WebhookRecord).where(WebhookRecord.slug == "off")
        ).first()
        rec.enabled = False
        db.add(rec)
        db.add(
            WebhookRecord(slug="out", direction="outbound", enabled=True)
        )
        db.commit()

    fresh = InboundWebhooks(engine)
    count = fresh.rehydrate(_make_default_handler([]))
    assert count == 1  # only the enabled inbound "on" row

    assert (await fresh.dispatch("on", {}))["ok"] is True
    assert (await fresh.dispatch("off", {}))["ok"] is False
    assert (await fresh.dispatch("out", {}))["ok"] is False


# --- real secret_name persisted (not the slug) --------------------------------


def test_inbound_register_persists_real_secret_name(tmp_path):
    engine = _engine(tmp_path)
    inbound = InboundWebhooks(engine)
    inbound.register(
        "greet",
        lambda b: {"ok": True},
        secret="live-value",
        secret_name="vault:webhook-greet",
    )

    with session_scope(engine) as db:
        rec = db.exec(
            select(WebhookRecord).where(WebhookRecord.slug == "greet")
        ).first()
    assert rec.secret_name == "vault:webhook-greet"
    assert rec.secret_name != "greet"


def test_outbound_register_persists_real_secret_name(tmp_path):
    engine = _engine(tmp_path)
    out = OutboundWebhooks(engine, lambda u, p, h: None)
    out.register(
        "notify",
        "https://example.com/hook",
        ["session.completed"],
        secret="live-value",
        secret_name="vault:webhook-notify",
    )

    with session_scope(engine) as db:
        rec = db.exec(
            select(WebhookRecord).where(WebhookRecord.slug == "notify")
        ).first()
    assert rec.secret_name == "vault:webhook-notify"
    assert rec.secret_name != "notify"


# --- secret resolved from vault after a restart -------------------------------


async def test_inbound_verifies_via_resolver_after_restart(tmp_path):
    engine = _engine(tmp_path)
    vault = {"vault:hook": "topsecret"}

    # First boot registers with the real vault key; no live secret cached on the
    # fresh instance below.
    first = InboundWebhooks(engine, secret_resolver=vault.get)
    first.register(
        "secure",
        lambda b: {"ok": True, "got": b},
        secret_name="vault:hook",
    )

    # Restart: fresh instance, only the resolver + durable row available.
    second = InboundWebhooks(engine, secret_resolver=vault.get)
    seen: list[dict] = []
    assert second.rehydrate(_make_default_handler(seen)) == 1

    raw = b'{"hello":"world"}'
    body = {"hello": "world"}
    good = sign(raw, "topsecret")

    ok = await second.dispatch("secure", body, raw=raw, signature=good)
    assert ok == {"ok": True}  # resolver-resolved secret verified the signature

    bad = await second.dispatch("secure", body, raw=raw, signature="bad")
    assert bad["ok"] is False
    assert "signature" in bad["error"]


def test_outbound_signs_via_resolver_after_restart(tmp_path):
    engine = _engine(tmp_path)
    vault = {"vault:hook": "abc"}

    # First boot registers with the real vault key only.
    first = OutboundWebhooks(
        engine, lambda u, p, h: None, secret_resolver=vault.get
    )
    first.register(
        "notify",
        "https://example.com/hook",
        ["session.completed"],
        secret_name="vault:hook",
    )

    # Restart: fresh instance with an empty in-memory secret cache.
    posted: list[tuple] = []

    def http_post(url, payload, headers):
        posted.append((url, payload, headers))
        return {"status": 200}

    second = OutboundWebhooks(engine, http_post, secret_resolver=vault.get)
    deliveries = second.on_event(
        Event(type=EventType.SESSION_COMPLETED, payload={"session_id": "s1"})
    )

    assert len(posted) == 1
    _url, payload, headers = posted[0]
    assert deliveries[0]["signed"] is True
    assert "X-IronJarvis-Signature" in headers
    # Signature verifies against the resolver-resolved secret.
    assert verify(
        canonical_bytes(payload), "abc", headers["X-IronJarvis-Signature"]
    )
