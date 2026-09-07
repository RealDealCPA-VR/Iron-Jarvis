"""Pane-scoped capability tokens: the FIFTH credential, and the only one ``/mcp`` takes (D19, D17A).

Five credentials exist in this app and none may substitute for another (plan §7).
:mod:`iron_jarvis.browser.pairing` owns the browser pairing token; this module owns
the pane token — minted for one running Build pane, bound to that pane's
capabilities, alive only as long as the pane and the process, and accepted at
``POST /mcp`` and nowhere else.

It lives in the browser package rather than in :mod:`iron_jarvis.mcpserver` because
the token is a *pane* fact; Ship 4 is merely where it is consumed.

The properties, each of which is a test in ``tests/test_pane_tokens_v1238.py``:

* **32 bytes from :func:`secrets.token_urlsafe`, never derived from the pane id.**
  A pane id is not a secret — it rides ``IRONJARVIS_PANE_ID`` into every child
  process, appears in dashboard URLs and in ``terminals.json`` — and it is REUSED:
  :meth:`~iron_jarvis.terminals.manager.TerminalManager.restore` brings a pane back
  under its original id. A token derived from it would therefore be forgeable by
  anything that ever saw the id, and would survive a restart into a different
  shell process. Two mints for the same pane produce different tokens, and that is
  asserted.
* **:meth:`PaneTokenStore.resolve` reads the LIVE pane.** The capability snapshot
  it returns is the one in force *at resolution time*, not at mint time, so
  unticking Browser in the pane's Capabilities popover takes effect on the very
  next ``tools/call`` with no re-mint, no reconnect and no restart. The snapshot
  taken at mint is a fallback for a store with no pane lookup wired (a unit test,
  or a call before ``platform.py`` has connected the manager) and is never
  preferred over a live pane.
* **Memory only.** A token that outlived the process would outlive the pane it
  names. There is deliberately no on-disk form, not even a hash: persisting one
  would mean a token minted for the shell that died at shutdown authenticating
  whatever now occupies that pane id.
* **Restart recovery is defined, and it is "no token".** ``rehydrate`` restores
  panes under the SAME ids with FRESH shells. This store comes up empty, every
  restored pane therefore has no token, and ``/mcp`` answers **401** with
  :data:`PANE_TOKEN_EXPIRED_MESSAGE`. Silently re-minting for a restored pane
  would hand a live credential to whatever now occupies that pane id — which is
  the whole reason the recovery rule is written down rather than left to fall out
  of the implementation.
* **Revocation on close.** There is no pane-closed event, so
  :meth:`revoke_pane` must be called from ``TerminalManager.kill``,
  ``TerminalManager.purge_dead`` (where a shell that died on its own is noticed)
  and ``TerminalManager.kill_all``. All three, or the one that is missed is the
  vulnerability. :meth:`sync_panes` is the single call ``purge_dead`` wants: hand
  it the ids that are still live and every other pane's token dies.

**Fail-closed twice.** :func:`authorize_mcp` refuses the install bearer and a
pairing token by EXPLICIT check, *before* it consults this store, so a credential
that somehow existed in two places at once is still refused at ``/mcp``; and
:func:`capability_enabled` treats anything that is not an unambiguous yes as no,
because ``capabilities`` reaches this module through JSON in ``terminals.json``
where ``"false"`` is a perfectly ordinary string and ``bool("false")`` is
:data:`True`.

**Blocking.** :func:`authorize_mcp` may reach SQLite through the pairing store's
verifier, so an async caller wraps it in :func:`asyncio.to_thread` — a synchronous
database hop on the daemon's single event loop is the v1.153.1 outage the user
experienced as "Daemon offline". Everything on :class:`PaneTokenStore` itself is
pure memory and safe to call from either side.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from ..core.ids import utcnow
from ..core.logging import get_logger

logger = get_logger(__name__)

#: Bytes of randomness in a pane token. The pairing token's number (plan §7), for
#: the same reason: ``token_urlsafe`` renders it as ~43 URL-safe characters, which
#: survives an ``Authorization: Bearer`` header and an environment variable
#: unescaped.
TOKEN_BYTES = 32

#: How many pane tokens may live at once. One per pane, and the terminal manager
#: caps panes far below this; the ceiling exists so a caller that mints in a loop
#: cannot grow the store without bound.
MAX_PANE_TOKENS = 64

#: The environment variable holding the per-install bearer token. DEFINED in
#: ``daemon/auth.py`` (``_TOKEN_ENV``); spelled here rather than imported so the
#: browser package stays free of the daemon package, and pinned equal to it by
#: ``test_install_token_env_matches_the_daemons``. Drift would silently stop the
#: install-bearer cross-rejection from ever firing.
INSTALL_TOKEN_ENV = "IRONJARVIS_TOKEN"

#: The two variables a launch recipe puts in the pane's child environment.
#: Spelled here because the token is minted here; the recipe lane reads them.
MCP_URL_ENV = "IRONJARVIS_MCP_URL"
MCP_TOKEN_ENV = "IRONJARVIS_MCP_TOKEN"

# --- The 401 sentences. One place, so the route and the tests cannot disagree. --
#: Plan section 12.2, verbatim: what an unknown pane token means after a restart.
PANE_TOKEN_EXPIRED_MESSAGE = (
    "the pane token expired when Iron Jarvis restarted; relaunch the harness "
    "from the Build pane"
)
MISSING_TOKEN_MESSAGE = (
    "no pane token: /mcp needs the token Iron Jarvis put in IRONJARVIS_MCP_TOKEN "
    "when the Build pane launched this harness"
)
INSTALL_BEARER_MESSAGE = (
    "the Iron Jarvis install token is not accepted at /mcp; use the pane token "
    "from IRONJARVIS_MCP_TOKEN"
)
PAIRING_TOKEN_MESSAGE = (
    "a browser pairing token is not accepted at /mcp; use the pane token from "
    "IRONJARVIS_MCP_TOKEN"
)

#: ``reason`` values on :class:`PaneTokenRefused`, so a caller can branch (and a
#: test can assert) on the KIND of refusal rather than on its wording.
REASON_NO_TOKEN = "no_token"
REASON_INSTALL_BEARER = "install_bearer"
REASON_PAIRING_TOKEN = "pairing_token"
REASON_UNKNOWN_TOKEN = "unknown_token"


def token_sha256(token: str) -> str:
    """The stored form of a pane token: lowercase SHA-256 hex.

    The store is memory-only, so this is not persistence — it keeps the plaintext
    out of the dict keys, out of a ``repr``, and out of any heap dump taken from a
    crashed daemon. One function so mint and resolve cannot disagree about
    encoding; a mismatch would not raise, it would refuse a correct token forever.
    """
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def capability_enabled(value: Any) -> bool:
    """Whether one capability value means *yes*. Fail-closed by construction.

    ``capabilities`` round-trips through ``terminals.json``, so a value arrives as
    whatever JSON held — and ``bool("false")`` is :data:`True`, which would turn
    every disabled capability back on across a single restart. Only an
    unambiguous yes counts: :data:`True`, the integer ``1``, or one of a short
    list of affirmative strings. Everything else — ``None``, ``0``, ``"false"``,
    ``"off"``, a dict, a stray object — is no.
    """
    if value is True:
        return True
    if isinstance(value, bool):  # False, and never reached by the int branch below
        return False
    if isinstance(value, int) and value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def normalise_capabilities(raw: Any) -> dict[str, bool]:
    """A pane's ``capabilities`` mapping reduced to ``{name: True}`` entries only.

    Non-mapping input (including ``None``, which is what :func:`getattr` yields
    for a pane whose class has no ``capabilities`` attribute yet) is an empty
    grant, not an error: an MCP caller with no capabilities is refused every tool,
    which is the safe reading of "I could not tell what this pane allows".
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, bool] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if capability_enabled(value):
            out[key] = True
    return out


