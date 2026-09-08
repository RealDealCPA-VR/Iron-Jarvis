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

**A download report is verified, never trusted.** The add-on sends the path
Chromium gave it; :func:`download_bus_payload` is what turns that claim into the
``local_path`` key the file tools read, and only when the string is genuinely
absolute. The whitelist around it (:data:`DOWNLOAD_PAYLOAD_KEYS`) exists so a
compromised add-on cannot send a ``local_path`` of its own choosing and have every
consumer downstream treat it as something the daemon checked.

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

**An unpaired socket has a LIFETIME budget, and it is the one that counts**
(v1.239.0). The refusal bound above is on CONSECUTIVE refusals and one readable
frame forgives it, which is right for a paired add-on with a bug and no bound at
all for the caller this file is actually defending against: ``?pairing=1`` needs
no credential, so any local process can send nine unreadable frames, then one
well-formed ``browser.pairing_ack``, and repeat forever — the refusal count
resets, the WARNING budget resets with it, and the D06A pairing deadline is
consulted only on an IDLE tick that a continuously sending socket never reaches.
:data:`MAX_UNPAIRED_FRAMES` bounds the whole life of a socket that has not
authenticated and nothing forgives it. The real add-on sends exactly one frame in
that state.

**``last_error`` is one line; the ledger is the history behind it** (v1.239.0).
A successful round trip retires ``last_error``, which is what the card's "Last
problem" line should do and is useless for the question a user with a flaky
browser actually asks — why does it keep dropping. Every fault therefore goes
through :meth:`ExtensionBackend.note_error`, which writes both, and
:meth:`ExtensionBackend.error_ledger` keeps the last
offered to callers on ``status()["recent_errors"]`` and bounded to
:data:`MAX_ERROR_LEDGER` DISTINCT ones, each clipped to
:data:`MAX_ERROR_DETAIL_CHARS` (a repeat bumps a count rather than
taking a row, so a flood of one fault cannot evict the interesting one). Nothing
clears it — not a success, not a new connection.

NOBODY READS IT YET, AND SAYING SO IS THE POINT. The ledger is exposed on
``status()``, and ``GET /browser/status`` does not forward it — so nothing on any
screen answers "why does my browser keep dropping", which is the question this
ledger was built for. A ledger kept and
not forwarded is work shipped to nobody: the honest state is recorded here rather
than implied away, because the alternative is a docstring promising a diagnostic
the user cannot reach. Forwarding it is one key on the status route and belongs
with whoever next touches that surface.

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
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from . import protocol as P
from .errors import BrowserError, BrowserErrorCode
from .panel import PANEL_ACCESS_ALLOWED

logger = get_logger(__name__)

#: Event names for the bus (plan §10.2). Read through ``getattr`` with the literal
#: as the fallback because the ``EventType`` constants are a coordinator edit to
#: ``core/events.py``: this module must publish the right *name* whether or not
#: that edit has landed yet, and a hard import of a missing attribute would fail
#: at module load in the frozen build, where the traceback reaches nobody.
EVENT_CONNECTED: str = getattr(EventType, "BROWSER_CONNECTED", "browser.connected")
EVENT_DISCONNECTED: str = getattr(
    EventType, "BROWSER_DISCONNECTED", "browser.disconnected"
)
EVENT_TAB_ACTIVATED: str = getattr(
    EventType, "BROWSER_TAB_ACTIVATED", "browser.tab_activated"
)
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

#: Total frames an UNPAIRED socket may deliver before it is closed as a flood.
#: :data:`MAX_REFUSED_FRAMES` counts CONSECUTIVE refusals and is deliberately
#: forgiven by one readable frame - right for a paired add-on with a bug, and no
#: bound at all against a caller holding no credential, which can send nine
#: unreadable frames, then one well-formed ``browser.pairing_ack``, and repeat for
#: as long as it likes: the refusal count resets, the WARNING budget resets with
#: it, and the D06A pairing deadline is only consulted on an IDLE tick that a
#: continuously sending socket never reaches. This bound is on the LIFETIME of a
#: socket that has not authenticated, and nothing forgives it. The real add-on
#: sends exactly one frame in that state - its pairing ack - so the ceiling is
#: generous by an order of magnitude and still ends the flood.
MAX_UNPAIRED_FRAMES = 20

