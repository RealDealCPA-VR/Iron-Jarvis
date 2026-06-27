"""Inbound webhooks — an external POST triggers an internal handler.

Register a slug with a handler ``Callable[[dict], dict]`` (sync or async) and an
optional HMAC secret. ``dispatch`` looks the handler up, verifies the signature
when a secret was configured, then calls the handler and returns its result.

The handler callable lives in memory; ``register`` also persists a
``WebhookRecord`` so the registration survives as a durable row (e.g. for a
``GET /webhooks`` listing) and can be re-armed after a restart via
:meth:`rehydrate`.

The HMAC secret is resolved at use-time. When a ``secret_resolver`` is injected
(``Callable[[str], str | None]``, e.g. ``secrets.get``) the live secret is
looked up from the persisted ``WebhookRecord.secret_name`` (the real vault key),
so verification keeps working after a daemon restart. With no resolver the
legacy in-memory ``_secrets`` dict is used for back-compat.
"""

from __future__ import annotations

import inspect
import time
from typing import Awaitable, Callable, Optional, Union

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from .models import WebhookRecord
from .security import canonical_bytes, verify, verify_signed

# A handler may return a dict directly or a coroutine resolving to one.
Handler = Callable[[dict], Union[dict, Awaitable[dict]]]
#: resolves a persisted ``secret_name`` (vault key) to its live secret value.
SecretResolver = Callable[[str], Optional[str]]