@dataclass(frozen=True)
class PaneGrant:
    """What a pane token resolves to: a pane id and the capabilities in force NOW.

    ``capabilities`` is a read-only mapping holding only the ENABLED names, so a
    caller that does ``grant.capabilities.get("browser")`` and a caller that does
    ``grant.allows("browser")`` cannot reach different answers, and a route cannot
    widen a grant it was handed.
    """

    pane_id: str
    capabilities: Mapping[str, bool]
    minted_at: datetime
    #: True when the capability snapshot was read off the live pane (the normal
    #: case), False when it fell back to the values captured at mint. A route may
    #: log it; nothing branches on it, because a fallback grant is already the
    #: narrower of the two.
    live: bool = True

    def allows(self, name: str) -> bool:
        """Whether this pane has ``name`` enabled right now."""
        return bool(self.capabilities.get(name, False))

    def enabled(self) -> list[str]:
        """The enabled capability names, sorted — the shape a diagnostics row wants."""
        return sorted(self.capabilities)


class PaneTokenRefused(Exception):
    """``/mcp`` refused a credential. Carries the 401 wording and a machine reason.

    An exception rather than a ``None`` return because there are four distinct
    refusals and three of them are the security contract: a caller that collapsed
    them into "falsy" would answer "no token" to an install bearer, and the
    cross-rejection tests would still pass while telling the user nothing.
    """

    #: Every refusal here is 401. A capability the pane lacks is a 403 and is NOT
    #: this exception — that decision belongs to the server half, which knows
    #: which tool was asked for.
    status_code = 401

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class _Record:
    """One live token. The plaintext is never stored — only its digest."""

    token_sha256: str
    pane_id: str
    minted_at: datetime
    #: The capabilities as they stood at mint. A FALLBACK only (see the module
    #: docstring): a store with a pane lookup always prefers the live pane.
    minted_capabilities: dict[str, bool] = field(default_factory=dict)