#: Faults kept by :meth:`ExtensionBackend.error_ledger`. ``last_error`` is ONE
#: string and a successful round trip retires it, which is right for the card's
#: "Last problem" line and wrong for the question a user with a flaky browser
#: actually asks - why does it keep dropping. A browser that fails, recovers and
#: fails again leaves nothing behind in one string. The ledger is that history,
#: bounded; a repeat of the same detail bumps a count rather than taking a row, so
#: a flood of one fault cannot push the interesting one out of the window.
MAX_ERROR_LEDGER = 20

#: Longest fault sentence the ledger will keep, per row (v1.239.0).
#:
#: The row COUNT was bounded from the start; the row SIZE was not, and the two
#: bounds are only a bound together. An UNPAIRED socket is unauthenticated by
#: design — that is what the pairing handshake is for — and several fault
#: sentences quote what the caller sent. So a caller with no credential could pin
#: twenty rows of its own arbitrary-length text in the daemon, for the life of the
#: connection, simply by sending long rubbish. Ship 1 capped pending pairings for
#: exactly this reason; this is the same rule one layer down.
#:
#: 400 characters is enough for every sentence this module actually writes (the
#: longest is the oversized-frame refusal, which is well under it), so the cap
#: bites only on quoted caller text — the case it exists for.
MAX_ERROR_DETAIL_CHARS = 400


def clip_detail(detail: object) -> str:
    """One fault sentence, stripped and bounded by :data:`MAX_ERROR_DETAIL_CHARS`.

    A function rather than an inline slice so the bound can be driven on its own,
    and so every future writer of a fault string gets it for free. ``None`` and
    non-strings answer ``""`` — a ledger row that says nothing is worse than no
    row, because it occupies one of the twenty slots that hold real faults.

    A clipped sentence ENDS IN AN ELLIPSIS. Without the marker a cut sentence
    reads as a complete one that happened to stop there, which is how a truncated
    diagnostic becomes a misleading one.
    """
    text = str(detail or "").strip()
    if len(text) <= MAX_ERROR_DETAIL_CHARS:
        return text
    return text[: MAX_ERROR_DETAIL_CHARS - 3] + "..."

#: ``browser.event`` name -> bus event name. A frame whose event is absent here is
#: recorded as an extension error rather than published under a guessed name: an
#: event published with a name nothing subscribes to is invisible, which is worse
#: than a named refusal the status route can show.
_EVENT_BUS_NAMES: dict[str, str] = {
    P.EVENT_TAB_ACTIVATED: EVENT_TAB_ACTIVATED,
    P.EVENT_NAVIGATION_COMPLETED: EVENT_NAVIGATION_COMPLETED,
    P.EVENT_DOWNLOAD_COMPLETED: EVENT_DOWNLOAD_COMPLETED,
}

#: The only keys the ADD-ON may contribute to a download payload, read off the
#: protocol's own ``DownloadPayload`` so the wire contract has ONE definition.
#:
#: The whitelist is a security boundary rather than tidiness. The bus payload of
#: plan 10.2 carries ``local_path``, and the file tools read that key as a path the
#: daemon verified. Copying the add-on's dict wholesale would let a buggy — or
#: compromised — add-on simply SEND a ``local_path`` of its choosing, and every
#: consumer downstream would treat an unchecked string as a verified one.
DOWNLOAD_PAYLOAD_KEYS: tuple[str, ...] = tuple(P.DownloadPayload.__annotations__)

#: Completed downloads the backend remembers so an acting tool result can name one.
#: A bound, not a cache policy: the list exists to answer "did the action the model
#: just took produce a file", and an unbounded list over a long session is a growing
#: set of absolute paths into the user's private folders, kept for nothing.
MAX_TRACKED_DOWNLOADS = 16

#: Default seconds :meth:`ExtensionBackend.await_download` may wait for a download
#: to finish. A BOUND, never a performance claim, and no test asserts an elapsed
#: duration against it. Waiting is what lets plan 10.3's one agent-facing capability
#: exist — the completed download's absolute path appears in the result of the
#: action that started it — and the bound is what stops an ordinary click, which
#: starts no download at all, from becoming a tool call that never returns.
DOWNLOAD_SETTLE_S = 5.0


