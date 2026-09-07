"""The Browser Bridge wire protocol — THE SINGLE SOURCE OF TRUTH for both sides.

Python is authoritative and TypeScript is generated: ``extensions/chrome/src/protocol.ts``
is written by :mod:`iron_jarvis.browser.gen_protocol` from the constants and
:class:`~typing.TypedDict` definitions in *this* module, and
``tests/test_browser_protocol_v1235.py`` regenerates into a buffer and asserts
byte equality with the committed file.

That arrangement exists because of the failure mode a hand-written second copy
guarantees. Two sides of a socket that each spell their own constants drift on
the day one side gains a frame type — and the drift is silent: the extension
sends ``browser.pairing_ack``, the daemon's state machine is looking for
``browser.pair_ack``, and the socket closes with 1008 and no words about which
name was wrong. A generated file makes that a red gate on the Python side, where
the gate always runs (the extension's own build is not part of the pytest suite).

What lives here, and nothing else:

* **Frame types** — every ``type`` string, split by direction, because the
  restricted-state machine of D06A refuses a frame by *type* and needs the
  daemon-to-extension and extension-to-daemon sets to be separately nameable.
* **Method names** — the ``method`` string of a ``browser.command`` frame, one
  per :class:`~iron_jarvis.browser.service.BrowserService` method.
* **Directive actions** and **event names** — the second discriminator on
  ``browser.directive`` and ``browser.event``.
* **Id prefixes** — ``req_`` for a daemon-minted command, ``evt_`` for an
  extension-originated event, ``pair_`` for a pairing request.
* **Frame ``TypedDict``s** — the shapes of plan section 9.6, verbatim.
* **The sensitive-autocomplete vocabularies**, re-exported from
  :mod:`iron_jarvis.computeruse.policy` so the content script scrubs by exactly
  the tokens the Python classifier escalates on. Restating them in TypeScript is
  the drift that would let a payment field cross the socket with a value.
* **Frame builders** — one function per frame, so no call site assembles a frame
  dict by hand and misspells a key.

This module imports from :mod:`iron_jarvis.computeruse` and
:mod:`iron_jarvis.browser.errors` only. The package contract holds: ``browser``
may import from ``computeruse``; ``computeruse`` never imports ``browser``.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from ..computeruse.policy import _PASSWORD_AUTOCOMPLETE, _PAYMENT_AUTOCOMPLETE
from .errors import REMEDIES, BrowserError, BrowserErrorCode, browser_error

PROTOCOL_VERSION = 1

# --------------------------------------------------------------------------- #
# Frame types
# --------------------------------------------------------------------------- #

#: Daemon -> extension.
FRAME_COMMAND = "browser.command"
FRAME_DIRECTIVE = "browser.directive"
FRAME_PAIRED = "browser.paired"
FRAME_PAIRING_REQUIRED = "browser.pairing_required"
FRAME_READY = "browser.ready"
FRAME_CONNECTION_REPLACED = "browser.connection_replaced"

#: Extension -> daemon.
FRAME_HELLO = "browser.hello"
FRAME_RESPONSE = "browser.response"
FRAME_EVENT = "browser.event"
FRAME_PAIRING_ACK = "browser.pairing_ack"

DAEMON_TO_EXTENSION: tuple[str, ...] = (
    FRAME_COMMAND,
    FRAME_DIRECTIVE,
    FRAME_PAIRED,
    FRAME_PAIRING_REQUIRED,
    FRAME_READY,
    FRAME_CONNECTION_REPLACED,
)

EXTENSION_TO_DAEMON: tuple[str, ...] = (
    FRAME_HELLO,
    FRAME_RESPONSE,
    FRAME_EVENT,
    FRAME_PAIRING_ACK,
)

#: Every legal ``type`` value, both directions.
ALL_FRAME_TYPES: tuple[str, ...] = DAEMON_TO_EXTENSION + EXTENSION_TO_DAEMON

#: The ONLY frame an unpaired (restricted) socket may send, per D06A. Anything
#: else closes it with 1008. Named here rather than spelled at the state machine
#: so the extension's socket code and the daemon's guard read the same constant.
RESTRICTED_INBOUND_FRAMES: tuple[str, ...] = (FRAME_PAIRING_ACK,)

# --------------------------------------------------------------------------- #
# Command methods — one per BrowserService method
# --------------------------------------------------------------------------- #

METHOD_STATUS = "status"
METHOD_LIST_TABS = "list_tabs"
METHOD_ACTIVE_TAB = "active_tab"
METHOD_READ_PAGE = "read_page"
METHOD_GET_ELEMENTS = "get_elements"
METHOD_SCREENSHOT = "screenshot"
METHOD_ACTIVATE_TAB = "activate_tab"
METHOD_SCROLL = "scroll"
METHOD_CREATE_TAB = "create_tab"
METHOD_CLOSE_TAB = "close_tab"
METHOD_CLICK = "click"
METHOD_TYPE_TEXT = "type_text"
METHOD_PRESS_KEY = "press_key"
METHOD_NAVIGATE = "navigate"

#: Methods that only observe. The wire mirror of ``browser_access="read_only"``:
#: a command outside this set on a read-only install is refused daemon-side
#: before it is sent, so a compromised extension cannot act by asking nicely.
READ_METHODS: tuple[str, ...] = (
    METHOD_STATUS,
    METHOD_LIST_TABS,
    METHOD_ACTIVE_TAB,
    METHOD_READ_PAGE,
    METHOD_GET_ELEMENTS,
    METHOD_SCREENSHOT,
)

#: Methods that move the user's own view without changing page state.
LOCAL_UI_METHODS: tuple[str, ...] = (
    METHOD_ACTIVATE_TAB,
    METHOD_SCROLL,
    METHOD_CREATE_TAB,
    METHOD_CLOSE_TAB,
)

#: Methods that change a page's state.
PAGE_ACTION_METHODS: tuple[str, ...] = (
    METHOD_CLICK,
    METHOD_TYPE_TEXT,
    METHOD_PRESS_KEY,
    METHOD_NAVIGATE,
)

ALL_METHODS: tuple[str, ...] = READ_METHODS + LOCAL_UI_METHODS + PAGE_ACTION_METHODS

# --------------------------------------------------------------------------- #
# Directive actions and event names
# --------------------------------------------------------------------------- #

#: ``browser.directive`` actions. ``request_host_permissions`` is the documented
#: deviation of plan section 6: ``chrome.permissions.request()`` needs a user
#: gesture and cannot run in a service worker, so the worker opens ``setup.html``
#: and one button there makes the call.
DIRECTIVE_REQUEST_HOST_PERMISSIONS = "request_host_permissions"
DIRECTIVE_DISCONNECT = "disconnect"

ALL_DIRECTIVES: tuple[str, ...] = (
    DIRECTIVE_REQUEST_HOST_PERMISSIONS,
    DIRECTIVE_DISCONNECT,
)

#: ``browser.event`` names. Each maps to one ``EventType`` constant published on
#: the existing bus; no second bus (D22).
EVENT_TAB_ACTIVATED = "tab_activated"
EVENT_NAVIGATION_COMPLETED = "navigation_completed"
EVENT_DOWNLOAD_COMPLETED = "download_completed"

ALL_EVENTS: tuple[str, ...] = (
    EVENT_TAB_ACTIVATED,
    EVENT_NAVIGATION_COMPLETED,
    EVENT_DOWNLOAD_COMPLETED,
)

# --------------------------------------------------------------------------- #
# Id prefixes and limits
# --------------------------------------------------------------------------- #

#: ``req_<n>`` is minted by the DAEMON and is the pending-future map's key.
REQUEST_ID_PREFIX = "req_"
#: ``evt_<n>`` is minted by the EXTENSION. Distinct prefixes so a stray response
#: carrying an event id can never resolve a command future.
EVENT_ID_PREFIX = "evt_"
#: ``pair_<rand>`` identifies one pending pairing request.
PAIRING_ID_PREFIX = "pair_"

#: Hard cap on one frame, in bytes, enforced BEFORE ``json.loads``.
#:
#: The frame crosses a WebSocket and is decoded on the daemon's single event
#: loop, so an oversized payload is not a big object — it is a stalled app, and
#: the v1.153.1 outage is what that looks like to the user ("Daemon offline",
#: every request timing out). A frame above the cap is refused with
#: ``EXTENSION_ERROR`` rather than parsed. Kept here, on the wire contract,
#: because the check has to happen before any snapshot code is reached.
MAX_FRAME_BYTES = 512 * 1024

#: Per-command timeouts, in seconds. These bound a wait; they are NOT performance
#: assertions, and no test asserts an elapsed duration against them.
DEFAULT_COMMAND_TIMEOUT_S = 15.0
COMMAND_TIMEOUTS_S: dict[str, float] = {
    METHOD_NAVIGATE: 30.0,
    METHOD_READ_PAGE: 20.0,
}

#: Seconds an unpaired socket may sit in the restricted state before 1008.
PAIRING_DEADLINE_S = 300.0

# --------------------------------------------------------------------------- #
# Sensitive-field vocabularies, re-exported so they cannot drift
# --------------------------------------------------------------------------- #

#: HTML ``autocomplete`` tokens marking a control as a credential field.
#: Re-exported from :mod:`iron_jarvis.computeruse.policy`, sorted so the
#: generated TypeScript is byte-stable across Python's set ordering — an
#: unsorted set would make the drift test fail at random between runs, which
#: trains a reader to re-run the gate instead of reading it.
PASSWORD_AUTOCOMPLETE: tuple[str, ...] = tuple(sorted(_PASSWORD_AUTOCOMPLETE))

#: The same, for payment fields.
PAYMENT_AUTOCOMPLETE: tuple[str, ...] = tuple(sorted(_PAYMENT_AUTOCOMPLETE))

#: The union the content script scrubs on (D13B). A field matching any token
#: yields ``sensitive: true`` and a ``null`` value, at capture time, so the
#: plaintext never crosses the socket at all.
SENSITIVE_AUTOCOMPLETE: tuple[str, ...] = tuple(
    sorted(set(PASSWORD_AUTOCOMPLETE) | set(PAYMENT_AUTOCOMPLETE))
)

#: URL schemes Chrome closes to add-ons (plan section 9.7). Checked daemon-side,
#: before the command is sent, so the refusal cannot fail silently in the page.
UNSUPPORTED_SCHEMES: tuple[str, ...] = (
    "chrome:",
    "edge:",
    "about:",
    "devtools:",
    "view-source:",
    "chrome-extension:",
)

#: Hosts that are closed to add-ons even over ``https:``.
UNSUPPORTED_HOSTS: tuple[str, ...] = (
    "chromewebstore.google.com",
    "chrome.google.com",
)

# --------------------------------------------------------------------------- #
# Frame shapes — plan section 9.6, verbatim
# --------------------------------------------------------------------------- #


class ErrorEnvelope(TypedDict):
    """The two-key failure envelope, built only by browser_error()."""

    code: str
    message: str


class CommandFrame(TypedDict):
    """Daemon -> extension: run one method. ``id`` keys the pending future."""

    id: str
    type: str
    method: str
    params: dict[str, Any]


class ResponseFrame(TypedDict):
    """Extension -> daemon: the answer to one command.

    ``success`` is the discriminator and both payload keys are optional, because
    a frame carrying ``result`` on a failure (or an empty ``error`` on a success)
    is how a caller ends up reading a stale value as the answer.
    """

    id: str
    type: str
    success: bool
    result: NotRequired[dict[str, Any]]
    error: NotRequired[ErrorEnvelope]


class DirectiveFrame(TypedDict):
    """Daemon -> extension: do something to the browser session itself."""

    id: str
    type: str
    action: str
    params: NotRequired[dict[str, Any]]


class PairingRequiredFrame(TypedDict):
    """Daemon -> extension: this socket is unpaired; here is its request id."""

    type: str
    request_id: str


class PairingAckFrame(TypedDict):
    """Extension -> daemon: the one frame a restricted socket may send."""

    type: str
    request_id: str


class PairedFrame(TypedDict):
    """Daemon -> extension: the pairing token, delivered exactly once, ever.

    The only place the plaintext token exists outside the extension's own
    ``chrome.storage.local``. It is never in a response body, a log line or an
    event payload; only its SHA-256 is persisted.
    """

    type: str
    token: str


class ReadyFrame(TypedDict):
    """Daemon -> extension: you are the authoritative connection.

    ``access`` is the live Browser access level (the same string
    ``GET /browser/status`` reports) so the add-on's own panel can name the mode
    instead of printing a placeholder. It is OPTIONAL on purpose: an older daemon
    paired with a newer add-on sends a ready frame without it, and the add-on then
    says the mode is unknown rather than guessing at one.
    """

    type: str
    active: bool
    access: NotRequired[str]


class ConnectionReplacedFrame(TypedDict):
    """Daemon -> outgoing extension: a newer socket took over (D08).

    Sent before the 1000 close, and every in-flight command on this connection is
    failed with ``CONNECTION_REPLACED`` rather than left to hang — which is the
    step plan section 7 names as the one most likely to be missed.
    """

    type: str
    error: ErrorEnvelope


class HelloFrame(TypedDict):
    """Extension -> daemon: who I am and whether I hold the host grant."""

    type: str
    extension_id: str
    extension_version: str
    host_permission: bool


class EventFrame(TypedDict):
    """Extension -> daemon: something happened in the browser, unprompted."""

    id: str
    type: str
    event: str
    payload: dict[str, Any]


#: Every frame ``TypedDict``, in generation order. ``ErrorEnvelope`` first because
#: two frames reference it and the generated TypeScript is read top to bottom.
FRAME_TYPEDDICTS: tuple[type, ...] = (
    ErrorEnvelope,
    CommandFrame,
    ResponseFrame,
    DirectiveFrame,
    PairingRequiredFrame,
    PairingAckFrame,
    PairedFrame,
    ReadyFrame,
    ConnectionReplacedFrame,
    HelloFrame,
    EventFrame,
)

#: ``type`` string -> the ``TypedDict`` describing that frame. The round-trip test
#: walks this map, so a frame type added without a shape (or a shape added without
#: a type) fails the gate instead of reaching the extension undocumented.
FRAME_SHAPES: dict[str, type] = {
    FRAME_COMMAND: CommandFrame,
    FRAME_DIRECTIVE: DirectiveFrame,
    FRAME_PAIRED: PairedFrame,
    FRAME_PAIRING_REQUIRED: PairingRequiredFrame,
    FRAME_READY: ReadyFrame,
    FRAME_CONNECTION_REPLACED: ConnectionReplacedFrame,
    FRAME_HELLO: HelloFrame,
    FRAME_RESPONSE: ResponseFrame,
    FRAME_EVENT: EventFrame,
    FRAME_PAIRING_ACK: PairingAckFrame,
}

# --------------------------------------------------------------------------- #
# Frame builders
# --------------------------------------------------------------------------- #


def command_frame(request_id: str, method: str, params: dict[str, Any] | None = None) -> CommandFrame:
    """Build a ``browser.command`` frame. ``params`` is always a dict on the wire.

    An absent ``params`` is normalised to ``{}`` rather than omitted, so the
    extension's dispatcher never has to guard a missing key — a ``params``
    sometimes-absent is how an optional-argument reader ends up throwing inside a
    service worker, where the exception is invisible to the daemon.
    """
    return {"id": request_id, "type": FRAME_COMMAND, "method": method, "params": dict(params or {})}


def response_frame(request_id: str, result: dict[str, Any] | None = None) -> ResponseFrame:
    """Build a successful ``browser.response`` frame."""
    return {"id": request_id, "type": FRAME_RESPONSE, "success": True, "result": dict(result or {})}


def error_response_frame(
    request_id: str,
    code: BrowserErrorCode | str,
    **fmt: Any,
) -> ResponseFrame:
    """Build a failed ``browser.response`` frame carrying a remedy the model can act on."""
    return {
        "id": request_id,
        "type": FRAME_RESPONSE,
        "success": False,
        "error": browser_error(code, **fmt),  # type: ignore[typeddict-item]
    }


def directive_frame(
    request_id: str,
    action: str,
    params: dict[str, Any] | None = None,
) -> DirectiveFrame:
    """Build a ``browser.directive`` frame."""
    frame: DirectiveFrame = {"id": request_id, "type": FRAME_DIRECTIVE, "action": action}
    if params:
        frame["params"] = dict(params)
    return frame


def pairing_required_frame(request_id: str) -> PairingRequiredFrame:
    """Build the ``browser.pairing_required`` frame an unpaired socket receives."""
    return {"type": FRAME_PAIRING_REQUIRED, "request_id": request_id}


def pairing_ack_frame(request_id: str) -> PairingAckFrame:
    """Build the ``browser.pairing_ack`` frame — the only frame a restricted socket may send."""
    return {"type": FRAME_PAIRING_ACK, "request_id": request_id}


def paired_frame(token: str) -> PairedFrame:
    """Build the one frame that ever carries the plaintext pairing token."""
    return {"type": FRAME_PAIRED, "token": token}


def ready_frame(active: bool = True, access: str | None = None) -> ReadyFrame:
    """Build the ``browser.ready`` frame the authoritative socket receives.

    ``access`` is OMITTED rather than sent empty when the caller has none to give.
    The add-on reads the key's presence: an empty string would be indistinguishable
    from a mode it does not recognise, and the panel would print a blank where a
    word belongs. A caller that knows the level passes it and the panel names it.
    """
    frame: ReadyFrame = {"type": FRAME_READY, "active": active}
    if access:
        frame["access"] = str(access)
    return frame


def connection_replaced_frame() -> ConnectionReplacedFrame:
    """Build the ``browser.connection_replaced`` frame sent before the 1000 close."""
    return {
        "type": FRAME_CONNECTION_REPLACED,
        "error": browser_error(BrowserErrorCode.CONNECTION_REPLACED),  # type: ignore[typeddict-item]
    }


def hello_frame(
    extension_id: str,
    extension_version: str,
    host_permission: bool,
) -> HelloFrame:
    """Build the ``browser.hello`` frame an authenticated socket opens with."""
    return {
        "type": FRAME_HELLO,
        "extension_id": extension_id,
        "extension_version": extension_version,
        "host_permission": bool(host_permission),
    }


def event_frame(event_id: str, event: str, payload: dict[str, Any] | None = None) -> EventFrame:
    """Build a ``browser.event`` frame."""
    return {"id": event_id, "type": FRAME_EVENT, "event": event, "payload": dict(payload or {})}


def command_timeout_s(method: str) -> float:
    """The wait bound for ``method``. One lookup, so no call site invents its own."""
    return COMMAND_TIMEOUTS_S.get(method, DEFAULT_COMMAND_TIMEOUT_S)


def unsupported_page_scheme(url: str) -> str:
    """The scheme to name in an ``UNSUPPORTED_PAGE`` refusal, or ``""`` if the page is fine.

    Returns the offending scheme (``"chrome:"``) rather than a bool so the remedy
    can say which one, per section 9.7's message. Host-closed pages report
    ``https:`` because that is what the user sees in the address bar; naming a
    scheme the URL does not have would read as a bug in the answer.
    """
    lowered = (url or "").strip().lower()
    for scheme in UNSUPPORTED_SCHEMES:
        if lowered.startswith(scheme):
            return scheme
    for host in UNSUPPORTED_HOSTS:
        if lowered.startswith(f"https://{host}/") or lowered == f"https://{host}":
            return "https:"
    return ""


__all__ = [
    "ALL_DIRECTIVES",
    "ALL_EVENTS",
    "ALL_FRAME_TYPES",
    "ALL_METHODS",
    "COMMAND_TIMEOUTS_S",
    "DAEMON_TO_EXTENSION",
    "DEFAULT_COMMAND_TIMEOUT_S",
    "DIRECTIVE_DISCONNECT",
    "DIRECTIVE_REQUEST_HOST_PERMISSIONS",
    "EVENT_DOWNLOAD_COMPLETED",
    "EVENT_ID_PREFIX",
    "EVENT_NAVIGATION_COMPLETED",
    "EVENT_TAB_ACTIVATED",
    "EXTENSION_TO_DAEMON",
    "FRAME_COMMAND",
    "FRAME_CONNECTION_REPLACED",
    "FRAME_DIRECTIVE",
    "FRAME_EVENT",
    "FRAME_HELLO",
    "FRAME_PAIRED",
    "FRAME_PAIRING_ACK",
    "FRAME_PAIRING_REQUIRED",
    "FRAME_READY",
    "FRAME_RESPONSE",
    "FRAME_SHAPES",
    "FRAME_TYPEDDICTS",
    "LOCAL_UI_METHODS",
    "MAX_FRAME_BYTES",
    "METHOD_ACTIVATE_TAB",
    "METHOD_ACTIVE_TAB",
    "METHOD_CLICK",
    "METHOD_CLOSE_TAB",
    "METHOD_CREATE_TAB",
    "METHOD_GET_ELEMENTS",
    "METHOD_LIST_TABS",
    "METHOD_NAVIGATE",
    "METHOD_PRESS_KEY",
    "METHOD_READ_PAGE",
    "METHOD_SCREENSHOT",
    "METHOD_SCROLL",
    "METHOD_STATUS",
    "METHOD_TYPE_TEXT",
    "PAGE_ACTION_METHODS",
    "PAIRING_DEADLINE_S",
    "PAIRING_ID_PREFIX",
    "PASSWORD_AUTOCOMPLETE",
    "PAYMENT_AUTOCOMPLETE",
    "PROTOCOL_VERSION",
    "READ_METHODS",
    "REMEDIES",
    "REQUEST_ID_PREFIX",
    "RESTRICTED_INBOUND_FRAMES",
    "SENSITIVE_AUTOCOMPLETE",
    "UNSUPPORTED_HOSTS",
    "UNSUPPORTED_SCHEMES",
    # frame shapes
    "CommandFrame",
    "ConnectionReplacedFrame",
    "DirectiveFrame",
    "ErrorEnvelope",
    "EventFrame",
    "HelloFrame",
    "PairedFrame",
    "PairingAckFrame",
    "PairingRequiredFrame",
    "ReadyFrame",
    "ResponseFrame",
    # errors, re-exported so one import serves a protocol call site
    "BrowserError",
    "BrowserErrorCode",
    "browser_error",
    # builders
    "command_frame",
    "command_timeout_s",
    "connection_replaced_frame",
    "directive_frame",
    "error_response_frame",
    "event_frame",
    "hello_frame",
    "paired_frame",
    "pairing_ack_frame",
    "pairing_required_frame",
    "ready_frame",
    "response_frame",
    "unsupported_page_scheme",
]
