"""Browser Bridge routes: the add-on's socket, and the Your browser card's HTTP surface.

``/browser/ws`` is the only WebSocket in this app that does NOT accept the install
bearer token (D06, plan §12.3). It accepts exactly one credential — the browser
pairing token — and exactly one non-loopback origin, the pinned add-on's
(``auth._extension_origin_ok``). Everything else closes with 1008.

This module is deliberately THIN. The protocol state machine, the frame size cap,
the pending-future map and the D08 replacement sequence all live in
:class:`~iron_jarvis.browser.extension_backend.ExtensionBackend`, and the
credential lives in :class:`~iron_jarvis.browser.pairing.PairingStore`; the routes
reach both through :class:`~iron_jarvis.browser.service.BrowserRuntime`. That split
is not tidiness: a route that re-implemented the restricted-frame rule, or the size
check, would be a SECOND policy, and one of the two would eventually be wrong
without anything looking different (the "one definition" rule this repository
learned at v1.231.0). So what is genuinely this module's, and nowhere else:

* **Guarding the credential BEFORE ``accept()``**, the same discipline as
  ``/events`` (``routes/system.py``): a socket that is accepted and then closed has
  already completed a handshake with a caller we had decided to refuse.
* **Driving the read loop**, and racing it against the pairing deadline with
  ``asyncio.wait(..., FIRST_COMPLETED)`` — again copying ``/events``. Without the
  receiver in that race, an add-on that goes away while the user has not pressed
  Pair leaves this coroutine parked and the card offering a Pair button for a
  browser that is gone.
* **Enforcing the pairing deadline on the socket** (D06A). ``PairingStore`` owns
  the clock and hands back expired records from ``expired()``; only the route holds
  the socket, so only the route can close it with 1008.
* **Retiring the connection in a ``finally``**, so a dropped socket fails its
  in-flight futures instead of leaving a tool call hanging forever.
* **Refusing to make ANY socket authoritative while ``browser_access`` is off.**
  "Off drops the live socket" is a promise a close cannot keep on its own: the
  add-on treats every non-1008 close as ordinary and reconnects about a second
  later, and 1008 would make it delete its stored pairing token. So a
  token-bearing socket that arrives while access is off is accepted, told
  ``browser.ready {active: false, access: "off"}``, and held INERT — never
  ``_conn``, so nothing can command it, ``GET /browser/status`` says not
  connected, and its frames are read and discarded rather than caching a tab or
  publishing a page title. The decision is re-read on every idle tick, so
  switching access back on promotes that same socket instead of making the user
  reconnect. The refusal is the DAEMON's: an add-on that chose to reconnect
  anyway changes nothing, which is the difference between enforcement and a
  client being polite.

Two things this module reads that the card contract depends on, and their reasons:

* ``GET /browser/status`` reads ``backend.status()`` and the pairing store, NOT
  ``runtime.status()``. The runtime's version makes a live round trip to the
  browser, which is right for the ``browser_get_status`` TOOL and wrong for a card
  that polls: a wedged add-on would make every poll wait out the 15-second command
  timeout, and the page the user opened to find out that their browser is wedged
  would be the page that hangs. The plan says this route never fails; a route that
  takes 15 seconds has failed.
* ``POST /browser/test`` runs the runtime's own ``active_tab`` against a
  ``_ReadOnlyRuntime`` view — a READ method, and the only method this route may
  ever send (D25: "Do not make Test mutate the page"). ``TEST_METHOD`` names it so
  the read-only property is a thing a test asserts against ``protocol.READ_METHODS``
  rather than a promise in a docstring, and the view is what makes the property
  hold for the NEXT edit as well: a method outside ``READ_METHODS`` is refused
  before it reaches the socket instead of being sent to a real, signed-in browser.
  The view is TOTAL, which took a second pass to be true — a method reached
  THROUGH it is re-bound to the view, so a round trip that delegates to a sibling
  still speaks through the read-only transport; ``directive`` is refused outright
  (``disconnect`` would drop the browser Test was pressed to measure); and the
  transport's other attributes are an allowlist, because ``command`` is not the
  only route to a frame and a denylist would admit the next one by default.

The plaintext pairing token never passes through this module: ``complete_pairing``
mints it and hands it to the one ``browser.paired`` frame, and
``POST /browser/pair`` answers ``{"paired": true}``.

Moved-into-routes convention: closure-local state is reached through ``d`` (see the
deps object built in create_app). ``d.platform.browser`` is a coordinator edit to
``platform.py`` and every access here is guarded, so the daemon boots and every
route degrades honestly while that field does not yet exist.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import types
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..auth import browser_ws_token_ok
from ..schemas import SettingsBody
from ...browser import protocol as P
from ...browser.errors import BrowserError, BrowserErrorCode, browser_error
from ...browser.extension_backend import ExtensionConnection
from ...browser import panel as _panel
from ...browser.identity import pinned_extension_id
from ...browser.service import ACCESS_OFF

# The FUNCTION, imported directly, because ``iron_jarvis.onboarding``'s package
# __init__ rebinds the name ``doctor`` from the MODULE to the ``doctor()``
# function -- ``from ...onboarding import doctor`` hands back a callable with no
# ``browser_addon_dir`` on it. Tests drive this through the real
# ``IRONJARVIS_BROWSER_ADDON_DIR`` env override the resolver reads per call, which
# is the seam the desktop supervisor itself uses.
from ...onboarding.doctor import browser_addon_dir

logger = logging.getLogger("iron_jarvis.browser")

#: Fallback pairing deadline, used only when the store does not carry its own
#: ``deadline_s``. ``PairingStore`` is the source of truth (a test shortens it
#: there rather than sleeping); this exists so a stand-in store cannot make the
#: socket immortal. Read at call time, so it is also a monkeypatch seam — the same
#: shape ``_MAX_UPLOAD_BYTES`` has on ``daemon/app.py``.
_PAIRING_DEADLINE_S = P.PAIRING_DEADLINE_S

#: How long the unpaired read waits before re-checking the deadline. A SLICE, not
#: the deadline itself: the pairing future is resolved by another task
#: (``POST /browser/pair`` → ``deliver_pairing``), so a single wait of the whole
#: deadline would still be parked when the socket became authoritative and would
#: then close a PAIRED browser with 1008. Nothing asserts this duration.
_PAIRING_POLL_S = 0.25

#: How long a socket held INERT (Browser access is off) waits before re-reading the
#: access level. A SLICE, like ``_PAIRING_POLL_S``: the setting is changed by another
#: task (``PUT /settings``), and nothing asserts this duration.
_ACCESS_POLL_S = 0.25

#: The ONE method ``POST /browser/test`` may send, and a member of
#: ``protocol.READ_METHODS`` — asserted by test, because a diagnostic that
#: navigated or clicked would be the worst possible button to press twice (D25).
TEST_METHOD = P.METHOD_ACTIVE_TAB

#: The Origin prefix an add-on sends. Used only to learn the extension id before
#: ``browser.hello`` arrives; the ORIGIN itself is authorised by
#: ``auth._extension_origin_ok`` against the pinned id, never here.
_EXTENSION_ORIGIN_PREFIX = "chrome-extension://"


#: The pairing socket's path, named once so the doctor can say it out loud.
WS_PATH = "/browser/ws"

#: Every path this module serves. Declared here, and VERIFIED against the app's own
#: route table at the end of :func:`register` -- a declaration alone would be a
#: constant read twice, which is not evidence that anything is being served.
SERVED_PATHS: tuple[str, ...] = (
    WS_PATH,
    "/browser/status",
    "/browser/pair",
    "/browser/disconnect",
    "/browser/forget",
    "/browser/test",
    "/browser/request-host-permission",
    "/browser/setup/arm",
    "/browser/setup/disarm",
)

#: What :func:`register` actually put on the LAST app registered in this process.
#: The per-app answer is :data:`SERVED_ATTR` on that app's platform; this is the
#: fallback for a caller that has no platform to ask.
_SERVED: frozenset[str] = frozenset()

#: Where :func:`register` records the served set for the platform it registered
#: with, so :func:`served_paths` can answer for THAT app rather than for whichever
#: one registered most recently. A module global is a fine answer in production --
#: one process builds one app -- but it is an answer about the process, and the
#: doctor is handed a platform and asks about the install it belongs to. A second
#: ``create_app`` anywhere (a helper, a future embedded mode, a half-built app
#: constructed after the real one) would otherwise make this row describe the wrong
#: route table with total confidence, and nothing would look different.
SERVED_ATTR = "browser_served_paths"


def served_paths(platform: Any = None) -> frozenset[str]:
    """The paths of :data:`SERVED_PATHS` really registered for *platform*.

    The doctor's browser row reads this (D25's "WebSocket route"). It answers
    ``frozenset()`` in a process that never built an app, which is the truth: the
    add-on's socket has nowhere to connect there. The alternative -- asserting the
    constant tuple -- would report a served socket in a build where the ``register``
    call had been deleted, which is exactly the failure the row exists to name.

    With no *platform* (or one that never went through :func:`register`) this falls
    back to the process-wide record, which is what every caller had before.
    """
    if platform is not None:
        try:
            served = getattr(platform, SERVED_ATTR, None)
        except Exception:  # noqa: BLE001 - a half-built platform may raise
            served = None
        if isinstance(served, frozenset):
            return served
    return _SERVED


class PairBody(BaseModel):
    """``POST /browser/pair`` — the pending request id the card is offering.

    Defined here rather than in ``daemon/schemas.py`` because that module is
    coordinator-owned; ``routes/fleet.py`` and ``routes/projects.py`` already keep
    local request models this way.
    """

    request_id: str = ""


class SetupArmBody(BaseModel):
    """``POST /browser/setup/arm`` — the access mode the setup modal chose, or none.

    ``None`` (the default) means "leave ``browser_access`` exactly as it is". A
    default of ``read_only`` here would be a capability the user never chose being
    switched on by a button labelled Set up, which is the failure the whole
    off-by-default arrangement exists to prevent.
    """

    access: str | None = None


def _runtime(d) -> Any:
    """``d.platform.browser``, or ``None`` while that field does not exist yet.

    ``Platform.browser`` is added by the coordinator's edit to ``platform.py``.
    Until then — and on any install where the browser package failed to build —
    these routes must still answer: ``GET /browser/status`` is documented never to
    fail, and the card polls it on every visit. A missing runtime is "not
    connected", never a 500 the page renders as "daemon offline".
    """
    platform = getattr(d, "platform", None)
    return getattr(platform, "browser", None)


def _addon_dir() -> str:
    """The absolute add-on folder Chrome must be pointed at here, or ``""``.

    ONE resolver, the doctor's (:func:`onboarding.doctor.browser_addon_dir`), which
    already answers this question for the ``browser_addon`` row: the env override
    the desktop supervisor exports (``IRONJARVIS_BROWSER_ADDON_DIR``, set by
    ``desktop/main.js`` when the bundled add-on carries a manifest), then the
    packaged ``<resources>/browser-addon``, then a source checkout. A second
    implementation of "where is the add-on" in this module is the one-definition
    failure this repository keeps paying for (v1.231.0), and the two would disagree
    on exactly the install where it mattered.

    WHY THE ROUTE CARRIES IT AT ALL. The next physical act after reading the card is
    typing this folder into Chrome's *Load unpacked* picker, and a folder NAME is
    not something a file picker can resolve. It is not a secret -- it is a directory
    inside the user's own installation, and the doctor already prints it -- so it
    carries no authorisation beyond the one every route in this module already has.

    Never raises, and never waits: a handful of ``stat`` calls, on a route
    documented never to fail. An unresolvable folder is ``""``, which the card
    renders as "this install could not find it" rather than as a path.
    """
    try:
        folder = browser_addon_dir()
    except Exception:  # pragma: no cover - defensive; the resolver swallows OSError
        logger.debug("browser add-on folder lookup degraded", exc_info=True)
        return ""
    return str(folder) if folder is not None else ""


# --------------------------------------------------------------- setup window

#: How long ``POST /browser/setup/arm`` leaves the door open, in seconds.
#:
#: THE SILENT FAILURE A BOUND PREVENTS: while the window is open Jarvis acts on a
#: browser's behalf without being asked to -- it sends the add-on the directive
#: that opens its own site-access page. An always-armed version would open a tab
#: at a user who never pressed anything, and would keep the dashboard claiming a
#: setup was in progress forever. Two minutes is long enough to open Chrome's
#: extensions page and press Load unpacked, and short enough that the window is
#: shut again before the user has walked away from the machine. NOTHING behind
#: this window mints a credential: see the note where ``_pinned_socket`` was.
SETUP_WINDOW_S = 120.0

#: Where the deadline lives: an attribute on the ``BrowserRuntime`` this install
#: built, not a module global. One process builds one app, but a module global is
#: an answer about the PROCESS -- the same reasoning :data:`SERVED_ATTR` carries --
#: and a second app in a test would then share one window with the first.
SETUP_DEADLINE_ATTR = "setup_window_until"


def _monotonic() -> float:
    """The clock the setup window is measured on, in one place.

    MONOTONIC, never the wall clock. THE SILENT FAILURE: a window stored as a
    ``time.time()`` deadline is reopened by a clock that steps backwards -- an NTP
    correction, a laptop resuming from sleep, a user fixing their timezone -- and
    a setup window the user closed two hours ago is open again with nothing
    anywhere saying so. ``time.monotonic`` cannot go backwards.

    A module-level function rather than a bare ``time.monotonic`` reference so a
    test can drive expiry by moving a clock instead of by sleeping: an assertion
    that waits out a real 120 seconds measures the hardware, not the rule.
    """
    return time.monotonic()


def _setup_arm(runtime: Any, window_s: float = SETUP_WINDOW_S) -> float:
    """Open the setup window on *runtime*; returns the seconds it will last.

    Called from ONE place -- the explicit arm route. Never on boot, never by
    default: see :data:`SETUP_WINDOW_S`.

    RE-ARMING IS ALLOWED, and it re-bases the deadline from now rather than
    topping up whatever was left. A user who pressed Set up, went to find Chrome's
    extensions page and came back to an expired window genuinely needs longer, and
    the only way to ask for it is to press Set up again -- an explicit act, by the
    same person, on the same trusted surface. What is said here is what the code
    does: the docstring of the arm route once claimed the window "is not extended
    by use", which was never true of this line, and a bound described one way and
    implemented another is a bound nobody can reason about.
    """
    window = float(window_s)
    setattr(runtime, SETUP_DEADLINE_ATTR, _monotonic() + window)
    return window


def _setup_disarm(runtime: Any) -> None:
    """Shut the window. Idempotent, and never raises.

    A runtime that refuses the attribute is left alone rather than failing the
    route: Disarm is the SAFE direction, and a Disarm button that returned an error
    would leave a user who wanted the window shut believing it was still open.
    """
    try:
        setattr(runtime, SETUP_DEADLINE_ATTR, None)
    except Exception:  # noqa: BLE001 - shutting a door must not raise
        logger.debug("browser setup window could not be disarmed", exc_info=True)


def _setup_remaining_s(runtime: Any) -> float:
    """Seconds left on the setup window, or ``0.0`` when it is shut or expired.

    EXPIRY IS CHECKED HERE, ON EVERY USE, and there is deliberately no background
    timer to shut the window. THE SILENT FAILURE a timer would introduce: a task
    that failed to be scheduled, was cancelled with its loop, or raised on a tick
    leaves the window open forever, and nothing looks different from the outside --
    the deadline is still in the object, and every reader would still be trusting
    a sweeper that is no longer running. A deadline compared at the point of use
    cannot be left armed by a task that stopped.

    Fails CLOSED: a runtime whose deadline is unreadable or not a number counts as
    shut, because the failure direction of "armed" is handing a credential away.
    """
    try:
        deadline = getattr(runtime, SETUP_DEADLINE_ATTR, None)
    except Exception:  # noqa: BLE001 - an unreadable window is a shut one
        return 0.0
    if deadline is None:
        return 0.0
    try:
        left = float(deadline) - _monotonic()
    except (TypeError, ValueError):
        return 0.0
    return left if left > 0.0 else 0.0


def _setup_armed(runtime: Any) -> bool:
    """Whether the setup window is open right now.

    It gates CONVENIENCES ONLY -- the auto-grant directive, and the card's own
    "a setup is in progress" hint. It has never gated, and must never gate, the
    minting of a credential: nothing but a human press does that.
    """
    return _setup_remaining_s(runtime) > 0.0


def _setup_view(runtime: Any) -> dict[str, Any]:
    """The ``setup`` key of ``GET /browser/status``, from one definition.

    ``expires_in_s`` is rounded UP so that "armed" and "0 seconds left" can never be
    reported together: a card that renders a countdown would show an open window
    with no time on it, and a reader cannot tell that from a bug.
    """
    left = _setup_remaining_s(runtime)
    if left <= 0.0:
        return {"armed": False, "expires_in_s": 0}
    # A microsecond of slack before rounding up (v1.246.1). The deadline is
    # ``monotonic() + window``, and on a clock that has not ticked since the
    # arm (Windows: ~16 ms resolution), ``(m + 120.0) - m`` can come back a
    # hair ABOVE 120 in floating point whenever ``m + 120`` crosses a power of
    # two (the sum rounds at the coarser spacing) — ``ceil`` then reported
    # 121 s on a 120-second window, and the v1.246.0 release gate went red on
    # exactly that, ~30 min into a fresh runner's uptime. ``max(1, …)`` keeps
    # the rule above: an armed window never reads 0.
    return {"armed": True, "expires_in_s": max(1, int(math.ceil(left - 1e-6)))}


#: THERE IS NO ``_pinned_socket`` GATE ANY MORE, AND THERE MUST NOT BE ONE.
#: It existed to decide whether a credential could be minted for a socket without
#: a human pressing Pair, on the strength of the extension id in the ``Origin``
#: header. That is not a decision an id can carry: ``Origin`` is a header the
#: client writes, ``/browser/ws?pairing=1`` is the one endpoint that needs no
#: credential, and the pinned id is a PUBLIC constant in every install. Anything
#: running as the user can therefore present it. The id is kept only so a pairing
#: row can name who asked -- see :func:`_origin_extension_id`, which says the same
#: thing -- and the human pressing Pair is the whole security boundary.


def _disconnected_status() -> dict[str, Any]:
    """The status shape for "there is no browser here", in one place.

    ``addon_dir`` is resolved HERE rather than in the connected branch because it
    is a fact about the disk, not about the socket: the card needs it precisely
    when nothing is connected, and the ``runtime is None`` branch returns this
    shape unmodified.
    """
    return {
        "connected": False,
        "access": "off",
        "host_permission": False,
        "extension_id": "",
        # Spelled in BOTH shapes on purpose: a key that exists only when
        # something is connected makes every reader write a second branch, and
        # the one that forgets renders a comparison against `undefined`.
        "extension_version": "",
        # The id the daemon EXPECTS, as opposed to ``extension_id`` above, which is
        # whatever is connected right now (empty when nothing is). Public material,
        # and the card needs it precisely when nothing is connected: a pairing
        # request arrives from an unauthenticated socket, and this is the only fact
        # that distinguishes the real add-on from anything else on the machine.
        "expected_extension_id": pinned_extension_id(),
        # The folder a user points Chrome's Load unpacked at, absolute, or "" when
        # this install cannot find one. See :func:`_addon_dir`.
        "addon_dir": _addon_dir(),
        "active_tab": None,
        "pending_pairing": None,
        "paired": False,
        "last_error": None,
        # The setup window, SHUT unless an arm route opened one. Present in the
        # disconnected shape too, because the card that renders the countdown is
        # exactly the card a user is looking at while nothing is connected yet.
        "setup": {"armed": False, "expires_in_s": 0},
    }


#: Every attribute of the real transport a READ path may reach through the view.
#: Tiny on purpose, and the only two ``BrowserRuntime``'s read path actually uses:
#: ``connected`` (through ``getattr(self.backend, "connected", False)`` -- which is
#: why a refusal here must NOT be an ``AttributeError``, or it would silently become
#: ``False``) and ``status()``, the cached transport view that makes no round trip.
_READABLE_TRANSPORT_ATTRS = frozenset({"connected", "status"})


class _ReadOnlyTransport:
    """The transport ``POST /browser/test`` speaks through: READ methods, nothing else.

    Not a second policy -- it grants nothing and takes nothing away, and every
    access decision is still the runtime's. It is a STOP placed between the
    diagnostic and the socket so that "Test does not mutate the page" (D25) is a
    property of the code path rather than a promise in a docstring. Test is a
    button a worried user presses twice; a frame that clicked or navigated would be
    sent to their real, signed-in browser, and no test of the *current* method
    protects the NEXT edit that points the round trip somewhere else.

    A refused method raises rather than returning empty: a diagnostic that quietly
    did nothing would report "Round-trip OK" while never speaking to the browser.

    **The gate is an ALLOWLIST, and it has to be.** ``command`` is not the only way
    to put a frame on the socket: ``directive`` sends one (``disconnect`` would drop
    the user's browser mid-diagnostic), and ``_await_response``, ``deliver_pairing``,
    ``release`` and ``connection.send`` all reach it too. A ``__getattr__`` that
    forwarded everything and refused a list would admit the NEXT one added by
    default, which is the same "true for the current edit only" property this class
    exists to remove. So exactly the attributes a READ path uses pass through
    (:data:`_READABLE_TRANSPORT_ATTRS`) and everything else is refused BY NAME --
    loudly, so an edit that legitimately needs another read is told to widen the set
    rather than discovering silence.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        #: Every method that reached the socket through here, for the caller's log.
        self.sent: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name not in _READABLE_TRANSPORT_ATTRS:
            raise BrowserError(
                BrowserErrorCode.EXTENSION_ERROR,
                message=(
                    f"the browser test refused to reach {name!r} on the transport: "
                    "Test is read-only and may use only a read attribute"
                ),
            )
        return getattr(self._backend, name)

    async def directive(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Always refused. No directive is a read.

        The two that exist act on the SESSION -- ``disconnect`` drops the socket and
        ``request_host_permissions`` opens a page in the user's browser -- so a Test
        that sent either would change the thing it was called to measure.
        """
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            message=(
                f"the browser test refused to send the directive {action!r}: Test is "
                "read-only and a directive acts on your browser session"
            ),
        )

    async def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if str(method) not in P.READ_METHODS:
            raise BrowserError(
                BrowserErrorCode.EXTENSION_ERROR,
                message=(
                    f"the browser test refused to send {method!r}: Test is read-only "
                    "and may send only a read method"
                ),
            )
        self.sent.append(str(method))
        return await self._backend.command(method, params, timeout_s=timeout_s)


class _ReadOnlyRuntime:
    """*runtime* with :class:`_ReadOnlyTransport` in place of its transport.

    A view, not a copy: every other attribute is the live runtime's, so the access
    gate, the pairing store and the snapshot cache are the real ones and no state
    is duplicated. The runtime object itself is NOT modified -- swapping its
    ``backend`` for the duration of a request would be shared mutable state, and a
    tool call arriving on another task mid-test would find a transport that refuses
    to click.

    **The view is TOTAL, one level was not enough.** A plain forwarding
    ``__getattr__`` hands back the attribute off the REAL runtime, so a method
    fetched through the view is BOUND TO THE REAL RUNTIME and its ``self.backend``
    is the real transport. That made the guarantee true only for a direct
    ``self.backend.command`` inside ``active_tab`` itself -- and the likeliest next
    edit is exactly the other shape, ``active_tab`` delegating to a sibling such as
    ``resolve_page_tab``, whose own backend call would have gone straight to the
    socket. So a plain function found on the runtime's class is re-bound to THIS
    view, and the read-only transport follows the call however deep it goes.
    Properties, classmethods, staticmethods and instance attributes are forwarded
    untouched: those are not methods this view can be the ``self`` of.
    """

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self.backend = _ReadOnlyTransport(runtime.backend)

    def __getattr__(self, name: str) -> Any:
        cls = type(self._runtime)
        for klass in cls.__mro__:
            if name in vars(klass):
                found = vars(klass)[name]
                # ``types.FunctionType`` and nothing looser: a ``property`` object
                # must be evaluated against the real instance, and a ``staticmethod``
                # or ``classmethod`` takes no ``self`` for this view to be.
                if isinstance(found, types.FunctionType):
                    return found.__get__(self, cls)
                break
        return getattr(self._runtime, name)


def _refuse(code: BrowserErrorCode | str, status: int, message: str = "") -> HTTPException:
    """An HTTPException whose ``detail`` is a STRING carrying the code and remedy.

    A dict detail would render as ``[object Object]``: ``lib/api.ts``'s
    ``flattenDetail`` only walks a LIST, and ``String({})`` is what every other
    shape becomes. So the code is prefixed into the sentence instead — the card
    shows a remedy, and the code stays greppable in a bug report.

    The code comes from ``browser_error``, never from ``str(code)``:
    ``BrowserErrorCode`` is a ``str`` mixin whose ``str()`` is still
    ``"BrowserErrorCode.EXTENSION_ERROR"``, which is exactly the leak that enum's
    own docstring warns about.
    """
    envelope = browser_error(code)
    return HTTPException(
        status_code=status, detail=f"{envelope['code']}: {message or envelope['message']}"
    )


def _error_body(code: BrowserErrorCode | str, elapsed_ms: int, message: str = "") -> dict[str, Any]:
    """The ``POST /browser/test`` failure body: ok false, with a code and a remedy."""
    envelope = browser_error(code)
    return {
        "ok": False,
        "detail": message or envelope["message"],
        "code": envelope["code"],
        "round_trip_ms": elapsed_ms,
        "active_tab": None,
    }


def _origin_extension_id(ws: WebSocket) -> str:
    """The add-on's id from the Origin header, or ``""``.

    Only to give the backend (and so the pairing row) an id before
    ``browser.hello`` arrives — a pairing socket never sends hello at all, because
    it had no token when it opened, so without this every first pairing row would
    be recorded with an empty ``extension_id``. It authorises NOTHING: the origin
    was already checked against the pinned id by ``HostOriginGuardMiddleware``,
    which is the only place that decision may be made.
    """
    origin = (ws.headers.get("origin") or "").strip().rstrip("/")
    if not origin.lower().startswith(_EXTENSION_ORIGIN_PREFIX):
        return ""
    return origin[len(_EXTENSION_ORIGIN_PREFIX) :]


def _access_off(runtime: Any) -> bool:
    """Whether Browser access is off, read LIVE and failing closed.

    Read on every call for the reason ``BrowserRuntime.access`` is: ``PUT /settings``
    mutates the live ``Config``, and a socket decision made from a value cached at
    boot is a capability the user switched off that keeps working until a restart. A
    runtime that cannot answer counts as off — the fail-safe direction.
    """
    try:
        return str(runtime.access() or ACCESS_OFF).strip() == ACCESS_OFF
    except Exception:  # noqa: BLE001 — an unreadable setting must not widen access
        logger.debug("browser access unreadable; treating it as off", exc_info=True)
        return True


def _preferred_pending(rows: list[dict[str, str]]) -> dict[str, str] | None:
    """The pending pairing ask the card should offer, or ``None``.

    Oldest-first is the store's order and it is the wrong choice ALONE: a local
    process that reopens a pairing socket every second is always the oldest offer, so
    a user who has just loaded the real add-on and presses Pair would hand the
    credential to whatever asked first. A request whose ``extension_id`` is the pinned
    add-on's therefore wins over one that has no id (any local process can open a
    pairing socket — ``/browser/ws?pairing=1`` is the one endpoint that needs no
    credential), and among equals the oldest still wins so the offer is stable across
    polls. The card renders the id beside the ask, because preferring the right row is
    not the same as telling the user which row they are approving.
    """
    if not rows:
        return None
    pinned = pinned_extension_id()
    for row in rows:
        if str(row.get("extension_id") or "") == pinned:
            return row
    return rows[0]


def _deadline_s(store: Any) -> float:
    """The pairing deadline, from the store that owns it."""
    try:
        return float(getattr(store, "deadline_s", _PAIRING_DEADLINE_S))
    except (TypeError, ValueError):
        return float(_PAIRING_DEADLINE_S)


#: Marker set on a connection once the setup window has DECIDED about its host
#: permission, so the directive is sent at most once per socket. Set whether or not
#: a directive went out: the decision is made once, when the facts are known.
_AUTO_GRANT_ATTR = "_ij_setup_grant_decided"


async def _auto_grant(runtime: Any, conn: Any) -> bool:
    """Ask an adopted browser for site access while the window is open.

    THE SILENT FAILURE: the add-on connects, everything reads "Connected", and every
    page read fails with a permission error the user has no obvious remedy for --
    the grant lives behind a button inside the add-on, on a page nothing opened.
    Inside the setup window that page is opened for them.

    ``{"requested": true}`` is all this can ever mean, here as on
    ``POST /browser/request-host-permission``: ``chrome.permissions.request()``
    needs a user gesture, so Jarvis asks and the human still clicks.

    Decided at most once per socket, and only when the window is open and the socket
    reports NO host permission. Never raises: a browser that is already granted, or
    wedged, must not lose its connection to a convenience.

    THE SILENT FAILURE THE ADOPTION CHECK PREVENTS: this runs as a background task,
    so a D08 replacement can land between the moment it was scheduled for socket A
    and the moment it runs. It reads ``conn.host_permission`` -- socket A's fact --
    but ``request_host_permission`` sends to whatever socket is authoritative NOW.
    Without the check it decides about A and delivers to B: a browser that had
    already granted access gets a setup page opened in front of the user, and the
    browser that had not been asked never is. A socket that is no longer the
    adopted one is skipped, and an unreadable backend is skipped too -- not sending
    a convenience is the safe direction.
    """
    if getattr(conn, _AUTO_GRANT_ATTR, False):
        return False
    if not _setup_armed(runtime):
        return False
    setattr(conn, _AUTO_GRANT_ATTR, True)
    if bool(getattr(conn, "host_permission", False)):
        return False
    try:
        adopted = runtime.backend.connection
    except Exception:  # noqa: BLE001 - an unreadable backend decides about nothing
        logger.debug("browser auto-grant could not read the adopted socket", exc_info=True)
        return False
    if adopted is not conn:
        logger.info("browser auto-grant skipped: this socket is no longer the adopted one")
        return False
    try:
        await runtime.request_host_permission()
    except BrowserError as exc:
        logger.info("browser auto-grant not delivered: %s", exc.code)
        return False
    except Exception:  # noqa: BLE001 - a convenience must never drop the socket
        logger.debug("browser auto-grant failed", exc_info=True)
        return False
    logger.info("browser asked for site access inside the setup window")
    return True


#: Where the in-flight auto-grant task is parked, so the loop keeps a strong
#: reference to it. ``asyncio`` holds only a weak one, and a task nothing refers to
#: can be collected mid-await — the directive would simply never be sent, on some
#: runs and not others.
_AUTO_GRANT_TASK_ATTR = "_ij_setup_grant_task"


def _schedule_auto_grant(runtime: Any, conn: Any) -> Any:
    """Run :func:`_auto_grant` as a background task; returns it, or ``None``.

    IT MUST NOT BE AWAITED BY THE READER, and this is not a preference.
    ``request_host_permission`` sends a ``browser.directive`` and then AWAITS the
    add-on's response — and the only code that reads frames off this socket is the
    pump. Awaiting it from the socket handler (or from inside the pump's own loop)
    parks the one coroutine that could deliver the answer, so the directive times
    out after the full command bound while the browser's reply sits unread in the
    socket. The user would see a browser that connects and then goes silent for
    fifteen seconds, with nothing naming why.

    Returns ``None`` when there is no running loop to schedule on, which is only
    true outside the ASGI server.
    """
    if getattr(conn, _AUTO_GRANT_ATTR, False):
        return None
    try:
        task = asyncio.get_running_loop().create_task(_auto_grant(runtime, conn))
    except RuntimeError:  # pragma: no cover - no running loop
        return None
    setattr(conn, _AUTO_GRANT_TASK_ATTR, task)
    return task


def _settings_writer(app: FastAPI) -> Any:
    """The app's OWN ``PUT /settings`` handler, looked up off its route table.

    ONE definition of "write a setting": validation on a throwaway copy, the undo
    journal, the atomic persist and the live re-arm that DROPS the paired socket
    when ``browser_access`` moves to ``off`` all live in ``routes/settings.py`` and
    none of them is re-implemented here. THE SILENT FAILURE a second writer would
    cause: arming would set the field on the live ``Config`` and skip the persist,
    so the access mode the user chose in the setup modal would be gone at the next
    boot with nothing to show they had chosen it.

    Read off ``app.routes`` rather than imported so registration ORDER does not
    matter and a build where the settings routes were not registered answers
    ``None`` -- which the arm route reports as a refusal rather than pretending the
    setting was written.
    """
    for route in app.routes:
        if str(getattr(route, "path", "")) != "/settings":
            continue
        if "PUT" in (getattr(route, "methods", None) or set()):
            return getattr(route, "endpoint", None)
    return None


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    # THE SIDE PANEL (v1.242.0). Installed at registration, from the ONE place
    # that holds both the deps object and the browser backend: the transport
    # knows nothing about chat and the runtime knows nothing about the deps
    # shim, so neither of them can wire this. Without it an inbound
    # `browser.panel` frame reaches a backend with no handler, and the panel is
    # told so — never left spinning.
    _panel.install(d)

    # ----------------------------------------------------------------- socket

    async def _open_pairing(runtime: Any, conn: ExtensionConnection) -> bool:
        """Register an unpaired socket and offer it a request id. False if it failed.

        ``open_request`` runs in a thread for the same reason every other
        ``PairingStore`` call does: the store is documented blocking (a lock, and a
        prune), and the daemon is ONE loop.
        """
        store = getattr(runtime, "pairing", None)
        if store is None:
            return False
        record = await asyncio.to_thread(
            lambda: store.open_request(extension_id=conn.extension_id)
        )
        if record is None:
            # The registry is full (PairingStore.MAX_PENDING_PAIRINGS). This socket
            # needed no credential to get here, so a flood must cost the daemon one
            # refusal and nothing else: close THIS socket and leave every pairing
            # already in flight — including the user's real browser — untouched.
            return False
        conn.pairing_request_id = record.request_id
        runtime.backend.register_restricted(conn)
        return await conn.send(P.pairing_required_frame(record.request_id))

    async def _pairing_lapsed(runtime: Any, conn: ExtensionConnection, expires_at: float) -> bool:
        """Whether this unpaired socket has run out of time (D06A).

        Two signals, both needed. ``PairingStore.expired()`` is the store's own
        verdict and the reason it retires records instead of deleting them — it is
        drained here so the retired list stays bounded. But ``expired()`` hands each
        record out exactly ONCE, so a second socket's tick can drain the record this
        socket was waiting for; the absolute ``expires_at`` computed at open time is
        the backstop that makes the close independent of who drained what.

        Neither signal fires for a request that was CONSUMED by pairing: ``mint``
        drops it from the registry without retiring it, so a socket mid-pairing is
        never mistaken for an abandoned one.
        """
        store = getattr(runtime, "pairing", None)
        if store is not None:
            try:
                retired = await asyncio.to_thread(store.expired)
            except Exception:  # noqa: BLE001 — a store failure must not kill the socket
                logger.debug("pairing sweep failed", exc_info=True)
                retired = []
            if any(record.request_id == conn.pairing_request_id for record in retired):
                return True
        return asyncio.get_running_loop().time() >= expires_at

    async def _pump(
        ws: WebSocket, runtime: Any, conn: ExtensionConnection, *, inert: bool = False
    ) -> None:
        """Read frames until the socket ends, then retire the connection.

        Every inbound frame goes to ``backend.handle_raw``, which measures BEFORE it
        parses (the 512 KB cap, on the single event loop — v1.153.1) and enforces the
        restricted-frame rule from inside the state machine. A ``False`` from it
        means the socket has already been closed and must not be read again.

        ``inert`` is a credentialled socket the daemon refuses to make authoritative
        because Browser access is OFF. Its frames are read and DISCARDED — never
        handed to ``handle_raw`` — because a ``browser.event`` from it would cache the
        user's active tab and publish their page's title on the bus while the
        capability that authorises reading their browser is switched off. The access
        level is re-read on every idle tick, so switching it back on promotes this
        socket in place instead of making the user reconnect.

        A PAIRED, ADOPTED socket now polls on the same slice, for the other half
        of that story: the access word is re-read and RE-ANNOUNCED whenever it
        moves, so switching Browser off is pushed to a browser that is already
        connected instead of leaving its sidebar header claiming a capability
        the daemon is refusing. Nothing here asserts a duration.
        """
        backend = runtime.backend
        loop = asyncio.get_running_loop()
        expires_at = loop.time() + _deadline_s(getattr(runtime, "pairing", None))
        # The access word this socket was last TOLD. Seeded with what adopt()
        # (or the inert ready frame) just sent, so the first tick re-announces
        # only a word that has actually moved since.
        announced_access = backend.access_word()
        reason, detail = "closed", ""
        recv = asyncio.ensure_future(ws.receive())
        try:
            while True:
                if inert:
                    done, _ = await asyncio.wait(
                        {recv}, timeout=_ACCESS_POLL_S, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not done:
                        if _access_off(runtime):
                            continue
                        # The user turned Browser access back on: this socket may
                        # become the authoritative one now, and adopt() sends the
                        # browser.ready that says so.
                        await backend.adopt(conn)
                        # adopt() sent a ready frame carrying this word; the
                        # paired branch below must not repeat it.
                        announced_access = backend.access_word()
                        inert = False
                        _schedule_auto_grant(runtime, conn)
                        continue
                    try:
                        message = recv.result()
                    except (WebSocketDisconnect, RuntimeError):
                        break
                    if message.get("type") == "websocket.disconnect":
                        break
                    recv = asyncio.ensure_future(ws.receive())
                    continue
                if conn.paired:
                    done, _ = await asyncio.wait(
                        {recv}, timeout=_ACCESS_POLL_S,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        # THE ACCESS WORD IS PUSHED ON EVERY CHANGE (v1.242.0).
                        # `access` reached the add-on only in `browser.ready`,
                        # sent at adopt() and on the off->on recovery above —
                        # there was NO push on on->off for a socket that was
                        # already adopted. So a user who turned Browser off
                        # left the sidebar header reading "Interactive" while
                        # the daemon refused every panel frame, and the panel's
                        # own `body[data-access="off"]` rule — which hides the
                        # composer — never fired in the one state it was
                        # written for. A header that names a capability the
                        # daemon is refusing is the exact dishonesty this
                        # product forbids, so the word is re-read on the same
                        # slice the inert path already uses and re-announced
                        # whenever it MOVES (never on every tick: a repeated
                        # identical ready frame is noise on a socket that also
                        # carries commands).
                        word = backend.access_word()
                        if word != announced_access:
                            announced_access = word
                            await conn.send(
                                backend.ready_frame(active=not _access_off(runtime))
                            )
                        continue
                else:
                    done, _ = await asyncio.wait(
                        {recv}, timeout=_PAIRING_POLL_S, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not done:
                        if await _pairing_lapsed(runtime, conn, expires_at):
                            await conn.close(1008)
                            reason, detail = "closed", "the pairing deadline passed"
                            break
                        continue
                try:
                    message = recv.result()
                except (WebSocketDisconnect, RuntimeError):
                    break
                if message.get("type") == "websocket.disconnect":
                    break
                raw = message.get("text")
                if raw is None:
                    raw = message.get("bytes") or b""
                if not await backend.handle_raw(conn, raw):
                    # The backend closed it: a restricted socket sent something
                    # other than its pairing ack. Its own last_error already says
                    # which frame type, so nothing is added here.
                    reason, detail = "closed", "the browser broke the pairing protocol"
                    break
                # AUTO-GRANT, at the only moment the answer is knowable. ``adopt()``
                # runs before a single frame has been read, so host_permission is
                # still its default there; ``browser.hello`` is the frame that
                # carries it. ``_auto_grant`` decides once, and only while the setup
                # window is open.
                if conn.paired and getattr(conn, "hello_seen", False):
                    _schedule_auto_grant(runtime, conn)
                recv = asyncio.ensure_future(ws.receive())
        finally:
            recv.cancel()
            try:
                # In a finally, always: a socket that dies mid-command must fail its
                # in-flight futures rather than leave a tool call awaiting forever.
                await backend.release(conn, reason=reason, detail=detail)
            except Exception:  # noqa: BLE001 — teardown must not raise into ASGI
                logger.debug("browser connection release failed", exc_info=True)

    @app.websocket(WS_PATH)
    async def browser_ws(ws: WebSocket) -> None:
        """The add-on's one socket. A pairing token, or ``?pairing=1``; nothing else.

        The install bearer is refused here by construction: ``browser_ws_token_ok``
        consults the pairing store and never ``IRONJARVIS_TOKEN``, and it reads the
        query string only — so an ``Authorization`` header cannot smuggle a
        credential in either (§12.3).
        """
        runtime = _runtime(d)
        if runtime is None or getattr(runtime, "backend", None) is None:
            # Close before accept rather than accept and then fail every frame.
            await _close(ws, 1008)
            return
        token = (ws.query_params.get("token") or "").strip()
        wants_pairing = (ws.query_params.get("pairing") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if token:
            if not await browser_ws_token_ok(ws, runtime.verify_token):
                await _close(ws, 1008)
                return
        elif not wants_pairing:
            # Fail closed. Neither a token nor the pairing flag is not a bootstrap
            # attempt, it is an unauthenticated caller.
            await _close(ws, 1008)
            return

        await ws.accept()
        conn = ExtensionConnection(
            ws, extension_id=_origin_extension_id(ws), paired=bool(token)
        )
        inert = False
        if token:
            if _access_off(runtime):
                # OFF MEANS OFF, AND THE REFUSAL IS THE DAEMON'S. Closing the socket
                # is not enough: the add-on treats any non-1008 close as ordinary and
                # reconnects about a second later, and 1008 would make it DELETE its
                # pairing token (socket.ts onClose) — so a browser whose user has
                # switched Browser access off would otherwise be back, authoritative,
                # in one second, with the card reading "Connected". This socket is
                # therefore never adopted: it holds no _conn, so `backend.connected`
                # is False, GET /browser/status says not connected, and every command
                # refuses at BrowserRuntime.require. The add-on is TOLD, in the
                # protocol's own words — browser.ready {active: false, access: "off"}
                # — instead of being left to guess from a close code, and its frames
                # are discarded while it waits. Nothing here trusts the client.
                inert = True
                await conn.send(runtime.backend.ready_frame(active=False))
            else:
                # adopt() sends browser.ready and performs the D08 replacement,
                # including failing every command in flight on the outgoing socket.
                await runtime.backend.adopt(conn)
        elif not await _open_pairing(runtime, conn):
            await _close(ws, 1008)
            return
        # AND NOTHING IS PAIRED HERE. A pairing socket leaves with an OFFER and
        # nothing else, however the setup window is set: the id this socket is
        # named by comes from its own ``Origin`` header, this endpoint is the one
        # that needs no credential, and the pinned id is public material shipped in
        # every install (see the note where ``_pinned_socket`` used to be). A
        # credential minted on that basis is minted for whoever asked first, and it
        # locks the real add-on out afterwards while the card reads "Connected".
        # The human pressing Pair is what tells the two apart, so the press stays.
        await _pump(ws, runtime, conn, inert=inert)

    # ------------------------------------------------------------------ HTTP

    @app.get("/browser/status")
    async def browser_status() -> dict[str, Any]:
        """Everything the Your browser card renders. NEVER fails, and never waits.

        Reads the backend's CACHED view plus the pairing store; it does not command
        the browser. See the module docstring: the live round trip belongs to the
        ``browser_get_status`` tool, and a card poll that waits out a 15-second
        command timeout is the page the user opened to diagnose a wedged browser
        hanging on the wedged browser.
        """
        runtime = _runtime(d)
        if runtime is None:
            return _disconnected_status()
        answer = _disconnected_status()
        try:
            view = dict(runtime.backend.status())
            answer.update(
                {
                    "connected": bool(view.get("connected")),
                    "access": str(runtime.access() or "off"),
                    "host_permission": bool(view.get("host_permission")),
                    "extension_id": str(view.get("extension_id") or ""),
                    # THE VERSION CHROME IS ACTUALLY RUNNING (v1.242.0). The
                    # add-on ships inside the installer, but Chrome keeps the
                    # copy it loaded until it restarts -- so an updated app can
                    # be talking to a build from two versions ago, whose icon
                    # still opens the retired popup and has no sidebar at all.
                    # The add-on reported this on `browser.hello` all along and
                    # NOTHING compared it to anything; forwarding it is what
                    # lets the card say "reload it" instead of the user
                    # wondering why the sidebar they just read about is missing.
                    "extension_version": str(view.get("extension_version") or ""),
                    "active_tab": view.get("active_tab") or None,
                    "last_error": view.get("last_error") or None,
                }
            )
        except Exception:
            logger.debug("browser transport status degraded", exc_info=True)
        try:
            rows = await runtime.pending_pairings()
            answer["pending_pairing"] = _preferred_pending(rows)
        except Exception:
            logger.debug("pending pairing lookup degraded", exc_info=True)
        try:
            store = getattr(runtime, "pairing", None)
            answer["paired"] = bool(store is not None and await asyncio.to_thread(store.paired))
        except Exception:
            logger.debug("pairing lookup degraded", exc_info=True)
        try:
            answer["setup"] = _setup_view(runtime)
        except Exception:  # noqa: BLE001 - this route is documented never to fail
            logger.debug("browser setup window lookup degraded", exc_info=True)
        return answer

    @app.post("/browser/pair")
    async def browser_pair(body: PairBody) -> dict[str, bool]:
        """Approve a pending browser: mint the credential and deliver it on ITS socket.

        The response body is ``{"paired": true}`` and carries no token, ever — and
        it cannot, because ``complete_pairing`` does not return one. A token in a
        JSON body lands in browser devtools, in every HTTP trace, and in whatever
        the user pastes into a bug report.

        The code-to-status mapping is the plan's (§3.1) and each code exists so the
        card can say the right thing: a stale Pair button (404) and a browser that
        is already paired (409) need different words.
        """
        runtime = _runtime(d)
        if runtime is None:
            raise _refuse(
                BrowserErrorCode.EXTENSION_ERROR, 503, "the browser bridge is unavailable"
            )
        try:
            await runtime.complete_pairing((body.request_id or "").strip())
        except BrowserError as exc:
            status = 404 if exc.code == BrowserErrorCode.PAIRING_REQUIRED.value else 409
            raise _refuse(exc.code, status, exc.message) from exc
        # THE SETUP IS OVER, SO THE WINDOW SHUTS. THE SILENT FAILURE: a window left
        # armed by a successful pairing keeps running for the rest of its two
        # minutes with nothing on screen saying why -- the card still renders "a
        # setup is in progress", and a POST /browser/forget pressed inside the
        # leftover minute drops straight back into a setup state the user did not
        # ask for a second time. Shutting it here means the window always ends at
        # something the user did.
        _setup_disarm(runtime)
        return {"paired": True}

    @app.post("/browser/disconnect")
    async def browser_disconnect() -> dict[str, bool]:
        """End the live socket and KEEP the credential. Never fails.

        The pair with Forget below: Disconnect is "stop for now", Forget ends the
        relationship. Collapsing them would make one of the two buttons a lie.
        """
        runtime = _runtime(d)
        if runtime is None:
            return {"disconnected": False}
        try:
            # suspend=True: a person pressed a button that says stop, so the add-on
            # is asked to STAY away rather than reconnect a second later and undo it.
            # The add-on's own panel offers the inverse (Connect).
            return {"disconnected": bool(await runtime.disconnect(suspend=True))}
        except Exception:
            logger.debug("POST /browser/disconnect found nothing to close", exc_info=True)
            return {"disconnected": False}

    @app.post("/browser/forget")
    async def browser_forget() -> dict[str, bool]:
        """Revoke every pairing, then drop the socket. Never fails.

        ``forgotten`` is whether a credential actually died, not whether the button
        was pressed: reporting true with nothing revoked would tell the user their
        browser was forgotten while the add-on kept reconnecting silently.
        """
        runtime = _runtime(d)
        if runtime is None:
            return {"forgotten": False}
        try:
            return {"forgotten": bool(await runtime.forget())}
        except Exception:
            logger.debug("POST /browser/forget found no pairing to revoke", exc_info=True)
            return {"forgotten": False}

    @app.post("/browser/test")
    async def browser_test() -> dict[str, Any]:
        """A READ-ONLY round trip to the user's browser, reported honestly (D25).

        ``round_trip_ms`` is a REPORTED measurement, never a threshold: nothing
        asserts it here or in a test, and a slow browser is still a working one.
        """
        loop_start = asyncio.get_running_loop().time()

        def _ms() -> int:
            return int((asyncio.get_running_loop().time() - loop_start) * 1000)

        runtime = _runtime(d)
        if runtime is None:
            return _error_body(BrowserErrorCode.BROWSER_NOT_CONNECTED, _ms())
        # The runtime's OWN active_tab, applied to a read-only view of it. The
        # implementation is the service's (it applies the access gate before any
        # frame is sent, and maps "no window open" to None rather than an error);
        # what the view adds is that a method outside READ_METHODS cannot reach the
        # socket from here even if a later edit points this round trip at one.
        # Resolved off the CLASS because that is what binding to the view requires;
        # a runtime that carries no such method is refused rather than called
        # directly, because calling it directly is precisely the unguarded path.
        probe = getattr(type(runtime), "active_tab", None)
        if not callable(probe):
            return _error_body(
                BrowserErrorCode.EXTENSION_ERROR,
                _ms(),
                "this browser service cannot be diagnosed read-only",
            )
        try:
            tab = await probe(_ReadOnlyRuntime(runtime))
        except BrowserError as exc:
            return _error_body(exc.code, _ms(), exc.message)
        except Exception as exc:  # noqa: BLE001 — a diagnostic must never 500
            return _error_body(
                BrowserErrorCode.EXTENSION_ERROR, _ms(), f"{type(exc).__name__}: {exc}"
            )
        return {
            "ok": True,
            "detail": (
                "Round-trip OK — active tab received."
                if tab
                else "Round-trip OK — your browser reported no active tab."
            ),
            "code": "",
            "round_trip_ms": _ms(),
            "active_tab": dict(tab) if tab else None,
        }

    @app.post("/browser/request-host-permission")
    async def browser_request_host_permission() -> dict[str, bool]:
        """Ask the add-on to open its setup page so the user can grant site access.

        Jarvis cannot make the grant itself: ``chrome.permissions.request()`` needs
        a user gesture and cannot run in a service worker, and Jarvis is a page on
        another origin (plan §6, DEVIATION 1). So ``{"requested": true}`` means "the
        browser was asked", never "the grant was given" — the grant arrives later as
        a status change, and claiming otherwise would be a lie the card renders as
        success.
        """
        runtime = _runtime(d)
        if runtime is None:
            raise _refuse(BrowserErrorCode.BROWSER_NOT_CONNECTED, 409)
        try:
            await runtime.request_host_permission()
        except BrowserError as exc:
            raise _refuse(exc.code, 409, exc.message) from exc
        except Exception as exc:  # noqa: BLE001 — name the browser's failure, not a traceback
            raise _refuse(
                BrowserErrorCode.EXTENSION_ERROR, 409, f"{type(exc).__name__}: {exc}"
            ) from exc
        return {"requested": True}

    @app.post("/browser/setup/arm")
    async def browser_setup_arm(body: SetupArmBody) -> dict[str, Any]:
        """Open the time-boxed setup window, and optionally set the access mode.

        This route is the ONLY thing that opens the window. Nothing arms it at boot
        and no default arms it. The window expires :data:`SETUP_WINDOW_S` seconds
        after THIS call, measured on a monotonic clock, and expiry is checked
        wherever it is read rather than by a timer. Pressing Set up again re-bases
        that deadline from now (:func:`_setup_arm`): a user who needed longer asked
        for longer, explicitly, on the surface they are already looking at.

        THE SILENT FAILURE THE WINDOW REMOVES: setup asks a user to press Pair in
        Jarvis and Allow in Chrome at a moment they cannot predict, and the two
        prompts appear on two different surfaces. People give up between them, and
        an install that stops at "Waiting to pair" looks identical to one that is
        broken.

        WHAT THE WINDOW DOES NOT DO: it never mints a credential. The Pair press is
        the security boundary and this route does not move it — a socket that
        arrives inside an open window is offered a pairing exactly as it would be
        with the window shut, because the only thing naming it is an ``Origin``
        header it wrote itself and the pinned id is public. See the note where
        ``_pinned_socket`` used to be. What the window does is send the add-on the
        directive that opens its own site-access page (:func:`_auto_grant`), which
        hands over nothing, and tell the card a setup is in progress so the Pair
        button is put in front of the user instead of being hunted for.

        It shuts itself at the deadline, and ``POST /browser/pair`` shuts it on a
        successful pairing: a setup window outlives neither its bound nor its job.

        The access mode is written through the app's own ``PUT /settings`` handler
        (:func:`_settings_writer`), never through a second writer: that path
        validates, journals the undo, persists atomically and re-arms the live
        browser hook. A 400 from it is the setting's own refusal and is passed
        through unchanged.
        """
        runtime = _runtime(d)
        if runtime is None:
            raise _refuse(
                BrowserErrorCode.EXTENSION_ERROR, 503, "the browser bridge is unavailable"
            )
        access = (body.access or "").strip()
        if access:
            writer = _settings_writer(app)
            if writer is None:
                raise _refuse(
                    BrowserErrorCode.EXTENSION_ERROR,
                    503,
                    "this install cannot change the browser access setting",
                )
            # In a thread: ``put_settings`` is a SYNC handler that writes a file and
            # touches the database, and the daemon is ONE loop. An HTTPException it
            # raises (a rejected access word) propagates unchanged.
            await asyncio.to_thread(writer, SettingsBody(values={"browser_access": access}))
        window = _setup_arm(runtime)
        return {
            "armed": True,
            "expires_in_s": int(window),
            # The folder the modal tells the user to load, from the ONE resolver.
            "addon_dir": _addon_dir(),
        }

    @app.post("/browser/setup/disarm")
    async def browser_setup_disarm() -> dict[str, bool]:
        """Shut the setup window. Never fails, and is safe to call when it is shut.

        Never fails because it is the SAFE direction: a Cancel that returned an error
        would leave a user who wanted the window closed believing it was still open,
        which is the one state this feature must never be wrong about. A missing
        runtime answers ``false`` for the same reason — there is no window there.
        """
        runtime = _runtime(d)
        if runtime is not None:
            _setup_disarm(runtime)
        return {"armed": False}


    # What actually landed. Read off the app's own route table rather than trusted
    # from the tuple above, so a route that failed to register (or was renamed) is
    # reported missing by the doctor instead of being claimed as served.
    global _SERVED
    live = {str(getattr(route, "path", "")) for route in app.routes}
    _SERVED = frozenset(path for path in SERVED_PATHS if path in live)
    # And recorded ON the platform these routes were registered for, so the doctor
    # answers for the app that serves the install it was handed rather than for the
    # last app this process happened to build. Guarded: ``d`` may carry no platform
    # at all (the browser package is optional in a partial build), and a platform
    # that refuses the attribute simply keeps the module-global answer.
    try:
        setattr(d.platform, SERVED_ATTR, _SERVED)
    except Exception:  # noqa: BLE001 - registration must never fail on bookkeeping
        logger.debug("browser routes could not record served paths on the platform",
                     exc_info=True)


async def _close(ws: WebSocket, code: int = 1000) -> None:
    """Close a socket without ever raising into the handler.

    A close on a socket the peer already dropped raises, and an exception out of a
    WebSocket handler is not a policy close — it is an unhandled error, because
    FastAPI's exception handlers are HTTP-only.
    """
    try:
        await ws.close(code=code)
    except Exception:
        logger.debug("browser socket was already gone at close(%s)", code, exc_info=True)