def absolute_local_path(claim: Any) -> str:
    """Return ``claim`` when it is an absolute local path, otherwise ``""``.

    Chromium documents ``DownloadItem.filename`` as an absolute local path, and
    that documentation is the whole reason plan 10.3 needs no native messaging
    host. It is still the ADD-ON's claim, arriving over a socket, so the string is
    checked here before anything calls it a path.

    The silent failure this catches: a relative or invented ``filename`` becomes a
    ``local_path`` the model hands to ``read_document``, which resolves a relative
    path against the SESSION WORKSPACE — so the daemon reads, or fails to read, a
    completely different file while every surface reports the download as found.

    Both path flavours are asked, never only the host's. A POSIX path is not
    absolute to :class:`PureWindowsPath` and a drive path is not absolute to
    :class:`PurePosixPath`, so a single-flavour check would call a genuine path a
    fake one on the other operating system — and this function is pinned from a
    suite that runs on both.
    """
    if not isinstance(claim, str):
        return ""
    text = claim.strip()
    if not text or "\x00" in text:
        return ""
    if PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute():
        return text
    return ""


def download_bus_payload(reported: Any) -> tuple[dict[str, Any], str]:
    """Turn one add-on download report into the bus payload of plan 10.2.

    Returns ``(payload, problem)``. ``problem`` is ``""`` when the reported
    ``filename`` verified as absolute, and the payload then carries ``local_path``;
    otherwise ``problem`` is a sentence naming what the browser claimed and the
    payload has NO ``local_path`` key at all.

    **The absent key is the point.** It is the same rule the tab list follows when
    it sends ``null`` instead of ``""`` for a title it cannot read: a
    ``local_path`` that is present but wrong is read by every consumer as verified,
    while one that is absent makes the model say it cannot find the file instead of
    reading the wrong one.
    """
    source = reported if isinstance(reported, Mapping) else {}
    payload: dict[str, Any] = {
        key: source[key] for key in DOWNLOAD_PAYLOAD_KEYS if key in source
    }
    verified = absolute_local_path(payload.get("filename"))
    if not verified:
        claimed = payload.get("filename")
        return payload, (
            "your browser reported a completed download without an absolute local "
            f"path (filename={claimed!r}), so Iron Jarvis will not hand that path to "
            "a file tool"
        )
    payload["local_path"] = verified
    return payload, ""


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
        #: Every frame this connection has delivered, refused ones included. Only
        #: consulted while it is UNPAIRED (:data:`MAX_UNPAIRED_FRAMES`): it is the
        #: one count a caller with no credential cannot reset by behaving for a
        #: single frame, and it never decreases.
        self.frames_seen = 0

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
            logger.debug(
                "browser socket send failed (%s)", frame.get("type"), exc_info=True
            )
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
        snapshot_invalidator: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.clock = clock
        self.access_reader = access_reader
        #: ``(tab_id: int | None) -> None``, installed by
        #: :class:`~iron_jarvis.browser.service.BrowserRuntime` (Ship 2). Called when
        #: a tab NAVIGATES and when this socket goes away, so the daemon's snapshot
        #: cache stops naming a page that no longer exists. Held as a callable
        #: because the transport owns no policy and no cache: it knows a page moved,
        #: and nothing about what a snapshot is.
        self.snapshot_invalidator = snapshot_invalidator
        #: ``async (conn, action, params) -> None``, installed by
        #: :func:`iron_jarvis.browser.panel.install`. The SIDE PANEL's whole
        #: reach into the daemon. Held as a callable for the same reason the
        #: two above are: this class is the transport, it holds no policy and
        #: knows nothing about chat, and a panel handler imported here would
        #: drag the entire chat lane into the socket module. ``None`` means no
        #: panel handler was installed, which is REPORTED to the panel rather
        #: than dropped — a Send that does nothing, silently, is the failure
        #: the whole sidebar was written against.
        self.panel_handler: Any = None
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
        #: The connection error ledger: every fault :meth:`note_error` recorded,
        #: oldest first, bounded by :data:`MAX_ERROR_LEDGER`. NOT cleared when a
        #: successful round trip retires ``last_error``, and not cleared by a new
        #: connection - losing the history at the moment the browser recovers is
        #: exactly what makes an intermittent fault undiagnosable.
        self.errors: list[dict[str, Any]] = []
        #: Cached active tab. Ship 2's ambient context reads this rather than
        #: making a round trip, because a prompt assembly that awaits a browser is
        #: a prompt assembly that can hang. THREE writers, and it needs all three:
        #: ``browser.event tab_activated`` (a tab SWITCH), ``navigation_completed``
        #: (a move inside the tab that is already active), and every successful
        #: ``active_tab`` command (see :meth:`command`) — because with only the
        #: first, a user who navigates in place leaves this naming the page BEFORE
        #: the one they are looking at, for the rest of the connection.
        self.active_tab: dict[str, Any] | None = None
        #: Completed downloads this browser reported, oldest first, each with a
        #: ``claimed`` flag. Plan 10.3 adds NO ``browser_download`` tool and no new
        #: file tool; the one agent-facing capability it does add is that the
        #: absolute path of a completed download appears in the result of the
        #: browser action that started it, so the model can hand that path straight
        #: to ``read_document``, ``extract_pdf`` or ``list_folder``. This list is
        #: where the path waits between the add-on's event and that result.
        self._downloads: list[dict[str, Any]] = []
        #: Replaced (not merely set) by :meth:`_notify_downloads` on every recorded
        #: download, so :meth:`await_download` waits on an EVENT rather than polling
        #: a clock — the daemon's single event loop serves every route, and a spin
        #: here is felt as the whole application going slow. A waiter captures this
        #: attribute BEFORE it checks for a download, which is what closes the
        #: lost-wakeup window between "nothing yet" and "now waiting".
        self._download_signal = asyncio.Event()

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
            logger.debug(
                "browser access read failed while building a ready frame", exc_info=True
            )
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

    def note_error(self, detail: str) -> None:
        """Record one transport fault: the ``last_error`` line AND a ledger row.

        Every assignment to :attr:`last_error` that means "something went wrong"
        goes through here, so the two cannot disagree - a second writer that sets
        the string directly is a fault the ledger never saw, and a ledger is only
        worth reading if it is complete.

        A repeat of the same sentence bumps the previous row's ``count`` instead of
        taking a new one. Without that, ten refused frames are ten identical rows
        and the twenty-row window holds one fault; with it, the window holds twenty
        DISTINCT faults, which is the thing worth keeping.
        """
        # CLIPPED AT THE ENTRY POINT, not at each caller. Every "something went
        # wrong" string in this module arrives here, so one clip covers all of
        # them — and covers the next one somebody adds without reading this.
        text = clip_detail(detail)
        if not text:
            return
        self.last_error = text
        if self.errors and self.errors[-1].get("detail") == text:
            row = self.errors[-1]
            row["count"] = int(row.get("count") or 1) + 1
            row["at"] = self._stamp()
            return
        self.errors.append({"at": self._stamp(), "detail": text, "count": 1})
        del self.errors[:-MAX_ERROR_LEDGER]

    def _stamp(self) -> str:
        """``clock()`` as an ISO string, never raising. A ledger row is diagnostics."""
        try:
            return self.clock().isoformat()
        except Exception:  # noqa: BLE001 - an injected clock must not break a fault path
            return ""

    def error_ledger(self) -> list[dict[str, Any]]:
        """Copies of the ledger rows, oldest first.

        Copies because a caller that mutated a row would rewrite the record of what
        happened, and this is the one structure in the transport whose whole value
        is that it was not edited after the fact.
        """
        return [dict(row) for row in self.errors]

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
            # The history behind that one line. The status ROUTE decides whether to
            # forward it; the transport's duty is to have kept it.
            "recent_errors": self.error_ledger(),
        }

    # --- connection lifecycle --------------------------------------------

    def connected_payload(self, conn: ExtensionConnection) -> dict[str, Any]:
        """The ``browser.connected`` payload of plan 10.2, from ONE definition.

        It is published from two places — :meth:`adopt`, and :meth:`_handle_hello`
        when the greeting contradicts what adoption knew — and the two held separate
        copies of the dict. A key added to one of them is a key the other event
        silently lacks, and a consumer reading the stream then sees the same browser
        described two different ways.

        ``access`` is the live ``browser_access`` word, and it is OMITTED rather
        than guessed when no reader is installed: ``"off"`` on a hunch would tell
        every consumer the user had switched the capability off. The pairing token
        never appears here. The extension id does, and it is public.
        """
        payload: dict[str, Any] = {
            "extension_id": conn.extension_id,
            "extension_version": conn.extension_version,
            "host_permission": conn.host_permission,
        }
        access = self.access_word()
        if access:
            payload["access"] = access
        return payload

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
                logger.info(
                    "browser connection replaced with %d command(s) in flight", failed
                )
            await self._publish(
                EVENT_DISCONNECTED,
                {
                    "reason": "replaced",
                    "detail": "a newer browser connection authenticated",
                },
            )
        await conn.send(self.ready_frame(active=True))
        self.last_error = ""
        await self._publish(
            EVENT_CONNECTED,
            self.connected_payload(conn),
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
        # Every cached snapshot belonged to the browser that has just gone. Keeping
        # them would leave the daemon holding page text — and password-field
        # metadata — for a session that is over, and would let a snapshot_id from
        # the old browser resolve against a new one.
        self._invalidate_snapshots(None)
        # And the download list, for the same reason: every path in it is an
        # absolute path into the user's private folders, remembered only so the
        # NEXT tool call on this browser could name the file. There is no next
        # call — and a path claimed against a new browser would attribute one
        # browser's download to another's click.
        self._downloads.clear()
        if word == "error" or detail:
            # The ledger's other half. A disconnect that carries a reason is the
            # fault a user watching an intermittently dropping browser most needs to
            # see, and it is the one fault that passes through no frame handler.
            # Recorded only for the socket that WAS authoritative, because release
            # runs twice for the same connection on the ordinary path.
            self.note_error(
                f"the browser disconnected ({word}): {detail}"
                if detail
                else f"the browser disconnected ({word})"
            )
        await self._publish(EVENT_DISCONNECTED, {"reason": word, "detail": detail})

    # --- downloads --------------------------------------------------------

    @property
    def recent_downloads(self) -> list[dict[str, Any]]:
        """Copies of the remembered download payloads, oldest first.

        Copies rather than the live rows: a caller that mutated one would change
        what a later tool result reports, and the ``claimed`` bookkeeping belongs to
        this object alone.
        """
        return [dict(record["payload"]) for record in self._downloads]

    def _notify_downloads(self) -> None:
        """Wake every current waiter, and hand the next one a fresh event.

        Swapping the object rather than ``set()``-then-``clear()`` is what makes
        :meth:`await_download` free of a lost wakeup: a waiter that captured the old
        event is woken by this ``set``, and a waiter that arrives afterwards holds
        the new one and is unaffected by a completion it already saw.
        """
        signal, self._download_signal = self._download_signal, asyncio.Event()
        signal.set()

    def record_download(
        self, reported: Any, *, claimed: bool = False
    ) -> dict[str, Any]:
        """Verify one reported download, remember it, and return the bus payload.

        Args:
            reported: the add-on's ``DownloadPayload``, from a ``download_completed``
                event frame or from an acting method's ``result.download``.
            claimed: ``True`` when the caller is already delivering this payload to a
                model — an acting tool's own result — so no later call reports the
                same file a second time.

        Never raises. A malformed report must not break the socket the user's whole
        browser rides on.

        An unverifiable path is NOT remembered: :meth:`claim_download` would
        otherwise hand a tool result a path that points at nothing. The caller still
        gets the payload back and still publishes the event, because the download
        really did complete — the user's own downloads list is the truth about where
        it went, and "it finished, and I could not verify where" is honest where
        silence is not.

        A repeat of a ``download_id`` REPLACES the earlier record rather than adding
        one, because the same completion legitimately arrives twice: once on an
        acting method's result and once on the event frame. Two records would let the
        model be told about one file twice, and it would then copy it into the
        project twice. ``claimed`` is sticky across that replacement — a file already
        named in a result stays named.
        """
        payload, problem = download_bus_payload(reported)
        if problem:
            self.note_error(problem)
            logger.debug("browser download not verified: %s", problem)
            return payload
        download_id = payload.get("download_id")
        was_claimed = False
        if download_id is not None:
            for record in list(self._downloads):
                if record["payload"].get("download_id") == download_id:
                    was_claimed = was_claimed or bool(record["claimed"])
                    self._downloads.remove(record)
        self._downloads.append({"payload": payload, "claimed": claimed or was_claimed})
        del self._downloads[:-MAX_TRACKED_DOWNLOADS]
        self._notify_downloads()
        return payload

    def claim_download(self, tab_id: Any = None) -> dict[str, Any] | None:
        """The newest unclaimed completed download for ``tab_id``, or ``None``.

        CLAIMED, not merely read. A download is reported in exactly one tool result;
        repeating it on every later browser call would tell the model that each click
        produced a file, and "put the statement in the project" would then run three
        times on one statement.

        A record whose ``tab_id`` the add-on could not determine matches any tab.
        Chromium's ``DownloadItem`` carries no tab at all, so the add-on's
        attribution is the tab that was active when the transfer started; refusing to
        name a real file the user just downloaded because Chrome would not say which
        tab started it would lose the path entirely, which is the outcome plan 10.3
        exists to prevent.
        """
        wanted = (
            tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None
        )
        for record in reversed(self._downloads):
            if record["claimed"]:
                continue
            owner = record["payload"].get("tab_id")
            if wanted is not None and isinstance(owner, int) and owner != wanted:
                continue
            record["claimed"] = True
            return dict(record["payload"])
        return None

    async def await_download(
        self, tab_id: Any = None, *, timeout_s: float = DOWNLOAD_SETTLE_S
    ) -> dict[str, Any] | None:
        """Claim a completed download for ``tab_id``, waiting up to ``timeout_s``.

        Returns ``None`` rather than raising when nothing arrives: a click that
        starts no download is the ordinary case, not a failure, and an error here
        would turn every ordinary click into a failed tool call.

        The wait is a BOUND and nothing asserts an elapsed duration against it. It
        never polls — see :meth:`_notify_downloads` — so a browser that downloads
        nothing costs the event loop one suspended task and no CPU.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout_s))
        while True:
            # Captured BEFORE the claim, so a download recorded between the two is
            # still waiting on THIS event object rather than on the one that follows.
            signal = self._download_signal
            claimed = self.claim_download(tab_id)
            if claimed is not None:
                return claimed
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(signal.wait(), remaining)
            except (TimeoutError, asyncio.TimeoutError):
                return None

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
        result = await self._await_response(
            lambda request_id: P.command_frame(request_id, method, params),
            bound=bound,
            label=method,
        )
        if method == P.METHOD_ACTIVE_TAB and result:
            # THE CACHE LEARNS FROM EVERY LIVE ANSWER, not only from events.
            # ``active_tab`` is written by ``tab_activated``, which the add-on
            # emits on a tab SWITCH — so a user who navigates inside the tab they
            # are already on leaves the cache naming the PREVIOUS page, and the
            # ambient block then states the wrong title and URL on every later
            # turn. Anything that asks the browser for the active tab has just
            # been told the truth; caching it here, in the object that owns the
            # cache, is one owner and no extra round trip. (The ambient block
            # still never triggers one: it reads this attribute and never calls.)
            self.active_tab = dict(result)
        if result.get("download") is not None:
            # A download also arrives on the RESULT of the action that started it:
            # plan 10.3 puts the path in the answer to the call the model made,
            # because an event the model never sees is a path it cannot hand to
            # ``read_document``. It gets exactly the verification the event path
            # gets — a result is not a more trustworthy channel than an event
            # merely because the daemon asked for it — and it is recorded as
            # already CLAIMED, so the ``download_completed`` frame that follows
            # cannot report the same file to a second tool call.
            result = dict(result)
            result["download"] = self.record_download(
                result.get("download"), claimed=True
            )
        return result

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
                    self.note_error(f"{label} timed out after {bound:g}s")
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
        if conn.restricted:
            # Counted BEFORE the frame is measured or parsed, because the cheapest
            # possible answer to a caller that has already spent its budget is the
            # right one. See MAX_UNPAIRED_FRAMES: the consecutive-refusal bound
            # below cannot see this caller at all, since one well-formed pairing
            # ack every ninth frame forgives it forever.
            conn.frames_seen += 1
            if conn.frames_seen > MAX_UNPAIRED_FRAMES:
                self.note_error(
                    f"a browser that has not paired sent {conn.frames_seen} frames; "
                    f"the limit before pairing is {MAX_UNPAIRED_FRAMES}, so the "
                    "connection was closed"
                )
                logger.warning("closing unpaired browser socket: %s", self.last_error)
                # 1002 and never 1008: the add-on deletes its stored pairing token
                # on a 1008 (extensions/chrome/src/bridge/socket.ts onClose), and a
                # second browser flooding this socket must not cost the user - whose
                # own browser is paired - their credential.
                await conn.close(CLOSE_PROTOCOL_ERROR)
                if conn.pairing_request_id:
                    self._restricted.pop(conn.pairing_request_id, None)
                return False
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
            return await self._refuse_frame(
                conn, "your browser sent a frame that is not JSON"
            )
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
        self.note_error(why)
        if conn.refused_frames <= MAX_REFUSAL_WARNINGS:
            logger.warning("browser frame refused: %s", why)
        else:
            logger.debug("browser frame refused (%d): %s", conn.refused_frames, why)
        if conn.refused_frames >= MAX_REFUSED_FRAMES:
            self.note_error(
                f"your browser sent {conn.refused_frames} frames Iron Jarvis could not "
                f"read, so the connection was closed; the last one: {why}"
            )
            logger.warning("closing browser socket: %s", self.last_error)
            await conn.close(CLOSE_PROTOCOL_ERROR)
            if conn.pairing_request_id:
                self._restricted.pop(conn.pairing_request_id, None)
            return False
        return True

    async def handle_frame(
        self, conn: ExtensionConnection, frame: dict[str, Any]
    ) -> bool:
        """Dispatch one parsed frame. ``False`` means the socket has been closed.

        The restricted-state rule of D06A is enforced here rather than at the
        route: a frame of any type other than
        :data:`~iron_jarvis.browser.protocol.RESTRICTED_INBOUND_FRAMES` on an
        unpaired socket closes it with 1008, from inside the state machine, so the
        rule holds for every caller including one that forgot to check.
        """
        kind = str(frame.get("type") or "")
        if conn.restricted and kind not in P.RESTRICTED_INBOUND_FRAMES:
            # `kind` is CALLER-CONTROLLED, so it is clipped BEFORE the sentence is
            # built. Clipping only inside note_error would still format a
            # caller-sized string on the event loop first, which is most of the
            # cost this bound exists to deny.
            self.note_error(
                f"an unpaired browser sent {clip_detail(kind) or 'an unnamed frame'}"
            )
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
        if kind == P.FRAME_PANEL:
            await self._handle_panel(conn, frame)
            return True
        # An unknown type on a PAIRED socket is recorded, not fatal: a newer add-on
        # speaking a frame this daemon predates must not take the connection down,
        # or an upgrade on either side becomes an outage.
        self.note_error(f"your browser sent an unknown frame type {kind!r}")
        return True

    async def _handle_panel(
        self, conn: ExtensionConnection, frame: dict[str, Any]
    ) -> None:
        """One ``browser.panel`` frame from the side panel (v1.242.0).

        THE SILENT FAILURE THIS PREVENTS: a sidebar whose buttons do nothing
        and say nothing. Every path out of here either runs the action or
        sends a ``browser.panel_event`` ``error`` naming the reason — an
        inbound panel frame is never dropped on the floor, which is exactly
        what happened before this branch existed (it fell through to the
        unknown-type note and the user watched a spinner forever).

        TWO GATES, both fail-closed, and neither is this module's policy:

        * **Unpaired.** ``browser.panel`` is deliberately absent from
          :data:`~iron_jarvis.browser.protocol.RESTRICTED_INBOUND_FRAMES`, so a
          socket that has not paired never reaches this method at all — the
          restricted-state check at the top of :meth:`handle_frame` closes it
          with 1008 first. A panel belongs to a browser the user pressed Pair
          for; a local process that never paired asking the daemon to run a
          chat turn is not an early panel.
        * **Browser access.** ``off`` — and an unreadable or unrecognised
          setting — refuses the action entirely. The word is read LIVE through
          :meth:`access_word` at frame time, never captured, because the user
          may have switched the capability off since this socket connected.
          The refusal is a spoken one: the panel is told why, and what to do.

        The access check here is not a second policy. It is the SAME
        ``config.browser_access`` word ``BrowserRuntime.access`` normalises,
        and the tools a panel turn may use are gated again, independently, by
        the ordinary chat arming path (``_filter_browser_tools``). This gate
        exists so that ``off`` costs a refusal instead of a turn.
        """
        action = str(frame.get("action") or "")
        raw_params = frame.get("params")
        params = dict(raw_params) if isinstance(raw_params, Mapping) else {}
        access = self.access_word()
        if access not in PANEL_ACCESS_ALLOWED:
            self.note_error(
                "the sidebar asked Iron Jarvis to work while Browser access is off"
            )
            await conn.send(
                P.panel_event_frame(
                    P.PANEL_EVENT_ERROR,
                    {
                        "text": "Browser access is off, so Iron Jarvis will not"
                        " run anything from this sidebar. Turn it on from"
                        " the Browser page in Iron Jarvis.",
                        "reason": "browser_access_off",
                    },
                )
            )
            return
        handler = self.panel_handler
        if handler is None:
            await conn.send(
                P.panel_event_frame(
                    P.PANEL_EVENT_ERROR,
                    {
                        "text": "This Iron Jarvis cannot run a sidebar"
                        " conversation.",
                        "reason": "no_panel_handler",
                    },
                )
            )
            return
        try:
            await handler(conn, action, params)
        except Exception as exc:  # noqa: BLE001 — a failed action must SPEAK
            logger.debug("panel action failed (%s)", action, exc_info=True)
            self.note_error(f"a sidebar action failed: {clip_detail(exc)}")
            await conn.send(
                P.panel_event_frame(
                    P.PANEL_EVENT_ERROR,
                    {"text": "Iron Jarvis could not do that from the sidebar."},
                )
            )

    async def _handle_hello(
        self, conn: ExtensionConnection, frame: dict[str, Any]
    ) -> None:
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
        conn.extension_version = str(
            frame.get("extension_version") or conn.extension_version
        )
        conn.host_permission = bool(frame.get("host_permission"))
        conn.hello_seen = True
        after = (conn.extension_id, conn.extension_version, conn.host_permission)
        if after != before and self._conn is conn:
            await self._publish(
                EVENT_CONNECTED,
                self.connected_payload(conn),
            )

    def _resolve_response(
        self, conn: ExtensionConnection, frame: dict[str, Any]
    ) -> None:
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
            self.note_error(f"your browser reported an unknown event {name!r}")
            return
        if name == P.EVENT_TAB_ACTIVATED:
            self.active_tab = payload or None
        elif name == P.EVENT_NAVIGATION_COMPLETED:
            if self.active_tab and (
                payload.get("tab_id") == self.active_tab.get("id")
                or payload.get("tab_id") == self.active_tab.get("tab_id")
            ):
                merged = {**self.active_tab, **payload}
                if payload.get("url") and payload.get("url") != self.active_tab.get(
                    "url"
                ):
                    # A DIFFERENT DOCUMENT. Any page-authored key the navigation
                    # did not restate describes the page that just went away, and
                    # a merge would leave page A's title sitting beside page B's
                    # URL — a row that is wrong in the most confident possible
                    # way, since each half looks right. Dropped, not kept: the
                    # ambient block omits a line it cannot fill, which is honest,
                    # and the next live ``active_tab`` refills it.
                    for stale_key in ("title", "text", "status", "page_version"):
                        if stale_key not in payload:
                            merged.pop(stale_key, None)
                self.active_tab = merged
            # The page this tab held is gone, so the snapshot taken of it is not a
            # stale VIEW of the same page — it describes a different document. The
            # element ids in it would resolve to nothing, and the daemon would say
            # so with no reason it could name. Dropping the cache entry makes the
            # next action STALE_SNAPSHOT, whose remedy is "call browser_read_page".
            # A navigation the add-on could not place (no ``tab_id``) drops EVERY
            # cached snapshot rather than none: one unnecessary re-read is cheap, and
            # a kept snapshot of a page that has been replaced is a wrong answer.
            self._invalidate_snapshots(payload.get("tab_id"))
        elif name == P.EVENT_DOWNLOAD_COMPLETED:
            # THE ADD-ON'S CLAIM BECOMES THE DAEMON'S FACT ONLY AFTER A CHECK.
            # ``filename`` arrives as Chromium's absolute local path; ``local_path``
            # — the key the file tools read — is added here, by the daemon, or not
            # at all. Every other key the add-on sent is dropped by the whitelist,
            # which is what stops a buggy or compromised add-on from simply SENDING
            # a ``local_path`` of its own and having the whole application treat it
            # as verified.
            payload = self.record_download(payload)
        await self._publish(bus_name, payload)

    def _invalidate_snapshots(self, tab_id: Any) -> None:
        """Tell the runtime to forget a tab's snapshot; ``None`` means every tab.

        Never raises into the socket handler and never awaits: the invalidator is
        dictionary work by contract (see
        :meth:`~iron_jarvis.browser.service.BrowserRuntime.invalidate_snapshot`).
        The v1.229.0 lesson applies literally — a call whose job is to keep the
        daemon honest must not become the thing that drops the connection.
        """
        hook = self.snapshot_invalidator
        if hook is None:
            return
        try:
            hook(tab_id)
        except Exception:  # noqa: BLE001 — never break a socket over a cache
            logger.debug("browser snapshot invalidation failed", exc_info=True)

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
    "DOWNLOAD_PAYLOAD_KEYS",
    "DOWNLOAD_SETTLE_S",
    "EVENT_CONNECTED",
    "EVENT_DISCONNECTED",
    "EVENT_DOWNLOAD_COMPLETED",
    "EVENT_NAVIGATION_COMPLETED",
    "EVENT_TAB_ACTIVATED",
    "MAX_REFUSAL_WARNINGS",
    "MAX_REFUSED_FRAMES",
    "MAX_TRACKED_DOWNLOADS",
    "ExtensionBackend",
    "ExtensionConnection",
    "absolute_local_path",
    "download_bus_payload",
]