class InboundWebhooks:
    #: how long a (slug, signature) pair is remembered for replay rejection;
    #: matches the default freshness window of ``verify_signed``.
    _REPLAY_TTL = 300.0

    def __init__(
        self,
        engine: Engine,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.engine = engine
        #: optional vault lookup (e.g. ``secrets.get``); when set, secrets are
        #: resolved from the persisted ``secret_name`` instead of memory.
        self._secret_resolver = secret_resolver
        self._handlers: dict[str, Handler] = {}
        self._secrets: dict[str, str] = {}
        #: short-TTL cache of accepted v2 signatures, keyed "slug:signature".
        self._seen: dict[str, float] = {}

    def _seen_recently(self, key: str) -> bool:
        """True if ``key`` was already accepted; otherwise remember it.

        Expired entries are pruned on access so the cache stays small without a
        background sweeper.
        """
        now = time.monotonic()
        for stale in [k for k, exp in self._seen.items() if exp <= now]:
            del self._seen[stale]
        if key in self._seen:
            return True
        self._seen[key] = now + self._REPLAY_TTL
        return False

    def register(
        self,
        slug: str,
        handler: Handler,
        secret: str | None = None,
        secret_name: str | None = None,
    ) -> str:
        """Register (or replace) the handler for ``slug`` and persist the row.

        ``secret_name`` is the durable vault key persisted on the
        ``WebhookRecord`` so it can be resolved after a restart; pass it whenever
        a secret is configured. ``secret`` is the live secret *value* — kept in
        the in-memory cache for back-compat when no ``secret_resolver`` is
        injected. The persisted ``secret_name`` is the real vault key, never the
        slug.
        """
        self._handlers[slug] = handler
        if secret:
            self._secrets[slug] = secret
        else:
            self._secrets.pop(slug, None)

        # Persist the caller's real vault key. Fall back to the slug only for
        # legacy callers that supply a secret value but no key (so the old
        # in-memory path keeps a non-empty marker).
        persisted_secret_name = secret_name or (slug if secret else "")

        with session_scope(self.engine) as db:
            existing = db.exec(
                select(WebhookRecord).where(WebhookRecord.slug == slug)
            ).first()
            if existing is None:
                db.add(
                    WebhookRecord(
                        slug=slug,
                        direction="inbound",
                        secret_name=persisted_secret_name,
                        enabled=True,
                    )
                )
            else:
                existing.direction = "inbound"
                existing.secret_name = persisted_secret_name
                db.add(existing)
            db.commit()
        return slug

    def rehydrate(
        self, make_default_handler: Callable[[str], Handler]
    ) -> int:
        """Re-arm in-memory handlers from the durable registry after a restart.

        Loads every enabled inbound ``WebhookRecord`` and registers a handler
        for each slug that has no live handler yet, using
        ``make_default_handler(slug)`` to build it (the daemon supplies a factory
        that emits the ``webhook.received`` event, mirroring
        :class:`~iron_jarvis.webhooks.tools.WebhookAddTool`). Secrets are NOT
        cached here — they are resolved at dispatch time via the injected
        ``secret_resolver`` from each record's persisted ``secret_name``.

        Returns the number of slugs rehydrated.
        """
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(WebhookRecord).where(
                    WebhookRecord.direction == "inbound"
                )
            ).all()
            slugs = [r.slug for r in rows if r.enabled]

        count = 0
        for slug in slugs:
            if slug in self._handlers:
                continue
            self._handlers[slug] = make_default_handler(slug)
            count += 1
        return count

    def _resolve_secret(self, slug: str) -> tuple[bool, str | None]:
        """Return ``(secret_expected, live_secret)`` for ``slug``.

        ``secret_expected`` is True when the webhook was registered with a secret
        (a signature is REQUIRED); ``live_secret`` is the resolved value or None.
        Callers must FAIL CLOSED when a secret is expected but unresolved.

        With a ``secret_resolver`` injected, the persisted ``secret_name`` (vault
        key) is read from the record and resolved through it — so a fresh instance
        after a restart can still verify. Otherwise the legacy in-memory cache
        keyed by slug is used.
        """
        if self._secret_resolver is None:
            secret = self._secrets.get(slug)
            return (secret is not None, secret)
        with session_scope(self.engine) as db:
            rec = db.exec(
                select(WebhookRecord).where(WebhookRecord.slug == slug)
            ).first()
            secret_name = rec.secret_name if rec else ""
        if not secret_name:
            return (False, None)
        try:
            return (True, self._secret_resolver(secret_name))
        except Exception:
            # A misconfigured/unavailable vault must not crash dispatch; the
            # secret is expected but unresolved -> the caller rejects (fail closed).
            return (True, None)

    async def dispatch(
        self,
        slug: str,
        body: dict,
        raw: bytes | None = None,
        signature: str | None = None,
        timestamp: str | int | None = None,
    ) -> dict:
        """Verify (if signed) and invoke the handler registered for ``slug``.

        Returns the handler's result on success, or an ``{"ok": False, ...}``
        error dict for an unknown slug or an invalid/missing signature.

        When ``timestamp`` is supplied the hardened v2 path is used: the
        signature must cover the timestamp, fall within the freshness window,
        and not have been seen before (replay protection). With no timestamp the
        legacy body-only :func:`verify` path is used so existing callers keep
        working unchanged.
        """
        handler = self._handlers.get(slug)
        if handler is None:
            return {"ok": False, "error": f"unknown webhook: {slug}"}

        secret_expected, secret = self._resolve_secret(slug)
        if secret:
            payload = raw if raw is not None else canonical_bytes(body)
            if timestamp is not None:
                if not verify_signed(timestamp, payload, secret, signature):
                    return {"ok": False, "error": "invalid webhook signature"}
                if self._seen_recently(f"{slug}:{signature}"):
                    return {"ok": False, "error": "replayed webhook signature"}
            elif not verify(payload, secret, signature):
                return {"ok": False, "error": "invalid webhook signature"}
        elif secret_expected:
            # A secret was configured but can't be resolved (vault outage, or a
            # legacy row that stored the slug instead of the vault key). Reject
            # rather than run the handler on an UNVERIFIED request (fail closed).
            return {"ok": False, "error": "webhook secret unavailable"}

        result = handler(body)
        if inspect.isawaitable(result):
            result = await result
        return result
