"""Pairing: the dedicated credential for the browser add-on, and nothing else (D06, D06A).

Five separate credentials exist in this app after the Browser work, and they never
substitute for one another (plan §7). This module owns exactly one of them: the
**browser pairing token**, minted per paired browser, accepted at ``/browser/ws``
and nowhere else. It is not the install bearer, not a pane token, and not a
provider secret; the cross-rejection is what makes "the add-on can talk to the
daemon" strictly weaker than "anything with the install token can".

Two halves live here, and the split matters:

* **The pending-request registry** is in memory. A pending pairing IS a live
  restricted socket, so it cannot outlive the process that holds it — persisting
  one would resurrect, after a restart, a request whose socket is long gone and
  offer the user a Pair button that mints a credential for nobody. Requests
  expire after :data:`~iron_jarvis.browser.protocol.PAIRING_DEADLINE_S` (5
  minutes, D06A) and expiry is evaluated on every read, so a caller cannot see a
  stale row by simply never asking.
* **The credential** is a :class:`~iron_jarvis.browser.models.BrowserPairing`
  row holding the SHA-256 only.

The rules, each of which is a test:

* **The plaintext token is returned exactly once, from :meth:`PairingStore.mint`.**
  Nothing stores it, nothing logs it, it never enters an event payload, and
  ``POST /browser/pair``'s body is ``{"paired": true}``. Its one appearance on the
  wire is the ``browser.paired`` frame.
* **Verification hashes the candidate.** :meth:`PairingStore.verify` compares
  digests with :func:`hmac.compare_digest`, over hex ASCII, so a token read
  straight off a query string can neither leak by timing nor raise inside the
  comparison (the v1.176.0 ``compare_digest`` non-ASCII lesson, which served a
  bare 500 the dashboard rendered as "daemon offline").
* **A revoked row is refused on every verify.** Forget stamps ``revoked_at``; it
  does not delete, and it does not rely on a caller filtering rows.

**Every method here is BLOCKING** (SQLite plus a hash), so an async caller wraps
it in :func:`asyncio.to_thread` — the daemon is one event loop and a synchronous
DB hop inside a socket handler is the v1.153.1 outage, which the user experienced
as "Daemon offline" rather than as a slow call.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow

from ..core.logging import get_logger
from .errors import BrowserError, BrowserErrorCode
from .models import BrowserPairing
from .protocol import PAIRING_DEADLINE_S, PAIRING_ID_PREFIX

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = get_logger(__name__)

#: Bytes of randomness in a pairing token. 32 bytes is the plan's number (D06A)
#: and ``token_urlsafe`` renders it as ~43 URL-safe characters, which is what
#: lets the token ride ``?token=`` on the socket URL without escaping.
TOKEN_BYTES = 32

#: How many unpaired sockets may be offering a pairing request at once. Reached
#: only by a flood: a real user pairs one browser, and a reconnecting add-on
#: replaces its own offer because ``_prune_locked`` retires the stale one first.
#: Small on purpose — every entry is a button the card would offer the user.
MAX_PENDING_PAIRINGS = 8


def token_sha256(token: str) -> str:
    """The stored form of a pairing token: lowercase SHA-256 hex.

    One function, so the mint side and the verify side cannot disagree about
    encoding. A mismatch here would not raise — it would simply refuse a correct
    token forever, and the user would read that as "pairing does not work".
    """
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PendingPairing:
    """One restricted socket waiting for the user to press Pair.

    Deliberately carries no token field of any kind: the token does not exist yet
    when this record is made, and it must not be attached to it afterwards. A
    frozen dataclass makes "I'll just stash the plaintext here" a type error
    rather than a code review question.
    """

    request_id: str
    first_seen_at: datetime
    extension_id: str = ""

    def row(self) -> dict[str, str]:
        """The ``pending_pairing`` object of ``GET /browser/status``."""
        return {
            "request_id": self.request_id,
            "first_seen_at": self.first_seen_at.isoformat(),
            "extension_id": self.extension_id,
        }


class PairingStore:
    """Mint, verify and revoke the browser pairing credential; track pending asks.

    Args:
        engine: the shared SQLAlchemy engine (``platform.engine``). The store
            creates its own table with ``checkfirst=True`` so it works before
            ``init_db`` has seen this module — the same self-heal
            :class:`~iron_jarvis.agents.remote.RemoteAgentRegistry` uses.
        deadline_s: seconds a pending request stays offerable. Defaults to the
            protocol's :data:`~iron_jarvis.browser.protocol.PAIRING_DEADLINE_S`;
            a test shortens it rather than sleeping, because no test in this
            repository asserts a wall clock.
        clock: ``() -> datetime`` used for expiry and stamps. Injected so a test
            can move time forward deterministically instead of waiting.

    Constructed explicitly by ``platform.py`` (the coordinator's file); nothing
    here reads global state, so a test builds one from any engine.
    """

    def __init__(
        self,
        engine: "Engine",
        *,
        deadline_s: float = PAIRING_DEADLINE_S,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.engine = engine
        self.deadline_s = float(deadline_s)
        self.clock = clock
        #: request_id -> PendingPairing. Guarded by a plain threading lock, not an
        #: asyncio one: a sync FastAPI route runs in the threadpool while the
        #: socket handler runs on the loop, so both kinds of caller touch this.
        self._pending: dict[str, PendingPairing] = {}
        #: Requests that have left the registry with their socket possibly still
        #: open — expired, or cleared by Forget. Drained by :meth:`expired`, which
        #: is how the route learns which sockets to close with 1008. Pruning
        #: straight to nowhere was a real defect caught by
        #: ``test_expired_returns_the_records_so_the_route_can_close_their_sockets``:
        #: any later read of the registry silently swept the record, so the route's
        #: sweep found nothing and the abandoned restricted socket stayed open for
        #: the life of the daemon.
        self._retired: list[PendingPairing] = []
        self._lock = threading.Lock()
        try:
            BrowserPairing.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — already exists / created concurrently
            logger.debug("browserpairing table already present", exc_info=True)

    # --- the pending-request registry ------------------------------------

    def open_request(self, *, extension_id: str = "") -> "PendingPairing | None":
        """Register a new pending pairing ask and return it.

        Called by the socket route the moment an unpaired connection arrives, so
        the ``browser.pairing_required`` frame can carry the id the card will post
        back. Expired requests are pruned here too: a browser that reconnects
        every few seconds would otherwise accumulate offers the user could press
        long after the socket behind them died.

        Bounded by :data:`MAX_PENDING_PAIRINGS`. ``/browser/ws?pairing=1`` needs no
        credential — that is the whole point of the pairing handshake — so without a
        ceiling any local process could open sockets in a loop and grow this registry
        without limit, and each entry ALSO becomes a Pair button the card offers the
        user. The refusal is deliberately not an exception: a flood must not be able
        to make the route raise, so the caller is handed ``None`` and closes that one
        socket while every legitimate pairing already in flight survives.
        """
        with self._lock:
            self._prune_locked()
            if len(self._pending) >= MAX_PENDING_PAIRINGS:
                return None
            record = PendingPairing(
                request_id=new_id(PAIRING_ID_PREFIX.rstrip("_")),
                first_seen_at=self.clock(),
                extension_id=str(extension_id or ""),
            )
            self._pending[record.request_id] = record
        return record

    def pending(self, request_id: str = "") -> PendingPairing | None:
        """The named pending request, or the oldest live one when ``request_id`` is empty.

        Returns ``None`` for an unknown *or expired* id — the caller never has to
        check the deadline itself, which is the point: a second deadline check
        written at a call site is a second deadline, and one of them will be wrong.
        """
        with self._lock:
            self._prune_locked()
            if request_id:
                return self._pending.get(request_id)
            live = sorted(self._pending.values(), key=lambda r: r.first_seen_at)
            return live[0] if live else None

    def pending_rows(self) -> list[dict[str, str]]:
        """Every live pending request, oldest first, in the status-route shape."""
        with self._lock:
            self._prune_locked()
            live = sorted(self._pending.values(), key=lambda r: r.first_seen_at)
        return [record.row() for record in live]

    def expired(self) -> list[PendingPairing]:
        """Prune, then drain and return every request that has left the registry.

        The socket route calls this on its own tick to close the abandoned sockets
        with 1008 (D06A). Returning the records rather than closing them here keeps
        this module free of any socket knowledge — it owns the credential, not the
        transport.

        It drains :attr:`_retired` rather than re-scanning ``_pending``, because
        ANY read of the registry prunes: a version that only looked at ``_pending``
        reported nothing whenever some other call had already swept the record, and
        the abandoned restricted socket then stayed open for the life of the daemon.
        Each retired record is handed out exactly once.
        """
        with self._lock:
            self._prune_locked()
            dead, self._retired = self._retired, []
        return dead

    def drop_request(self, request_id: str) -> bool:
        """Forget one pending request (its socket closed, or it was just paired)."""
        with self._lock:
            return self._pending.pop(request_id, None) is not None

    def _prune_locked(self) -> None:
        """Move expired requests to :attr:`_retired`; never drop one on the floor.

        Retiring rather than deleting is load-bearing: the socket behind an expired
        request may still be open, and only :meth:`expired` hands it to the route
        that can close it with 1008 (D06A).
        """
        cutoff = self.clock() - timedelta(seconds=self.deadline_s)
        for request_id, record in list(self._pending.items()):
            if record.first_seen_at <= cutoff:
                self._pending.pop(request_id, None)
                self._retired.append(record)

    # --- the credential ---------------------------------------------------

    def mint(
        self,
        request_id: str,
        *,
        extension_id: str = "",
        label: str = "",
        allow_replace: bool = False,
    ) -> str:
        """Consume ``request_id`` and return the plaintext token — ONCE, ever.

        The only function in the app that produces this value. It is returned and
        not retained: the row carries :func:`token_sha256` of it, and the caller's
        one legitimate use is the ``browser.paired`` frame.

        Raises:
            BrowserError: ``PAIRING_REQUIRED`` when ``request_id`` is unknown or
                past the deadline — the route maps that to **404**, matching plan
                §3.1. ``AUTHENTICATION_FAILED`` when an unrevoked pairing already
                exists and ``allow_replace`` is false — the route maps that to
                **409**. Two distinct codes because "your Pair button is stale"
                and "this browser is already paired" need different words in the
                card, and one shared code would print the wrong one half the time.
        """
        record = self.pending(request_id)
        if record is None:
            raise BrowserError(BrowserErrorCode.PAIRING_REQUIRED)
        if not allow_replace and self.paired():
            raise BrowserError(
                BrowserErrorCode.AUTHENTICATION_FAILED,
                message=(
                    "This install is already paired with a browser. Press Forget "
                    "on the Browser page in Iron Jarvis before pairing another."
                ),
            )
        token = secrets.token_urlsafe(TOKEN_BYTES)
        # Read the public id into a LOCAL before the commit. Touching
        # ``row.extension_id`` afterwards is a lazy refresh on a detached
        # instance, which raises ``DetachedInstanceError`` from inside the log
        # call — i.e. the line that was supposed to record a successful pairing
        # becomes the reason pairing failed (the v1.229.0 ring-handler lesson,
        # one layer down). Found by ``test_pairing_logs_no_credential``.
        announced = str(extension_id or record.extension_id or "")
        row = BrowserPairing(
            token_sha256=token_sha256(token),
            extension_id=announced,
            label=str(label or ""),
            created_at=self.clock(),
        )
        with session_scope(self.engine) as db:
            db.add(row)
            db.commit()
        self.drop_request(request_id)
        # The TOKEN is deliberately not logged, at any level. A DEBUG line here
        # would put the credential in the user's diagnostics bundle forever.
        logger.info("browser paired (extension_id=%s)", announced or "unknown")
        return token

    def verify(self, token: str) -> BrowserPairing | None:
        """The live pairing this token belongs to, or ``None``.

        Touches ``last_seen_at`` on a hit, so the card can say when the browser
        was last seen without a second liveness store. A revoked row never
        matches: the filter is here, not at the caller, because a caller that
        forgets it authenticates a forgotten browser.
        """
        if not token:
            return None
        digest = token_sha256(token)
        now = self.clock()
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(BrowserPairing).where(BrowserPairing.revoked_at.is_(None))))
            match = None
            for row in rows:
                # Compare every candidate's digest (hex, so always ASCII-safe)
                # rather than filtering by the digest in SQL: a WHERE on the hash
                # is a short-circuiting comparison in the database engine, and
                # constant-time comparison is the property this line is for.
                if hmac.compare_digest(row.token_sha256.encode("ascii"), digest.encode("ascii")):
                    match = row
            if match is None:
                return None
            match.last_seen_at = now
            db.add(match)
            db.commit()
            db.refresh(match)
            db.expunge(match)
            return match

    def paired(self) -> bool:
        """Whether any unrevoked pairing exists — what the card's Paired state reads."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(BrowserPairing).where(BrowserPairing.revoked_at.is_(None))
            ).first()
            return row is not None

    def rows(self) -> list[dict[str, str | None]]:
        """Every pairing, newest first, with no secret material in the payload.

        Returns dicts rather than ORM rows so a route cannot serialise
        ``token_sha256`` by handing the model to a response — the hash is not the
        token, but it is also not something a response body needs.
        """
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(BrowserPairing)))
            for row in rows:
                db.expunge(row)
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [
            {
                "id": row.id,
                "extension_id": row.extension_id,
                "label": row.label,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            }
            for row in rows
        ]

    def revoke(self, pairing_id: str) -> bool:
        """Revoke one pairing by id. Returns whether a live row was stamped."""
        with session_scope(self.engine) as db:
            row = db.exec(select(BrowserPairing).where(BrowserPairing.id == pairing_id)).first()
            if row is None or row.revoked_at is not None:
                return False
            row.revoked_at = self.clock()
            db.add(row)
            db.commit()
        return True

    def revoke_all(self) -> int:
        """Forget every paired browser; returns how many credentials died.

        This is ``POST /browser/forget``. It also clears pending requests: leaving
        one behind would let the user press a Pair button that mints a fresh
        credential immediately after they asked to forget, which reads as Forget
        having failed.
        """
        stamped = 0
        now = self.clock()
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(BrowserPairing).where(BrowserPairing.revoked_at.is_(None))))
            for row in rows:
                row.revoked_at = now
                db.add(row)
                stamped += 1
            if stamped:
                db.commit()
        with self._lock:
            # Retired, not dropped: a restricted socket that was mid-pairing when
            # the user pressed Forget still has to be closed, and only the route
            # can close it.
            self._retired.extend(self._pending.values())
            self._pending.clear()
        return stamped


__all__ = [
    "TOKEN_BYTES",
    "PairingStore",
    "PendingPairing",
    "token_sha256",
]