class PaneTokenStore:
    """Random, pane-bound, capability-bound, pane-lifetime tokens. Memory only.

    Args:
        pane_lookup: ``(pane_id) -> pane | None``, wired by ``platform.py`` to
            ``terminals.get``. It is what makes :meth:`resolve` read the LIVE
            capabilities; without it the store falls back to the mint-time
            snapshot and says so on the grant (``PaneGrant.live is False``). It is
            injected rather than imported so this module never reaches into the
            terminals package, and so a test can drive every path with a plain
            object.
        clock: ``() -> datetime`` for the mint stamp. Injected because no test in
            this repository asserts a wall clock.
        max_tokens: the ceiling from :data:`MAX_PANE_TOKENS`.

    Thread-safety: a plain :class:`threading.Lock`. The ``/terminals`` routes are
    sync ``def`` and run in Starlette's threadpool while ``/mcp`` runs on the
    loop, so both kinds of caller touch this store.
    """

    def __init__(
        self,
        *,
        pane_lookup: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] = utcnow,
        max_tokens: int = MAX_PANE_TOKENS,
    ) -> None:
        self.pane_lookup = pane_lookup
        self.clock = clock
        self.max_tokens = int(max_tokens)
        self._records: list[_Record] = []
        self._lock = threading.Lock()

    # --- minting ----------------------------------------------------------

    def mint(self, pane_id: str, capabilities: Mapping[str, Any] | None = None) -> str:
        """Mint the pane's token and return the plaintext — the only time it exists.

        Any token this pane already held is revoked in the same breath. Relaunching
        a harness in a pane must not leave the previous harness's credential live:
        the user's mental model is "this pane, one harness", and an orphan token is
        exactly the thing :meth:`revoke_pane` exists to prevent, one call earlier.

        Raises:
            ValueError: for an empty ``pane_id`` — a token bound to no pane would
                resolve to a grant nothing could gate.
            RuntimeError: at :data:`MAX_PANE_TOKENS`.
        """
        pane = str(pane_id or "").strip()
        if not pane:
            raise ValueError("pane_id is required to mint a pane token")
        # NEVER derived from the pane id: the id is public and is reused across
        # restarts, so anything derived from it is forgeable and outlives its shell.
        token = secrets.token_urlsafe(TOKEN_BYTES)
        record = _Record(
            token_sha256=token_sha256(token),
            pane_id=pane,
            minted_at=self.clock(),
            minted_capabilities=normalise_capabilities(capabilities),
        )
        with self._lock:
            self._records = [r for r in self._records if r.pane_id != pane]
            if len(self._records) >= self.max_tokens:
                raise RuntimeError(f"pane token cap reached ({self.max_tokens})")
            self._records.append(record)
        # The TOKEN is not logged, at any level: a DEBUG line would put a live
        # credential in the user's diagnostics bundle forever (the pairing lesson).
        logger.info("minted a pane capability token (pane=%s)", pane)
        return token

    # --- resolving --------------------------------------------------------

    def resolve(self, token: str) -> PaneGrant | None:
        """The grant this token names, with capabilities AS OF NOW, or ``None``.

        ``None`` means every one of: no token, an unknown token, a token whose
        pane has been closed, and a token whose pane's shell has died. The caller
        (:func:`authorize_mcp`) turns all of them into the same 401, because
        distinguishing them to an unauthenticated caller would confirm which pane
        ids exist.

        A pane that has gone away has its record dropped here as well as at the
        manager's chokepoints — belt and braces, since this is the read that
        actually gates a call.
        """
        if not token:
            return None
        digest = token_sha256(token).encode("ascii")
        with self._lock:
            match = None
            for record in self._records:
                # Compare every candidate rather than a dict lookup: the digests
                # are hex (always ASCII-safe) and constant-time comparison is the
                # property this loop is for. The list is one entry per pane.
                if hmac.compare_digest(record.token_sha256.encode("ascii"), digest):
                    match = record
            if match is None:
                return None
            pane_id = match.pane_id
            minted_at = match.minted_at
            fallback = dict(match.minted_capabilities)

        if self.pane_lookup is None:
            return PaneGrant(
                pane_id=pane_id,
                capabilities=MappingProxyType(fallback),
                minted_at=minted_at,
                live=False,
            )

        try:
            pane = self.pane_lookup(pane_id)
        except Exception:  # noqa: BLE001 — a lookup that raises is not a grant
            logger.debug("pane lookup failed for %s", pane_id, exc_info=True)
            return None
        if pane is None:
            self.revoke_pane(pane_id)
            return None
        # ``alive`` is a property on TerminalSession; a pane object that has no
        # such attribute is trusted (a test double), because a missing attribute
        # is not evidence of death and the manager's chokepoints still revoke.
        if getattr(pane, "alive", True) is False:
            self.revoke_pane(pane_id)
            return None
        live_caps = normalise_capabilities(getattr(pane, "capabilities", None))
        return PaneGrant(
            pane_id=pane_id,
            capabilities=MappingProxyType(live_caps),
            minted_at=minted_at,
            live=True,
        )

    # --- revocation -------------------------------------------------------

    def revoke_pane(self, pane_id: str) -> int:
        """Kill every token for one pane. Returns how many died.

        Called from ``TerminalManager.kill``, ``purge_dead`` and ``kill_all`` —
        all three, because there is no pane-closed event and the one that is
        missed is the vulnerability.
        """
        pane = str(pane_id or "")
        if not pane:
            return 0
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records if r.pane_id != pane]
            killed = before - len(self._records)
        if killed:
            logger.info("revoked %d pane capability token(s) (pane=%s)", killed, pane)
        return killed

    def sync_panes(self, live_pane_ids: Iterable[str]) -> int:
        """Revoke every token whose pane is not in ``live_pane_ids``. Returns the count.

        The single call ``purge_dead`` wants: it evicts several panes at once and
        knows the survivors, not the casualties.
        """
        keep = {str(p) for p in live_pane_ids}
        with self._lock:
            doomed = sorted({r.pane_id for r in self._records if r.pane_id not in keep})
            self._records = [r for r in self._records if r.pane_id in keep]
        for pane in doomed:
            logger.info("revoked a pane capability token (pane=%s, no longer live)", pane)
        return len(doomed)

    def revoke_all(self) -> int:
        """Kill every token. ``kill_all``'s call, and the shutdown path's."""
        with self._lock:
            killed = len(self._records)
            self._records = []
        return killed

    # --- inspection (no secret material ever leaves here) -----------------

    def has_pane(self, pane_id: str) -> bool:
        """Whether this pane currently holds a token."""
        with self._lock:
            return any(r.pane_id == str(pane_id or "") for r in self._records)

    def pane_ids(self) -> list[str]:
        """The panes holding a token, sorted. For diagnostics and tests."""
        with self._lock:
            return sorted({r.pane_id for r in self._records})

    def rows(self) -> list[dict[str, str]]:
        """One row per live token, newest first, carrying no secret material.

        Not even the digest: it is not the token, but it is also not something a
        diagnostics payload needs.
        """
        with self._lock:
            records = list(self._records)
        records.sort(key=lambda r: r.minted_at, reverse=True)
        return [
            {"pane_id": r.pane_id, "minted_at": r.minted_at.isoformat()}
            for r in records
        ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


# --------------------------------------------------------------------------- #
# The cross-rejection. ONE place, so /mcp cannot grow a second opinion.
# --------------------------------------------------------------------------- #
def install_bearer_token() -> str:
    """The configured install bearer, or ``""`` when daemon auth is disabled.

    Read per call, matching how ``daemon/auth.py`` reads its environment per
    request: a reconfigured install must not need a restart for the refusal to
    keep working.
    """
    return (os.environ.get(INSTALL_TOKEN_ENV) or "").strip()


def is_install_bearer(candidate: str, install_token: str | None = None) -> bool:
    """Whether ``candidate`` is the per-install bearer token.

    Compares BYTES with :func:`hmac.compare_digest`, the shape
    ``daemon.auth.token_matches`` was rewritten into: the plain-``str`` form
    raises :class:`TypeError` on a non-ASCII argument, and this argument comes
    straight off an ``Authorization`` header. A raise inside the refusal path
    would be served as a bare 500.

    With no install token configured there is nothing to impersonate, so the
    answer is ``False`` and the pane-token check decides.
    """
    expected = install_bearer_token() if install_token is None else str(install_token or "")
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def is_pairing_token(candidate: str, pairing_verify: Callable[[str], Any] | None) -> bool:
    """Whether ``candidate`` is a live browser pairing token.

    ``pairing_verify`` is ``PairingStore.verify`` — passed in, not imported, so
    this module holds no daemon state and a test can drive the branch with a
    lambda. **It reaches SQLite**, which is why :func:`authorize_mcp` is blocking.

    Truthiness, not ``is True``: the real verifier answers with the pairing ROW it
    matched and ``None`` for a miss. A verifier that raises is treated as "not a
    pairing token" — the pane-token resolve below still refuses the credential, so
    the failure cannot open a door; it can only change which sentence the user
    reads.
    """
    if not candidate or pairing_verify is None:
        return False
    try:
        return bool(pairing_verify(candidate))
    except Exception:  # noqa: BLE001 — a broken verifier must not become a 500
        logger.debug("pairing verification failed during /mcp authorization", exc_info=True)
        return False


def authorize_mcp(
    candidate: str,
    *,
    tokens: PaneTokenStore,
    pairing_verify: Callable[[str], Any] | None = None,
    install_token: str | None = None,
) -> PaneGrant:
    """Authorise one ``/mcp`` request, or raise :class:`PaneTokenRefused` (401).

    The order is the security contract and it is deliberate: the install bearer
    and a pairing token are refused by their OWN explicit checks *before* the pane
    store is consulted, so a value that somehow existed as both is still refused
    at ``/mcp``. Checking the pane store first would make the explicit checks
    unreachable for exactly the credential that most needs them, and the test that
    mints an install bearer as a pane token would go green against a rule that no
    longer holds.

    **Blocking** (``pairing_verify`` hits SQLite): an async route calls this as
    ``await asyncio.to_thread(authorize_mcp, ...)``.
    """
    token = (candidate or "").strip()
    if not token:
        raise PaneTokenRefused(REASON_NO_TOKEN, MISSING_TOKEN_MESSAGE)
    if is_install_bearer(token, install_token):
        logger.warning("/mcp refused the install bearer token")
        raise PaneTokenRefused(REASON_INSTALL_BEARER, INSTALL_BEARER_MESSAGE)
    if is_pairing_token(token, pairing_verify):
        logger.warning("/mcp refused a browser pairing token")
        raise PaneTokenRefused(REASON_PAIRING_TOKEN, PAIRING_TOKEN_MESSAGE)
    grant = tokens.resolve(token)
    if grant is None:
        raise PaneTokenRefused(REASON_UNKNOWN_TOKEN, PANE_TOKEN_EXPIRED_MESSAGE)
    return grant


__all__ = [
    "INSTALL_BEARER_MESSAGE",
    "INSTALL_TOKEN_ENV",
    "MAX_PANE_TOKENS",
    "MCP_TOKEN_ENV",
    "MCP_URL_ENV",
    "MISSING_TOKEN_MESSAGE",
    "PAIRING_TOKEN_MESSAGE",
    "PANE_TOKEN_EXPIRED_MESSAGE",
    "REASON_INSTALL_BEARER",
    "REASON_NO_TOKEN",
    "REASON_PAIRING_TOKEN",
    "REASON_UNKNOWN_TOKEN",
    "TOKEN_BYTES",
    "PaneGrant",
    "PaneTokenRefused",
    "PaneTokenStore",
    "authorize_mcp",
    "capability_enabled",
    "install_bearer_token",
    "is_install_bearer",
    "is_pairing_token",
    "normalise_capabilities",
    "token_sha256",
]
