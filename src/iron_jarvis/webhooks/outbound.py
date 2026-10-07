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
import threading
import time
from typing import Any, Callable, Optional

from sqlalchemy import Engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session as _OrmSession
from sqlalchemy.orm import object_session
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import Event
from .models import WebhookRecord
from .security import canonical_bytes, sign
from .validate import assert_safe_webhook_url

HttpPost = Callable[[str, dict, dict], Any]
#: the plain-language refusal for an outbound webhook with no event types
#: (surfaced verbatim by ``POST /webhooks`` and the ``webhook_add`` tool).
EMPTY_EVENT_TYPES = (
    "Pick at least one event type for an outbound webhook, for example "
    "session.completed. With none listed, nothing would ever be sent."
)
#: resolves a persisted ``secret_name`` (vault key) to its live secret value.
SecretResolver = Callable[[str], Optional[str]]


# --------------------------------------------------------------------------- #
# The subscription index's invalidation (v1.311.0).
# --------------------------------------------------------------------------- #
# `on_event` runs for EVERY event the daemon publishes (3-5 per agent step) and
# used to open a session, SELECT every outbound row and serialise the event
# before checking whether any webhook wanted that type — on a default install,
# which has none. It now answers from an in-memory index, and the index must
# never outlive the rows it was read from. So it is keyed to a process-wide
# GENERATION that ANY ORM write to `WebhookRecord` bumps — not only this
# class's own methods: `InboundWebhooks.register` writes the same table, and a
# route or an import may too (verifier adjustment). Two bumps per write:
#
# * at FLUSH (mapper after_insert/update/delete), so a reader that starts after
#   the write is visible to its own session rebuilds; and
# * at COMMIT (the session was marked at flush), because a reader on ANOTHER
#   thread that rebuilt between the flush and the commit read the OLD committed
#   rows under the NEW generation — the commit-time bump makes its next event
#   rebuild again. A reader captures the generation BEFORE it queries.
#
# Bulk ORM statements (`session.execute(update(WebhookRecord)…)`) mark the
# session through `do_orm_execute`. A write that bypasses the ORM entirely (a
# restored database file, an sqlite3 shell) is caught by INDEX_MAX_AGE_S.
_GEN_LOCK = threading.Lock()
_GENERATION = 0
_DIRTY_KEY = "ij_webhook_index_dirty"
#: Safety net for writes no ORM listener can see: the index is re-read at
#: least this often (one cheap query per minute at most, never per event).
INDEX_MAX_AGE_S = 60.0


def _bump_generation() -> None:
    global _GENERATION
    with _GEN_LOCK:
        _GENERATION += 1


def _index_generation() -> int:
    with _GEN_LOCK:
        return _GENERATION


def _on_row_write(_mapper: Any, _connection: Any, target: Any) -> None:
    _bump_generation()
    try:
        sess = object_session(target)
        if sess is not None:
            sess.info[_DIRTY_KEY] = True
    except Exception:  # noqa: BLE001 — invalidation bookkeeping never fails a write
        pass


def _on_orm_execute(state: Any) -> None:
    try:
        if not (state.is_update or state.is_delete or state.is_insert):
            return
        mapper = state.bind_mapper
        if mapper is not None and mapper.class_ is WebhookRecord:
            _bump_generation()
            state.session.info[_DIRTY_KEY] = True
    except Exception:  # noqa: BLE001
        pass


def _on_commit(session: Any) -> None:
    if session.info.pop(_DIRTY_KEY, False):
        _bump_generation()


for _evt in ("after_insert", "after_update", "after_delete"):
    sa_event.listen(WebhookRecord, _evt, _on_row_write)
sa_event.listen(_OrmSession, "do_orm_execute", _on_orm_execute)
sa_event.listen(_OrmSession, "after_commit", _on_commit)


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
        #: (generation, read_at, {event_type: [enabled outbound rows]}) — see
        #: the invalidation note above. None until the first read.
        self._index: "tuple[int, float, dict[str, list[WebhookRecord]]] | None" = None
        self._index_lock = threading.Lock()

    def warm(self) -> None:
        """Read the subscription index now (boot), so the first event does not
        pay for it. Never raises — an unreadable table is re-tried lazily."""
        try:
            self._subscribers()
        except Exception:  # noqa: BLE001
            self._index = None

    def _subscribers(self) -> "dict[str, list[WebhookRecord]]":
        """``{event_type: [enabled outbound rows]}``, re-read only when a
        ``WebhookRecord`` write bumped the generation (or the safety-net age
        passed). Disabled rows are left out here, exactly as ``on_event``
        always skipped them."""
        gen = _index_generation()
        now = time.monotonic()
        cached = self._index
        if cached is not None and cached[0] == gen and now - cached[1] < INDEX_MAX_AGE_S:
            return cached[2]
        with self._index_lock:
            cached = self._index
            gen = _index_generation()  # captured BEFORE the read (see the note above)
            if cached is not None and cached[0] == gen and now - cached[1] < INDEX_MAX_AGE_S:
                return cached[2]
            with session_scope(self.engine) as db:
                records = db.exec(
                    select(WebhookRecord).where(WebhookRecord.direction == "outbound")
                ).all()
            index: dict[str, list[WebhookRecord]] = {}
            for rec in records:
                if not rec.enabled:
                    continue
                try:
                    types = json.loads(rec.event_types_json)
                except (json.JSONDecodeError, TypeError):
                    types = []
                # ONE entry per (type, record) — a row that lists a type twice
                # is still one subscription, and must get one POST, as before
                # the index (v1.311.0 review).
                for t in dict.fromkeys(str(x) for x in (types if isinstance(types, list) else [])):
                    index.setdefault(t, []).append(rec)
            self._index = (gen, time.monotonic(), index)
            return index

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
        internal/loopback/metadata address while ``allow_internal`` is False --
        or if ``event_types`` is empty: ``on_event`` delivers only when the
        event's type is IN the list, so an empty list would match nothing and
        the webhook would silently receive no events (v1.292.0, platform-06).
        """
        if not [t for t in event_types if str(t).strip()]:
            raise ValueError(EMPTY_EVENT_TYPES)
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

    def unregister(self, slug: str) -> bool:
        """Remove the outbound delivery ``slug``: its durable row and the
        in-memory secret cache entry. The vault secret itself is left alone --
        ``secret_name`` may be shared with another webhook. Returns True when a
        row was removed (v1.292.0, platform-06: a webhook can be deleted)."""
        self._secrets.pop(slug, None)
        with session_scope(self.engine) as db:
            row = db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
        return True

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
        # v1.311.0: the in-memory index answers "does any webhook want this
        # type?" BEFORE anything is serialised or queried — the common answer
        # (no outbound webhook at all, or none for this type) costs a dict
        # lookup instead of a session, a SELECT and canonical JSON per event.
        records = list(self._subscribers().get(event.type) or ())
        if not records:
            return []
        payload = event.to_dict()
        body_bytes = canonical_bytes(payload)

        deliveries: list[dict[str, Any]] = []
        for rec in records:
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
