"""Inbound webhooks — an external POST triggers an internal handler.

Register a slug with a handler ``Callable[[dict], dict]`` (sync or async) and an
optional HMAC secret. ``dispatch`` looks the handler up, verifies the signature
when a secret was configured, then calls the handler and returns its result.

The handler callable and secret live in memory; ``register`` also persists a
``WebhookRecord`` so the registration survives as a durable row (e.g. for a
``GET /webhooks`` listing).
"""

from __future__ import annotations

import inspect
from typing import Awaitable, Callable, Union

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from .models import WebhookRecord
from .security import canonical_bytes, verify

# A handler may return a dict directly or a coroutine resolving to one.
Handler = Callable[[dict], Union[dict, Awaitable[dict]]]


class InboundWebhooks:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._handlers: dict[str, Handler] = {}
        self._secrets: dict[str, str] = {}

    def register(
        self, slug: str, handler: Handler, secret: str | None = None
    ) -> str:
        """Register (or replace) the handler for ``slug`` and persist the row."""
        self._handlers[slug] = handler
        if secret:
            self._secrets[slug] = secret
        else:
            self._secrets.pop(slug, None)

        with session_scope(self.engine) as db:
            existing = db.exec(
                select(WebhookRecord).where(WebhookRecord.slug == slug)
            ).first()
            if existing is None:
                db.add(
                    WebhookRecord(
                        slug=slug,
                        direction="inbound",
                        secret_name=slug if secret else "",
                        enabled=True,
                    )
                )
            else:
                existing.direction = "inbound"
                existing.secret_name = slug if secret else ""
                db.add(existing)
            db.commit()
        return slug

    async def dispatch(
        self,
        slug: str,
        body: dict,
        raw: bytes | None = None,
        signature: str | None = None,
    ) -> dict:
        """Verify (if signed) and invoke the handler registered for ``slug``.

        Returns the handler's result on success, or an ``{"ok": False, ...}``
        error dict for an unknown slug or an invalid/missing signature.
        """
        handler = self._handlers.get(slug)
        if handler is None:
            return {"ok": False, "error": f"unknown webhook: {slug}"}

        secret = self._secrets.get(slug)
        if secret:
            payload = raw if raw is not None else canonical_bytes(body)
            if not verify(payload, secret, signature):
                return {"ok": False, "error": "invalid webhook signature"}

        result = handler(body)
        if inspect.isawaitable(result):
            result = await result
        return result
