"""Outbound webhooks — POST a payload to URLs when matching events fire.

Register a slug with a target URL, the event types it cares about, and an
optional HMAC secret. ``on_event`` is meant to be subscribed to the EventBus:
for every enabled outbound webhook whose ``event_types`` include the event's
type, it POSTs the serialized event via the injected ``http_post`` callable,
adding an ``X-IronJarvis-Signature`` header when a secret is configured.

External HTTP is injected (``http_post(url, payload, headers) -> response``) so
the delivery path is fully offline-testable.

The HMAC secret is resolved at delivery-time. When a ``secret_resolver`` is
injected (``Callable[[str], str | None]``, e.g. ``secrets.get``) the live secret
is looked up from each ``WebhookRecord.secret_name`` (the real vault key), so
deliveries stay signed after a daemon restart. With no resolver the legacy
in-memory ``_secrets`` dict is used for back-compat.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import Event
from .models import WebhookRecord
from .security import canonical_bytes, sign
from .validate import assert_safe_webhook_url

HttpPost = Callable[[str, dict, dict], Any]
#: resolves a persisted ``secret_name`` (vault key) to its live secret value.
SecretResolver = Callable[[str], Optional[str]]


class OutboundWebhooks:
    def __init__(
        self,
        engine: Engine,
        http_post: HttpPost,
        *,
        allow_internal: bool = False,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.engine = engine
        self.http_post = http_post
        #: when False (default) outbound targets resolving to private/loopback/
        #: link-local/etc. addresses are refused (SSRF defense).
        self.allow_internal = allow_internal
        #: optional vault lookup (e.g. ``secrets.get``); when set, secrets are
        #: resolved from the persisted ``secret_name`` instead of memory.
        self._secret_resolver = secret_resolver
        self._secrets: dict[str, str] = {}

    def register(
        self,
        slug: str,
        url: str,
        event_types: list[str],
        secret: str | None = None,
        secret_name: str | None = None,
    ) -> str:
        """Register (or update) an outbound delivery and persist its row.

        ``secret_name`` is the durable vault key persisted on the
        ``WebhookRecord`` (the real key, never the slug) so it can be resolved
        after a restart; pass it whenever a secret is configured. ``secret`` is
        the live secret *value* — kept in the in-memory cache for back-compat
        when no ``secret_resolver`` is injected.

        Raises ``ValueError`` (before persisting anything) if ``url`` is unsafe
        to deliver to -- e.g. a non-http(s) scheme or a host resolving to an
        internal/loopback/metadata address while ``allow_internal`` is False.
        """
        assert_safe_webhook_url(url, allow_internal=self.allow_internal)
        if secret:
            self._secrets[slug] = secret
        else:
            self._secrets.pop(slug, None)

        persisted_secret_name = secret_name or (slug if secret else "")

        types_json = json.dumps(list(event_types))
        from sqlalchemy.exc import IntegrityError

        def _apply(row: "WebhookRecord | None", db) -> None:
            if row is None:
                db.add(
                    WebhookRecord(
                        slug=slug,
                        direction="outbound",
                        target_url=url,
                        event_types_json=types_json,
                        secret_name=persisted_secret_name,
                        enabled=True,
                    )
                )
            else:
                row.direction = "outbound"
                row.target_url = url
                row.event_types_json = types_json
                row.secret_name = persisted_secret_name
                db.add(row)

        with session_scope(self.engine) as db:
            _apply(db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first(), db)
            try:
                db.commit()
            except IntegrityError:  # concurrent first-register of the same slug
                db.rollback()
                _apply(db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first(), db)
                db.commit()
        return slug

    def _resolve_secret(self, rec: WebhookRecord) -> str | None:
        """Return the live HMAC secret for ``rec`` (or ``None`` if unsigned).

        With a ``secret_resolver`` injected, the persisted ``secret_name`` (vault
        key) is resolved through it — so a fresh instance after a restart still
        signs. Otherwise the legacy in-memory cache keyed by slug is used.
        """
        if self._secret_resolver is None:
            return self._secrets.get(rec.slug)
        if not rec.secret_name:
            return None
        try:
            return self._secret_resolver(rec.secret_name)
        except Exception:
            # A misconfigured/unavailable vault must not crash delivery; just
            # send the payload unsigned rather than dropping the event.
            return None

    def on_event(self, event: Event) -> list[dict[str, Any]]:
        """POST the event to every enabled outbound webhook that matches.

        Reads the durable registry (so a disabled row is skipped), resolves the
        live secret (via the injected resolver from the persisted ``secret_name``
        or the in-memory cache), signs the body when present, and returns one
        delivery descriptor per webhook fired.
        """
        payload = event.to_dict()
        body_bytes = canonical_bytes(payload)

        with session_scope(self.engine) as db:
            records = db.exec(
                select(WebhookRecord).where(WebhookRecord.direction == "outbound")
            ).all()

        deliveries: list[dict[str, Any]] = []
        for rec in records:
            if not rec.enabled:
                continue
            try:
                types = json.loads(rec.event_types_json)
            except json.JSONDecodeError:
                types = []
            if event.type not in types:
                continue

            headers: dict[str, str] = {}
            secret = self._resolve_secret(rec)
            if secret:
                headers["X-IronJarvis-Signature"] = sign(body_bytes, secret)

            # Re-validate at delivery time (the persisted row, or the DNS it
            # resolves to, may have changed since registration) to defeat DNS
            # rebinding. A blocked target is skipped, not POSTed.
            try:
                assert_safe_webhook_url(
                    rec.target_url, allow_internal=self.allow_internal
                )
            except ValueError as exc:
                deliveries.append(
                    {
                        "slug": rec.slug,
                        "url": rec.target_url,
                        "signed": bool(secret),
                        "blocked": True,
                        "error": str(exc),
                    }
                )
                continue

            response = self.http_post(rec.target_url, payload, headers)
            deliveries.append(
                {
                    "slug": rec.slug,
                    "url": rec.target_url,
                    "signed": bool(secret),
                    "response": response,
                }
            )
        return deliveries
