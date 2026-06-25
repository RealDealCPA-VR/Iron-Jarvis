"""Outbound webhooks — POST a payload to URLs when matching events fire.

Register a slug with a target URL, the event types it cares about, and an
optional HMAC secret. ``on_event`` is meant to be subscribed to the EventBus:
for every enabled outbound webhook whose ``event_types`` include the event's
type, it POSTs the serialized event via the injected ``http_post`` callable,
adding an ``X-IronJarvis-Signature`` header when a secret is configured.

External HTTP is injected (``http_post(url, payload, headers) -> response``) so
the delivery path is fully offline-testable.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import Event
from .models import WebhookRecord
from .security import canonical_bytes, sign

HttpPost = Callable[[str, dict, dict], Any]


class OutboundWebhooks:
    def __init__(self, engine: Engine, http_post: HttpPost) -> None:
        self.engine = engine
        self.http_post = http_post
        self._secrets: dict[str, str] = {}

    def register(
        self,
        slug: str,
        url: str,
        event_types: list[str],
        secret: str | None = None,
    ) -> str:
        """Register (or update) an outbound delivery and persist its row."""
        if secret:
            self._secrets[slug] = secret
        else:
            self._secrets.pop(slug, None)

        types_json = json.dumps(list(event_types))
        with session_scope(self.engine) as db:
            existing = db.exec(
                select(WebhookRecord).where(WebhookRecord.slug == slug)
            ).first()
            if existing is None:
                db.add(
                    WebhookRecord(
                        slug=slug,
                        direction="outbound",
                        target_url=url,
                        event_types_json=types_json,
                        secret_name=slug if secret else "",
                        enabled=True,
                    )
                )
            else:
                existing.direction = "outbound"
                existing.target_url = url
                existing.event_types_json = types_json
                existing.secret_name = slug if secret else ""
                db.add(existing)
            db.commit()
        return slug

    def on_event(self, event: Event) -> list[dict[str, Any]]:
        """POST the event to every enabled outbound webhook that matches.

        Reads the durable registry (so a disabled row is skipped), pulls the
        live secret from memory, signs the body when present, and returns one
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
            secret = self._secrets.get(rec.slug)
            if secret:
                headers["X-IronJarvis-Signature"] = sign(body_bytes, secret)

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
