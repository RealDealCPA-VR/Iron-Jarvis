"""The ``Mcp-Session-Id`` lifecycle for the server half (plan 12.1).

Streamable HTTP has no connection to hang state on: every call is a fresh POST.
The session id is the thread that ties them together, and the repository's own
client already pins how it must behave —
``mcp/client.py:HttpTransport._handshake`` captures ``mcp-session-id`` from the
``initialize`` RESPONSE HEADERS and ``_base_headers`` sends it back on every
later request. So:

* the id is minted during ``initialize`` and returned as a **response header**,
  never only in the body (a body-only id is invisible to that client);
* the header name is compared case-insensitively, because the client reads
  ``"mcp-session-id"`` lowercase while the spec spells it ``Mcp-Session-Id``, and
  HTTP header names are case-insensitive on the wire but *not* in a plain dict.

Three properties that are not obvious, each a test:

* **A session id is not a credential.** The pane token is. The id is bound to the
  pane that opened it and :meth:`McpSessionRegistry.get` refuses it when
  presented alongside a token for a DIFFERENT pane — otherwise pane A's harness
  could continue pane B's conversation by guessing an id, and the capability
  filter would then be computed from the wrong pane.
* **Memory only, and bounded.** A session outliving the process would name a pane
  that no longer exists; a registry that only ever grew would be a slow leak in a
  daemon the user leaves running for weeks. Idle sessions are pruned on every
  read, so a caller cannot see a stale row by simply never asking (the pairing
  store's rule, for the same reason).
* **Closing a pane closes its sessions.** :meth:`revoke_pane` is called from the
  same chokepoints as
  :meth:`~iron_jarvis.browser.panetokens.PaneTokenStore.revoke_pane`; a live
  session for a dead pane is a half-open door even once its token is gone.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from ..core.ids import utcnow
from ..core.logging import get_logger
from .jsonrpc import PROTOCOL_VERSION

logger = get_logger(__name__)

#: The header the id travels in, spelled once. The client reads it lowercase.
SESSION_HEADER = "Mcp-Session-Id"

#: Bytes of randomness in a session id. Smaller than a token's 32 because this is
#: an identifier, not a credential — but still random, because a guessable id
#: plus a stolen token would be one hurdle instead of two.
SESSION_ID_BYTES = 16

#: How long a session may sit idle before it is pruned. Long enough that a
#: harness thinking between calls is never surprised; short enough that a closed
#: editor does not leave rows behind for the life of the daemon.
SESSION_IDLE_TTL_S = 3600.0

#: Ceiling per pane, and overall. A harness that re-initialises in a loop
#: replaces its own oldest session rather than growing the registry.
MAX_SESSIONS_PER_PANE = 4
MAX_SESSIONS = 64


@dataclass
class McpSession:
    """One ``initialize``d MCP conversation, bound to the pane that opened it."""

    id: str
    pane_id: str
    created_at: datetime
    last_seen_at: datetime
    protocol_version: str = PROTOCOL_VERSION
    client_info: dict[str, Any] = field(default_factory=dict)
    #: Set by ``notifications/initialized``. The handshake is complete only then;
    #: a server that answered ``tools/call`` before it would be accepting work
    #: from a client that has not finished agreeing what the protocol is.
    initialized: bool = False

    def row(self) -> dict[str, Any]:
        """A diagnostics row. Carries no credential — the pane token is elsewhere."""
        return {
            "id": self.id,
            "pane_id": self.pane_id,
            "protocol_version": self.protocol_version,
            "client": str(self.client_info.get("name") or ""),
            "initialized": self.initialized,
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }


class McpSessionRegistry:
    """Mint, look up, touch and close ``Mcp-Session-Id`` values. Memory only.

    Args:
        clock: ``() -> datetime``, injected so idle expiry is driven
            deterministically instead of by a sleep — no test in this repository
            asserts a wall clock.
        idle_ttl_s: seconds of silence before a session is pruned.
        max_per_pane / max_sessions: the ceilings above.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utcnow,
        idle_ttl_s: float = SESSION_IDLE_TTL_S,
        max_per_pane: int = MAX_SESSIONS_PER_PANE,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self.clock = clock
        self.idle_ttl_s = float(idle_ttl_s)
        self.max_per_pane = int(max_per_pane)
        self.max_sessions = int(max_sessions)
        self._sessions: dict[str, McpSession] = {}
        # A plain threading lock, not an asyncio one: /mcp runs on the loop while
        # the /terminals routes that revoke a pane are sync `def` in the
        # threadpool, so both kinds of caller touch this registry.
        self._lock = threading.Lock()

    # --- lifecycle --------------------------------------------------------

    def open(
        self,
        pane_id: str,
        *,
        client_info: Mapping[str, Any] | None = None,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> McpSession:
        """Mint a session for ``initialize``. Returns the record; the id is its ``id``.

        Raises:
            ValueError: for an empty ``pane_id``. A session belongs to a pane —
                one with no pane could not be revoked when that pane closed.
        """
        pane = str(pane_id or "").strip()
        if not pane:
            raise ValueError("pane_id is required to open an MCP session")
        now = self.clock()
        record = McpSession(
            id=secrets.token_urlsafe(SESSION_ID_BYTES),
            pane_id=pane,
            created_at=now,
            last_seen_at=now,
            protocol_version=str(protocol_version or PROTOCOL_VERSION),
            client_info=dict(client_info or {}),
        )
        with self._lock:
            self._prune_locked()
            # Evict this pane's oldest first, then the registry's oldest: a
            # re-initialising harness must never be able to push another pane's
            # live session out.
            mine = sorted(
                (s for s in self._sessions.values() if s.pane_id == pane),
                key=lambda s: s.created_at,
            )
            while len(mine) >= self.max_per_pane:
                self._sessions.pop(mine.pop(0).id, None)
            while len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.last_seen_at)
                self._sessions.pop(oldest.id, None)
            self._sessions[record.id] = record
        return record

    def get(self, session_id: str, *, pane_id: str = "") -> McpSession | None:
        """The named session, or ``None`` for unknown, expired, or WRONG PANE.

        ``pane_id`` is the pane the request's token resolved to. Passing it is how
        a session id stops being transferable: an id presented with another pane's
        credential resolves to nothing, so the capability filter is never computed
        from a pane the caller does not hold a token for. Omitting it (a
        diagnostics read) skips the binding check only.

        Touches ``last_seen_at`` on a hit, so the idle TTL measures silence rather
        than age.
        """
        if not session_id:
            return None
        with self._lock:
            self._prune_locked()
            record = self._sessions.get(session_id)
            if record is None:
                return None
            if pane_id and record.pane_id != str(pane_id):
                logger.warning(
                    "refused an MCP session id presented by a different pane (%s)",
                    pane_id,
                )
                return None
            record.last_seen_at = self.clock()
            return record

    def mark_initialized(self, session_id: str, *, pane_id: str = "") -> bool:
        """Record ``notifications/initialized``. Returns whether a session matched."""
        record = self.get(session_id, pane_id=pane_id)
        if record is None:
            return False
        record.initialized = True
        return True

    def close(self, session_id: str, *, pane_id: str = "") -> bool:
        """``DELETE /mcp``: end one session. Returns whether one was live.

        Honours the same pane binding as :meth:`get`, or one harness could close
        another pane's session by id.
        """
        if not session_id:
            return False
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False
            if pane_id and record.pane_id != str(pane_id):
                return False
            self._sessions.pop(session_id, None)
            return True

    def revoke_pane(self, pane_id: str) -> int:
        """Close every session belonging to one pane. Returns how many died.

        Called wherever :meth:`PaneTokenStore.revoke_pane` is called: killing the
        credential while leaving the conversation open would leave a row claiming
        a pane that no longer exists.
        """
        pane = str(pane_id or "")
        if not pane:
            return 0
        with self._lock:
            doomed = [sid for sid, s in self._sessions.items() if s.pane_id == pane]
            for sid in doomed:
                self._sessions.pop(sid, None)
        return len(doomed)

    def revoke_all(self) -> int:
        """Close every session. The shutdown path's call."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            return count

    # --- inspection -------------------------------------------------------

    def rows(self) -> list[dict[str, Any]]:
        """Every live session, newest first, in a diagnostics shape."""
        with self._lock:
            self._prune_locked()
            records = list(self._sessions.values())
        records.sort(key=lambda s: s.created_at, reverse=True)
        return [r.row() for r in records]

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._sessions)

    def _prune_locked(self) -> None:
        """Drop idle sessions. Called from every read, so no caller can see a stale row."""
        if self.idle_ttl_s <= 0:
            return
        cutoff = self.clock() - timedelta(seconds=self.idle_ttl_s)
        for sid, record in list(self._sessions.items()):
            if record.last_seen_at <= cutoff:
                self._sessions.pop(sid, None)


def session_id_from_headers(headers: Mapping[str, Any] | None) -> str:
    """Read ``Mcp-Session-Id`` out of a header mapping, case-insensitively.

    Starlette's ``request.headers`` is already case-insensitive, but a plain dict
    (a test, the stdio shim relaying a captured request) is not — and a lookup
    that silently missed would present every call as a brand-new session, which
    reads as "the harness keeps losing its connection".
    """
    if not headers:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(SESSION_HEADER) or getter(SESSION_HEADER.lower())
        if value:
            return str(value).strip()
    for key, value in dict(headers).items():
        if isinstance(key, str) and key.lower() == SESSION_HEADER.lower():
            return str(value).strip()
    return ""


__all__ = [
    "MAX_SESSIONS",
    "MAX_SESSIONS_PER_PANE",
    "SESSION_HEADER",
    "SESSION_ID_BYTES",
    "SESSION_IDLE_TTL_S",
    "McpSession",
    "McpSessionRegistry",
    "session_id_from_headers",
]
