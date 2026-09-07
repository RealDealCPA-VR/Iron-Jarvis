"""The one live socket to the Iron Jarvis browser add-on (plan §5.3, D08).

This module is the transport half of the Browser capability: it holds the single
authoritative :class:`ExtensionConnection`, mints request ids, tracks the pending
futures that make concurrent in-flight commands possible, and performs the D08
replacement sequence when a newer socket authenticates. It knows nothing about
tools, permissions or ``browser_access`` — :class:`~iron_jarvis.browser.service.
BrowserRuntime` owns all of that, and keeping policy out of here is what makes
"the add-on holds no policy" true rather than aspirational.

**The pending-future map is the whole concurrency mechanism.** Nothing serialises
commands; a command registers a future under its ``req_<n>`` id, sends one frame,
and awaits. Two consequences that are each a test:

* **A timeout removes the future.** A late reply must be *discarded*, not delivered
  to a waiter that has already given up — resolving a dead future is at best a
  no-op and at worst (if the id were reused) an answer delivered to the wrong
  question. The removal is in a ``finally``, so it happens on every exit path.
* **A replaced connection fails its futures.** Plan §7 names step 5 of the D08
  sequence as the one most likely to be missed: every command in flight on the
  outgoing socket must fail with ``CONNECTION_REPLACED`` rather than hang. A hung
  future is the worst available outcome — the tool call never returns, the chat
  turn never finishes, and the user sees a spinner with no error anywhere.

**Frame size is checked before ``json.loads``** (:data:`~iron_jarvis.browser.
protocol.MAX_FRAME_BYTES`). The decode runs on the daemon's single event loop, so
an oversized payload is not a big object — it is a stalled application, and the
v1.153.1 outage is what that looks like to the user: every request timing out and
the dashboard reporting "Daemon offline". :meth:`ExtensionBackend.handle_raw`
therefore measures the bytes first and refuses without parsing.

**The frame cap cannot fail the right waiter, and Ship 2 must not discover that.**
A refused frame is dropped before the parse, so its ``id`` is unknowable — the
command it was answering therefore burns its whole bound and reports
``ACTION_TIMEOUT`` ("your browser did not answer in time") when the browser did
answer, just too largely. Ship 1 accepts that: the alternative is parsing the
payload we refused to parse. Ship 2 is where it stops being an edge case, because
plan §10.1 puts base64 screenshot bytes on the response frame and a full-page PNG
passes 512 KB routinely. The fix belongs on the add-on side — measure the frame
there and answer with an ``EXTENSION_ERROR``/``PAGE_NOT_READY`` envelope naming the
size — plus a decided screenshot transport (chunking, or a cap raised for that one
method). Decide it before that ship, not after a user waits 15 s per screenshot.

**A refused frame is counted, not just logged.** One refusal is a bug on the other
side of the socket; :data:`MAX_REFUSED_FRAMES` of them in a row is a caller writing
to the daemon's log at line rate, so the socket is closed once the count is reached
— with 1002, never 1008: the add-on drops its stored pairing token on a 1008
(``extensions/chrome/src/bridge/socket.ts`` ``onClose``), and a garbage frame is not
a reason to make the user pair again.

**The restricted state is a state machine, not a convention** (D06A). An unpaired
socket may send exactly one frame type,
:data:`~iron_jarvis.browser.protocol.RESTRICTED_INBOUND_FRAMES`; anything else
closes it with 1008 from inside :meth:`ExtensionBackend.handle_frame`, so the rule
holds even for a caller that forgot to check. The route's only duty is to stop
reading when the handler says the socket is gone.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import datetime
from typing import Any

from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from . import protocol as P
from .errors import BrowserError, BrowserErrorCode

logger = get_logger(__name__)

#: Event names for the bus (plan §10.2). Read through ``getattr`` with the literal
#: as the fallback because the ``EventType`` constants are a coordinator edit to
#: ``core/events.py``: this module must publish the right *name* whether or not
#: that edit has landed yet, and a hard import of a missing attribute would fail
#: at module load in the frozen build, where the traceback reaches nobody.
EVENT_CONNECTED: str = getattr(EventType, "BROWSER_CONNECTED", "browser.connected")
EVENT_DISCONNECTED: str = getattr(EventType, "BROWSER_DISCONNECTED", "browser.disconnected")
EVENT_TAB_ACTIVATED: str = getattr(EventType, "BROWSER_TAB_ACTIVATED", "browser.tab_activated")
EVENT_NAVIGATION_COMPLETED: str = getattr(
    EventType, "BROWSER_NAVIGATION_COMPLETED", "browser.navigation_completed"
)
EVENT_DOWNLOAD_COMPLETED: str = getattr(
    EventType, "BROWSER_DOWNLOAD_COMPLETED", "browser.download_completed"
)

#: The four ``browser.disconnected`` reasons of plan §10.2, and the whole vocabulary.
#: A word outside this tuple becomes ``closed``: the reason is what the ledger and any
#: later event consumer read as the authoritative account of why a browser went away,
#: and a free-form string typed at a call site is how "replaced" became "closed" (the
#: same lesson ``providers/router.failure_reason`` exists for — a failure reason is
#: DERIVED, never typed at the publish site).
DISCONNECT_REASONS: tuple[str, ...] = ("closed", "replaced", "revoked", "error")

#: Consecutive refused frames (oversized, not JSON, not an object) one connection may
#: send before it is closed. A bound, not a rate: nothing here measures a clock.
MAX_REFUSED_FRAMES = 10

#: How many of those refusals are logged at WARNING. The rest are counted silently —
#: a caller that can write one log line per frame it sends has a log-flood primitive,
#: and the count is still visible on ``GET /browser/status.last_error``.
MAX_REFUSAL_WARNINGS = 3

#: The close code for a connection that exhausted :data:`MAX_REFUSED_FRAMES`.
#: 1002 (protocol error) and NOT 1008: the add-on treats 1008 on a token-bearing
#: socket as "this credential is refused" and deletes its stored pairing token, so
#: 1008 here would make a buggy frame cost the user a re-pair.
CLOSE_PROTOCOL_ERROR = 1002

#: ``browser.event`` name -> bus event name. A frame whose event is absent here is
#: recorded as an extension error rather than published under a guessed name: an
#: event published with a name nothing subscribes to is invisible, which is worse
#: than a named refusal the status route can show.
_EVENT_BUS_NAMES: dict[str, str] = {
    P.EVENT_TAB_ACTIVATED: EVENT_TAB_ACTIVATED,
    P.EVENT_NAVIGATION_COMPLETED: EVENT_NAVIGATION_COMPLETED,
    P.EVENT_DOWNLOAD_COMPLETED: EVENT_DOWNLOAD_COMPLETED,
}


class ExtensionConnection:
    """One WebSocket to the add-on, plus the futures awaiting answers on it.

    Args:
        ws: anything with ``async send_json(dict)`` and ``async close(code)`` —
            a Starlette ``WebSocket`` in the daemon, a stand-in in a test. Typed
            loosely on purpose: this class must be constructible without a live
            ASGI server, or the replacement sequence could only be tested through
            a route.
        extension_id: the add-on's id. Known from the ``Origin`` header before
            ``browser.hello`` arrives, and refreshed by that frame.
        extension_version: the add-on's version, from ``browser.hello``.
        host_permission: whether the add-on holds the all-sites grant (Q02).
        paired: whether this socket authenticated with a pairing token. A false
            value means RESTRICTED: only ``browser.pairing_ack`` may arrive.
        pairing_request_id: the ``pair_<rand>`` this restricted socket is offering.
        clock: ``() -> datetime`` for :attr:`connected_at`, injected for tests.
    """

    def __init__(
        self,
        ws: Any,
        *,
        extension_id: str = "",
        extension_version: str = "",
        host_permission: bool = False,
        paired: bool = False,
        pairing_request_id: str = "",
        clock: Any = utcnow,
    ) -> None:
        self.ws = ws
        self.extension_id = str(extension_id or "")
        self.extension_version = str(extension_version or "")
        self.host_permission = bool(host_permission)
        self.paired = bool(paired)
        self.pairing_request_id = str(pairing_request_id or "")
        self.connected_at: datetime = clock()
        #: request_id -> the future awaiting that response. THE concurrency
        #: mechanism: nothing else orders or serialises commands.
        self.pending: dict[str, asyncio.Future] = {}
        #: Whether the add-on has greeted us. Recorded rather than required: a
        #: socket that pairs on this same connection never sends hello, because
        #: it had no token when it opened.
        self.hello_seen = False
        #: Whether the restricted socket acknowledged its pairing offer.
        self.pairing_acked = False
        self.closed = False
        self.close_code = 0
        #: Whether a NEWER socket took this one's place (D08). The one fact that
        #: decides whether a disconnect is reported as ``replaced`` or as ``closed``,
        #: recorded on the connection by :meth:`ExtensionBackend.adopt` so the reason
        #: is derived from what happened rather than from a word the caller typed.
        self.superseded = False
        #: Consecutive frames this connection sent that could not be accepted.
        self.refused_frames = 0

    @property
    def restricted(self) -> bool:
        """Whether the pairing-only frame filter applies.

        Derived from :attr:`paired` rather than stored beside it. Two fields for
        one fact drift the moment a code path sets one and forgets the other, and
        the drift here would be a socket that is treated as authenticated while
        the pairing state machine still thinks it is restricted (or worse, the
        reverse) — a security property decided by whichever assignment ran last.
        """
        return not self.paired

    async def send(self, frame: dict[str, Any]) -> bool:
        """Send one frame; ``False`` when the socket is gone. Never raises.

        Callers are failure paths (a ``connection_replaced`` frame, an error
        response), and an exception thrown out of one of those would replace a
        handled browser failure with an unhandled 500. The boolean lets a caller
        that *needs* the frame to have landed — :meth:`ExtensionBackend.command` —
        turn a dead socket into ``BROWSER_NOT_CONNECTED`` with words the model can
        act on.
        """
        if self.closed:
            return False
        try:
            await self.ws.send_json(frame)
            return True
        except Exception:  # noqa: BLE001 — a dead socket is an expected outcome here
            logger.debug("browser socket send failed (%s)", frame.get("type"), exc_info=True)
            self.closed = True
            return False

    async def close(self, code: int = 1000) -> None:
        """Close the socket once, recording the code ACTUALLY sent. Never raises.

        The assignment is below the guard, not above it: the policy paths close with
        1008 (a restricted-state violation, the pairing deadline) and the ``finally``
        in the route then calls ``release``, which closes with 1000. Recording the
        second, never-sent code would make the first diagnostic that surfaces this
        field report a protocol-violating add-on as a clean disconnect.
        """
        if self.closed:
            return
        self.close_code = code
        self.closed = True
        try:
            await self.ws.close(code=code)
        except Exception:  # noqa: BLE001 — already closed by the peer
            logger.debug("browser socket close failed", exc_info=True)

    def fail_pending(self, code: BrowserErrorCode, **fmt: Any) -> int:
        """Fail every future awaiting on this connection; returns how many.

        Synchronous and total: it must run even when the socket send that preceded
        it raised, which is why :meth:`ExtensionBackend.adopt` calls it from a
        ``finally``. ``set_exception`` on an already-resolved future raises
        ``InvalidStateError``, so each one is checked — a single racing response
        would otherwise take down the whole replacement sequence.
        """
        pending, self.pending = self.pending, {}
        failed = 0
        for request_id, future in pending.items():
            if future.done():
                continue
            future.set_exception(BrowserError(code, **fmt))
            failed += 1
            logger.debug("browser command %s failed: %s", request_id, code)
        return failed


class ExtensionBackend:
    """Holds the one live add-on connection and speaks the protocol over it.

    Args:
        event_bus: the platform :class:`~iron_jarvis.core.events.EventBus`.
            Optional so a test can build a backend with no bus; publishing is
            wrapped so a bus failure can never break a socket either way.
        clock: ``() -> datetime``, injected for deterministic tests.
        access_reader: ``() -> str`` returning the live ``browser_access`` word, so
            ``browser.ready`` can carry the mode the add-on's own panel names. A
            READER and not a value: this module holds no policy, and a captured
            string would be the same stale-setting bug ``BrowserRuntime.access``
            exists to prevent. ``BrowserRuntime`` installs its own ``access()``
            here; with no reader the ready frame simply omits the key, and the
            add-on then says the mode is unknown rather than guessing.

    Constructed explicitly by ``platform.py`` (the coordinator's file). Nothing
    here reads global state.
    """

    def __init__(
        self,
        *,
        event_bus: Any | None = None,
        clock: Any = utcnow,
        access_reader: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.clock = clock
        self.access_reader = access_reader
        self._conn: ExtensionConnection | None = None
        self._lock = asyncio.Lock()
        self._seq = itertools.count(1)
        #: pairing request id -> the restricted socket offering it, so
        #: ``POST /browser/pair`` can find the socket to deliver the token on.
        #: A restricted socket is NOT authoritative: it cannot run a command, and
        #: putting it in ``_conn`` would make an unpaired browser look connected.
        self._restricted: dict[str, ExtensionConnection] = {}
        #: The last thing that went wrong, for ``GET /browser/status.last_error``.
        #: One key, per the v1.229.0 rule that a failing loop is NAMED where the
        #: user is standing rather than logged at DEBUG and reported ``ok``.
        self.last_error: str = ""
        #: Cached active tab from ``browser.event tab_activated``. Ship 2's ambient
        #: context reads this rather than making a round trip, because a prompt
        #: assembly that awaits a browser is a prompt assembly that can hang.
        self.active_tab: dict[str, Any] | None = None

    # --- state ------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether an authenticated, open socket is available for commands."""
        conn = self._conn
        return conn is not None and conn.paired and not conn.closed

    @property
    def connection(self) -> ExtensionConnection | None:
        """The authoritative connection, or ``None``. Read-only by convention."""
        return self._conn

    def access_word(self) -> str:
        """The live ``browser_access`` word for the wire, or ``""`` when unknown.

        Never a guess: an unreadable or absent reader answers ``""``, which
        :meth:`ready_frame` turns into an OMITTED key. Sending ``"off"`` on a hunch
        would tell an add-on its user had switched the capability off.
        """
        reader = self.access_reader
        if reader is None:
            return ""
        try:
            value = reader()
        except Exception:  # noqa: BLE001 — a ready frame must not fail on a getter
            logger.debug("browser access read failed while building a ready frame", exc_info=True)
            return ""
        return value.strip() if isinstance(value, str) else ""

    def ready_frame(self, *, active: bool = True) -> dict[str, Any]:
        """``browser.ready``, stamped with the live access mode.

        Built here rather than at each call site so the mode is read at SEND time.
        ``active`` is False for a credentialled socket the daemon refuses to make
        authoritative — today, one that connected while Browser access is off.
        """
        return dict(P.ready_frame(active, self.access_word() or None))

    def next_request_id(self) -> str:
        """The next ``req_<n>``. Minted here so ids are unique per daemon run."""
        return f"{P.REQUEST_ID_PREFIX}{next(self._seq)}"

    def status(self) -> dict[str, Any]:
        """The transport's half of ``GET /browser/status``. Never raises.

        Deliberately does not touch the browser: the status route must answer
        while the add-on is wedged, because it is where the user looks to find out
        *that* it is wedged.
        """
        conn = self._conn
        return {
            "connected": self.connected,
            "extension_id": conn.extension_id if conn else "",
            "extension_version": conn.extension_version if conn else "",
            "host_permission": bool(conn.host_permission) if conn else False,
            "connected_at": conn.connected_at.isoformat() if conn else None,
            "in_flight": len(conn.pending) if conn else 0,
            "active_tab": dict(self.active_tab) if self.active_tab else None,
            "last_error": self.last_error or None,
        }

    # --- connection lifecycle --------------------------------------------

    def register_restricted(self, conn: ExtensionConnection) -> None:
        """Track an unpaired socket by its pairing request id (D06A).

        Registered rather than adopted: an unpaired browser must not read as
        connected anywhere, and a restricted socket that could run a command would
        make the pairing step decorative.
        """
        if conn.pairing_request_id:
            self._restricted[conn.pairing_request_id] = conn

    def restricted_socket(self, request_id: str) -> ExtensionConnection | None:
        """The restricted socket offering ``request_id``, if it is still open."""
        conn = self._restricted.get(request_id)
        if conn is not None and conn.closed:
            self._restricted.pop(request_id, None)
            return None
        return conn

    async def adopt(self, conn: ExtensionConnection) -> ExtensionConnection | None:
        """Make ``conn`` authoritative and retire any predecessor (D08).

        The sequence, in the plan's own order, and the order matters:

        1. ``conn`` becomes ``_conn`` **first**, under the lock, so a command
           racing in behind us goes to the live socket rather than the one being
           closed.
        2. the outgoing socket is told ``browser.connection_replaced``;
        3. **every in-flight future on it fails with ``CONNECTION_REPLACED``** —
           in a ``finally``, so a failed send cannot leave a waiter hanging;
        4. the outgoing socket is closed with 1000;
        5. ``conn`` is told ``browser.ready``.

        Returns the replaced connection, or ``None`` when there was none.
        """
        async with self._lock:
            previous = self._conn
            self._conn = conn
            self._restricted.pop(conn.pairing_request_id, None)
        if previous is not None and previous is not conn:
            # Recorded BEFORE anything else can publish: the outgoing socket's own
            # ``_pump`` reaches its ``finally`` and calls ``release`` moments from
            # now, and this flag is how that release knows the disconnect it is
            # looking at was a replacement (already announced here) rather than a
            # browser the user closed.
            previous.superseded = True
            try:
                await previous.send(P.connection_replaced_frame())
            finally:
                # Step 5 of D08, and the step plan §7 names as the one most
                # likely to be missed. A waiter left pending here never returns:
                # the tool call hangs, the turn never ends, and no error is ever
                # shown anywhere in the app.
                failed = previous.fail_pending(BrowserErrorCode.CONNECTION_REPLACED)
                await previous.close(1000)
            if failed:
                logger.info("browser connection replaced with %d command(s) in flight", failed)
            await self._publish(
                EVENT_DISCONNECTED,
                {"reason": "replaced", "detail": "a newer browser connection authenticated"},
            )
        await conn.send(self.ready_frame(active=True))
        self.last_error = ""
        await self._publish(
            EVENT_CONNECTED,
            {
                "extension_id": conn.extension_id,
                "extension_version": conn.extension_version,
                "host_permission": conn.host_permission,
            },
        )
        return previous if previous is not conn else None

    async def deliver_pairing(self, request_id: str, token: str) -> ExtensionConnection:
        """Hand the plaintext token to the restricted socket, then adopt it (D06A).

        The token's ONE appearance on the wire. It is not stored, not logged and
        not returned: ``POST /browser/pair`` answers ``{"paired": true}``, and the
        caller minted this value from
        :meth:`~iron_jarvis.browser.pairing.PairingStore.mint` moments ago.

        Raises:
            BrowserError: ``BROWSER_NOT_CONNECTED`` when the socket that asked has
                gone. Refusing loudly matters: the alternative is a minted
                credential no browser ever received, and a card that says paired
                while the add-on keeps asking to pair.
        """
        conn = self.restricted_socket(request_id)
        if conn is None:
            raise BrowserError(
                BrowserErrorCode.BROWSER_NOT_CONNECTED,
                message=(
                    "The browser that asked to pair is no longer connected. Ask "
                    "the user to reload the Iron Jarvis add-on and press Pair again."
                ),
            )
        if not await conn.send(P.paired_frame(token)):
            raise BrowserError(BrowserErrorCode.BROWSER_NOT_CONNECTED)
        conn.paired = True
        self._restricted.pop(request_id, None)
        await self.adopt(conn)
        return conn

    async def release(
        self,
        conn: ExtensionConnection | None,
        *,
        reason: str = "closed",
        detail: str = "",
    ) -> None:
        """Retire ``conn``: fail its futures, forget it, and say so on the bus ONCE.

        Called when a socket closes for any reason other than replacement (the
        peer went away, the pairing deadline expired, Disconnect was pressed).

        ``reason`` is the caller's INTENT, drawn from :data:`DISCONNECT_REASONS`, and
        the published word is DERIVED from it plus what actually happened. Two rules,
        and each existed as a live defect:

        * **A superseded socket reports ``replaced``, not the caller's word.** The
          outgoing socket's ``_pump`` reaches its ``finally`` with the local default
          ``"closed"``, and :meth:`adopt` has already published the replacement — so
          taking the caller's string produced a SECOND, false
          ``browser.disconnected {reason: "closed"}`` *after* the new socket's
          ``browser.connected``. Any consumer deriving connection state from the
          event stream then rendered "not connected" over a working browser.
        * **Only the socket that WAS authoritative announces anything.** Exactly one
          event per real disconnect: a connection that never held ``_conn`` (a
          restricted socket, or one the daemon refused to activate) never announced a
          connect either, and ``release`` is called twice for the same socket on the
          ordinary path — once by ``POST /browser/disconnect``, then again by the
          pump's ``finally``.

        Failing the futures here is the same property :meth:`adopt` protects: a
        socket that dies mid-command must produce an error, not silence.
        """
        if conn is None:
            return
        word = reason if reason in DISCONNECT_REASONS else "closed"
        if conn.superseded:
            word = "replaced"
        conn.fail_pending(BrowserErrorCode.BROWSER_NOT_CONNECTED)
        await conn.close(1011 if word == "error" else 1000)
        if conn.pairing_request_id:
            self._restricted.pop(conn.pairing_request_id, None)
        was_authoritative = self._conn is conn
        if not was_authoritative:
            return
        async with self._lock:
            if self._conn is conn:
                self._conn = None
        self.active_tab = None
        await self._publish(EVENT_DISCONNECTED, {"reason": word, "detail": detail})

    # --- commands ---------------------------------------------------------

    async def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run one method on the browser and return its ``result`` dict.

        Args:
            method: one of :data:`~iron_jarvis.browser.protocol.ALL_METHODS`.
            params: the method's arguments; ``None`` becomes ``{}`` on the wire.
            timeout_s: overrides
                :func:`~iron_jarvis.browser.protocol.command_timeout_s`. A bound,
                never a performance claim — no test asserts how long this took.

        Raises:
            BrowserError: ``BROWSER_NOT_CONNECTED`` with no live socket,
                ``ACTION_TIMEOUT`` when the add-on does not answer in time, or
                whatever code the add-on itself reported.
        """
        bound = float(timeout_s) if timeout_s else P.command_timeout_s(method)
        return await self._await_response(
            lambda request_id: P.command_frame(request_id, method, params),
            bound=bound,
            label=method,
        )

    async def directive(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Send a ``browser.directive`` and await its response.

        Directives act on the browser *session* rather than on a page:
        ``request_host_permissions`` (the documented plan §6 deviation — the add-on
        opens ``setup.html`` because ``chrome.permissions.request`` needs a user
        gesture) and ``disconnect``. They share the command pending map because
        they share the response frame, so the id namespace must stay one namespace.
        """
        bound = float(timeout_s) if timeout_s else P.DEFAULT_COMMAND_TIMEOUT_S
        return await self._await_response(
            lambda request_id: P.directive_frame(request_id, action, params),
            bound=bound,
            label=f"directive {action}",
        )

    async def _await_response(
        self,
        build_frame: Any,
        *,
        bound: float,
        label: str,
    ) -> dict[str, Any]:
        """Mint an id, send the frame it names, and await its answer. ONE copy.

        :meth:`command` and :meth:`directive` differ only in the frame they build,
        the bound they use and the words a timeout is recorded with — everything
        else (the not-connected refusal, the id, the pending registration, the
        timeout scope, the ``finally`` that drops the future) is a contract that must
        stay in lock step. It was written twice, and a fix applied to one of the two
        would have silently regressed the other; ``directive`` is the half carrying
        the host-permission grant and Disconnect.

        Args:
            build_frame: ``(request_id) -> frame``. The id is minted here because
                the pending map is keyed by it.
            bound: seconds to wait. A bound, never a performance claim.
            label: what a timeout is called in ``last_error`` — a method name, or
                ``"directive <action>"``.
        """
        conn = self._conn
        if conn is None or not conn.paired or conn.closed:
            raise BrowserError(BrowserErrorCode.BROWSER_NOT_CONNECTED)
        request_id = self.next_request_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.pending[request_id] = future
        try:
            if not await conn.send(build_frame(request_id)):
                raise BrowserError(BrowserErrorCode.BROWSER_NOT_CONNECTED)
            try:
                async with asyncio.timeout(bound) as scope:
                    return await future
            except TimeoutError:
                # Only OUR deadline earns the timeout wording. On 3.11+
                # ``asyncio.TimeoutError`` IS the builtin, so a ``TimeoutError``
                # raised from anywhere else must keep its own message — the
                # v1.228.0 misattribution lesson.
                if scope.expired():
                    self.last_error = f"{label} timed out after {bound:g}s"
                    raise BrowserError(BrowserErrorCode.ACTION_TIMEOUT) from None
                raise
        finally:
            # A late reply is DISCARDED, not delivered: the waiter is gone, and a
            # reused id would otherwise answer the wrong question.
            conn.pending.pop(request_id, None)

    # --- inbound frames ---------------------------------------------------

    async def handle_raw(self, conn: ExtensionConnection, raw: str | bytes) -> bool:
        """Measure, then parse, then handle one inbound frame.

        Returns ``True`` while the socket should keep being read; ``False`` once it
        has been closed (a restricted-state violation, per D06A).

        The size check happens **before** ``json.loads`` because the decode runs on
        the daemon's single event loop: a 40 MB frame is not a big object, it is
        every request in the app timing out while the loop is busy, which the user
        reads as "Daemon offline" (v1.153.1). An oversized frame is dropped and
        recorded, not parsed — the command it was answering then fails honestly
        with ``ACTION_TIMEOUT`` rather than the daemon pretending to have an answer.
        """
        if isinstance(raw, str) and len(raw) > P.MAX_FRAME_BYTES:
            # The cheap upper bound first: a ``str`` of N characters is at least N
            # UTF-8 bytes, so this refuses without building the copy. Starlette hands
            # text frames through as ``str`` and uvicorn's own ``ws_max_size`` default
            # is 16 MB, so encoding before measuring made the single event loop build
            # a 16 MB ``bytes`` object purely to decide it was too big — a smaller
            # version of the v1.153.1 shape this check exists to prevent.
            return await self._refuse_frame(
                conn,
                f"your browser sent a frame over {P.MAX_FRAME_BYTES} bytes, "
                "so it was refused unread",
            )
        data = raw.encode("utf-8", "replace") if isinstance(raw, str) else bytes(raw)
        if len(data) > P.MAX_FRAME_BYTES:
            return await self._refuse_frame(
                conn,
                f"your browser sent a {len(data)} byte frame; the limit is "
                f"{P.MAX_FRAME_BYTES} bytes, so it was refused unread",
            )
        try:
            frame = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return await self._refuse_frame(conn, "your browser sent a frame that is not JSON")
        if not isinstance(frame, dict):
            return await self._refuse_frame(
                conn, "your browser sent a frame that is not an object"
            )
        conn.refused_frames = 0
        return await self.handle_frame(conn, frame)

    async def _refuse_frame(self, conn: ExtensionConnection, why: str) -> bool:
        """Record one unacceptable frame; close the socket once there have been too many.

        Returns ``True`` while the socket should keep being read, matching
        :meth:`handle_raw`. A single refusal is a bug on the other side and must not
        cost the user their connection — but an unbounded stream of them is a caller
        writing to ``daemon.log`` at line rate with no credential at all, so the
        WARNINGs stop after :data:`MAX_REFUSAL_WARNINGS` and the socket closes at
        :data:`MAX_REFUSED_FRAMES` with :data:`CLOSE_PROTOCOL_ERROR`.
        """
        conn.refused_frames += 1
        self.last_error = why
        if conn.refused_frames <= MAX_REFUSAL_WARNINGS:
            logger.warning("browser frame refused: %s", why)
        else:
            logger.debug("browser frame refused (%d): %s", conn.refused_frames, why)
        if conn.refused_frames >= MAX_REFUSED_FRAMES:
            self.last_error = (
                f"your browser sent {conn.refused_frames} frames Iron Jarvis could not "
                f"read, so the connection was closed; the last one: {why}"
            )
            logger.warning("closing browser socket: %s", self.last_error)
            await conn.close(CLOSE_PROTOCOL_ERROR)
            if conn.pairing_request_id:
                self._restricted.pop(conn.pairing_request_id, None)
            return False
        return True

    async def handle_frame(self, conn: ExtensionConnection, frame: dict[str, Any]) -> bool:
        """Dispatch one parsed frame. ``False`` means the socket has been closed.

        The restricted-state rule of D06A is enforced here rather than at the
        route: a frame of any type other than
        :data:`~iron_jarvis.browser.protocol.RESTRICTED_INBOUND_FRAMES` on an
        unpaired socket closes it with 1008, from inside the state machine, so the
        rule holds for every caller including one that forgot to check.
        """
        kind = str(frame.get("type") or "")
        if conn.restricted and kind not in P.RESTRICTED_INBOUND_FRAMES:
            self.last_error = f"an unpaired browser sent {kind or 'an unnamed frame'}"
            logger.warning("closing unpaired browser socket: %s", self.last_error)
            await conn.close(1008)
            if conn.pairing_request_id:
                self._restricted.pop(conn.pairing_request_id, None)
            return False
        if kind == P.FRAME_PAIRING_ACK:
            conn.pairing_acked = True
            return True
        if kind == P.FRAME_HELLO:
            await self._handle_hello(conn, frame)
            return True
        if kind == P.FRAME_RESPONSE:
            self._resolve_response(conn, frame)
            return True
        if kind == P.FRAME_EVENT:
            await self._handle_event(frame)
            return True
        # An unknown type on a PAIRED socket is recorded, not fatal: a newer add-on
        # speaking a frame this daemon predates must not take the connection down,
        # or an upgrade on either side becomes an outage.
        self.last_error = f"your browser sent an unknown frame type {kind!r}"
        return True

    async def _handle_hello(self, conn: ExtensionConnection, frame: dict[str, Any]) -> None:
        """Record who the add-on is, and re-announce only if the facts changed.

        ``browser.hello`` arrives *after* adoption on a token-bearing socket, and
        never at all on a socket that paired in place (it had no token when it
        opened). So ``browser.connected`` is published at adoption with what was
        known then, and again here only when this frame contradicts it — a stale
        ``host_permission`` in an event is a lie, and a second identical event is
        noise.
        """
        before = (conn.extension_id, conn.extension_version, conn.host_permission)
        conn.extension_id = str(frame.get("extension_id") or conn.extension_id)
        conn.extension_version = str(frame.get("extension_version") or conn.extension_version)
        conn.host_permission = bool(frame.get("host_permission"))
        conn.hello_seen = True
        after = (conn.extension_id, conn.extension_version, conn.host_permission)
        if after != before and self._conn is conn:
            await self._publish(
                EVENT_CONNECTED,
                {
                    "extension_id": conn.extension_id,
                    "extension_version": conn.extension_version,
                    "host_permission": conn.host_permission,
                },
            )

    def _resolve_response(self, conn: ExtensionConnection, frame: dict[str, Any]) -> None:
        """Resolve the future keyed by this response's id, if one is still waiting.

        An id with no waiter is normal and ignored: it is the late reply to a
        command that already timed out, which :meth:`command` deliberately dropped.
        A success carries ``result``; a failure carries the add-on's own
        ``{code, message}``, which is preserved verbatim rather than replaced by
        the generic remedy — the page knows why it failed and the model should read
        that, not a template.

        **``last_error`` is the TRANSPORT's last fault, not the last command's.** An
        add-on-reported failure is not written here at all, and a delivered answer
        CLEARS whatever was there. ``last_error`` is rendered by the Your browser card
        as an amber "Last problem: …" and nothing but a reconnect used to clear it, so
        one ``TAB_NOT_FOUND`` — which this service documents as normal ("a browser
        with no window open is a state, not a failure") — left a permanent problem
        banner over a browser that works, and Ship 2/3 make ``ELEMENT_NOT_FOUND`` and
        ``STALE_ELEMENT`` routine. The add-on's ``{code, message}`` is not lost: it
        rides the raised :class:`BrowserError` to the tool result the model reads.
        What stays in ``last_error`` is what the model never sees — our own timeout, a
        frame we refused unread, an unknown frame or event — and a successful round
        trip retires even those, so the words mean "the last thing that went wrong is
        still true".
        """
        request_id = str(frame.get("id") or "")
        future = conn.pending.pop(request_id, None)
        if future is None or future.done():
            return
        if frame.get("success"):
            result = frame.get("result")
            self.last_error = ""
            future.set_result(dict(result) if isinstance(result, dict) else {})
            return
        envelope = frame.get("error") if isinstance(frame.get("error"), dict) else {}
        code = str(envelope.get("code") or BrowserErrorCode.EXTENSION_ERROR.value)
        message = str(envelope.get("message") or "")
        future.set_exception(BrowserError(code, message=message, detail=message))

    async def _handle_event(self, frame: dict[str, Any]) -> None:
        """Publish an add-on-originated event on the existing bus (D22).

        No second bus, and no session id: browser events are ambient. The active
        tab is cached on the way through so a later prompt assembly can name the
        tab without a round trip.
        """
        name = str(frame.get("event") or "")
        payload = frame.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        bus_name = _EVENT_BUS_NAMES.get(name)
        if bus_name is None:
            self.last_error = f"your browser reported an unknown event {name!r}"
            return
        if name == P.EVENT_TAB_ACTIVATED:
            self.active_tab = payload or None
        elif name == P.EVENT_NAVIGATION_COMPLETED and self.active_tab:
            if payload.get("tab_id") == self.active_tab.get("id") or payload.get(
                "tab_id"
            ) == self.active_tab.get("tab_id"):
                self.active_tab = {**self.active_tab, **payload}
        await self._publish(bus_name, payload)

    # --- events -----------------------------------------------------------

    async def _publish(self, name: str, payload: dict[str, Any]) -> None:
        """Publish on the bus, never raising into the socket handler.

        A bus failure must not close the browser connection or fail a tool call:
        the same reasoning as the v1.229.0 ring-handler lesson, where the log call
        that was supposed to RECORD a failure became the failure. A pairing token
        never reaches this function — the only credential-shaped value in this
        module is the argument of :meth:`deliver_pairing`, which publishes nothing.
        """
        bus = self.event_bus
        if bus is None:
            return
        try:
            await bus.publish(name, payload)
        except Exception:  # noqa: BLE001 — never break a socket over telemetry
            logger.debug("browser event %s not published", name, exc_info=True)


__all__ = [
    "CLOSE_PROTOCOL_ERROR",
    "DISCONNECT_REASONS",
    "EVENT_CONNECTED",
    "EVENT_DISCONNECTED",
    "EVENT_DOWNLOAD_COMPLETED",
    "EVENT_NAVIGATION_COMPLETED",
    "EVENT_TAB_ACTIVATED",
    "MAX_REFUSAL_WARNINGS",
    "MAX_REFUSED_FRAMES",
    "ExtensionBackend",
    "ExtensionConnection",
]
