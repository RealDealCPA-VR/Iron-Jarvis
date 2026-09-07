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
# Snapshot modes and limits (D13, D13A) — plan sections 9.1 and 9.2
# --------------------------------------------------------------------------- #

#: The three values of a ``read_page`` ``mode`` param, in increasing cost.
MODE_SUMMARY = "summary"
MODE_INTERACTIVE = "interactive"
MODE_FULL = "full"

#: Every legal mode. ``interactive`` is the default (D13) — it is the only mode
#: that yields element ids, and an action needs one, so defaulting to anything
#: else would make "read then click" two reads.
SNAPSHOT_MODES: tuple[str, ...] = (MODE_SUMMARY, MODE_INTERACTIVE, MODE_FULL)
DEFAULT_SNAPSHOT_MODE = MODE_INTERACTIVE

#: ``snap_<8 hex>``. Minted by the CONTENT SCRIPT, because the element registry
#: it keys is held in the page; the daemon only remembers which id was newest.
SNAPSHOT_ID_PREFIX = "snap_"

#: The D13A limits. They live on the WIRE contract, beside ``MAX_FRAME_BYTES``,
#: for the same reason that one does: both sides need them, and the side that
#: must not exceed them is the one whose numbers are generated rather than typed.
#: The content script trims to these while walking the page — that is the only
#: place a limit can be enforced *before* the bytes exist — and the daemon trims
#: again on arrival and reports what it dropped, so an add-on that ignores a cap
#: cannot hand a model an unbounded page. Plan section 9.2 names ``snapshot.py``
#: as their home; :mod:`iron_jarvis.browser.snapshot` re-exports every one of
#: them under exactly these names, so there is ONE definition and both spellings
#: read the same number. A second literal in TypeScript is the drift this whole
#: module exists to prevent.
#:
#: Visible text characters, ``interactive`` mode: the D13A headline figure.
MAX_TEXT_CHARS = 20_000
#: ``summary`` mode's text budget — metadata and headings plus a first taste.
SUMMARY_TEXT_CHARS = 2_000
#: ``full`` mode "raises the text cap" (plan 9.1). Three times the default, and
#: still an order of magnitude inside ``MAX_FRAME_BYTES`` once elements, links
#: and JSON escaping are counted — a cap that can produce a frame the daemon
#: then refuses would turn "read more of this page" into a failure.
FULL_TEXT_CHARS = 60_000
#: Interactive elements in one snapshot.
MAX_ELEMENTS = 250
#: Accessibility walk depth.
MAX_AX_DEPTH = 24
#: Nodes visited while walking, however shallow the tree.
MAX_AX_NODES = 5_000
#: Headings.
MAX_HEADINGS = 100
#: Links.
MAX_LINKS = 200
#: Characters of one element's accessible name (or a heading's / link's text).
MAX_NAME_CHARS = 200

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
# Read-method params and result shapes (Ship 2, plan sections 8.6 and 9.1)
# --------------------------------------------------------------------------- #
#
# These are the payloads INSIDE a ``browser.command`` / ``browser.response``
# frame, not frames themselves, so they are declared in their own tuple
# (:data:`RESULT_TYPEDDICTS`) rather than in :data:`FRAME_TYPEDDICTS`:
# ``FRAME_SHAPES`` maps a ``type`` string to a shape and every frame shape must
# have a type. A params object has no ``type`` of its own — it rides inside one —
# and putting it in the frame tuple would break that invariant while looking
# harmless. The generator emits both tuples.


class ReadPageParams(TypedDict):
    """``read_page`` params. Every limit is sent, none is assumed.

    ``tab_id`` is optional and absent means the active tab, resolved in the
    browser and echoed back in the result, so a model never needs two calls to
    read what the user is looking at.

    The limit keys are present because the content script must not carry its own
    copy of the numbers: a page trimmed to a cap the daemon does not know is a
    page whose truncation nobody reports.
    """

    mode: str
    tab_id: NotRequired[int]
    max_chars: NotRequired[int]
    max_elements: NotRequired[int]
    max_headings: NotRequired[int]
    max_links: NotRequired[int]
    max_name_chars: NotRequired[int]
    max_ax_depth: NotRequired[int]
    max_ax_nodes: NotRequired[int]


class GetElementsParams(TypedDict):
    """``get_elements`` params — a filtered view of one fresh snapshot."""

    tab_id: NotRequired[int]
    query: NotRequired[str]
    role: NotRequired[str]
    limit: NotRequired[int]


class ScreenshotParams(TypedDict):
    """``screenshot`` params. ``full_page`` is always sent, never inferred."""

    full_page: bool
    tab_id: NotRequired[int]


class ElementRow(TypedDict):
    """One entry in the interactive registry (D13), verbatim.

    ``value`` is typed ``None`` and nothing else, and it appears at all only on a
    control the scrubber marked sensitive — which is how the type system itself
    refuses the D13B leak. Section 9.4 collects no field value for ANY input, so
    there is no shape in this protocol that can carry one.
    """

    id: str
    role: str
    name: str
    text: str
    visible: bool
    enabled: bool
    type: NotRequired[str]
    autocomplete: NotRequired[str]
    sensitive: NotRequired[bool]
    value: NotRequired[None]


class HeadingRow(TypedDict):
    """One heading, as ``{level, text}``."""

    level: int
    text: str


class LinkRow(TypedDict):
    """One link. ``element_id`` ties it back to the registry."""

    element_id: str
    text: str
    href: str


class FormRow(TypedDict):
    """One form. ``fields`` holds element ids, never values."""

    name: str
    action: str
    fields: list[str]


class SecurityNote(TypedDict):
    """The Q03 injection warning that rides on a snapshot.

    ``warning`` is always ``true`` when the key is present: the absence of the
    note is how "nothing was detected" is said. A ``{"warning": false}`` object
    would be a second way to say the same thing, and a reader that checked only
    for the key's presence would then warn on a clean page.
    """

    warning: bool
    category: str
    reason: str


class TruncationRow(TypedDict):
    """One limit that bit, named, with how much was dropped (D13A).

    ``total`` is what the page actually had, when the page could say; ``0`` means
    it could not. Reported per limit rather than as one boolean, because "this
    page was cut short" and "the element registry stopped at 250" send a model to
    different next calls.
    """

    limit: str
    kept: int
    total: int


class SnapshotResult(TypedDict):
    """The ``read_page`` result — the snapshot of plan section 9.1.

    ``timestamp``, ``truncation``, ``counts`` and ``omitted`` are optional on the
    WIRE and always present on the daemon's own
    :class:`~iron_jarvis.browser.snapshot.PageSnapshot`: the add-on may leave them
    out, and the daemon fills them in from what arrived, so an older add-on
    degrades to a snapshot with a daemon-stamped time rather than to a failure.
    """

    snapshot_id: str
    page_version: int
    tab_id: int
    title: str
    url: str
    mode: str
    truncated: bool
    text: str
    headings: list[HeadingRow]
    elements: list[ElementRow]
    forms: list[FormRow]
    links: list[LinkRow]
    security: NotRequired[SecurityNote | None]
    timestamp: NotRequired[str]
    truncation: NotRequired[list[TruncationRow]]
    counts: NotRequired[dict[str, int]]
    omitted: NotRequired[list[str]]


class ElementsResult(TypedDict):
    """The ``get_elements`` result — a filtered slice of a fresh snapshot.

    ``truncation`` is the same channel :class:`SnapshotResult` carries, and it is
    here for the same reason: a filtered list is drawn from ONE walk of the page,
    and that walk has caps. When it stopped at ``max_ax_depth`` or ran out of
    ``max_ax_nodes``, the elements below were never visited, so they are in
    neither ``elements`` nor any count of what matched — the answer looks
    complete. A registry that looks complete is then cached as the tab's complete
    registry, and the next lookup answers ``ELEMENT_NOT_FOUND`` for a button that
    is genuinely on the page: a confident lie, and the one the daemon's caching
    branch exists to avoid. Naming the limit is what turns "there is no Export
    button" back into "I did not look at all of it".

    Optional on the wire, like :class:`SnapshotResult`'s, so an older add-on that
    does not send it degrades to today's behaviour rather than to a failure.
    """

    snapshot_id: str
    page_version: int
    elements: list[ElementRow]
    count: int
    truncated: bool
    truncation: NotRequired[list[TruncationRow]]


class ScreenshotResult(TypedDict):
    """The ``screenshot`` result. Base64 PNG on the response frame (D14).

    The bytes travel as base64 inside the ordinary response frame — there is no
    browser-specific image transport (D14) — which is also why
    :data:`MAX_FRAME_BYTES` matters here more than anywhere else: a full-page
    capture of a long page is the one payload that can reach the cap honestly.
    """

    tab_id: int
    media_type: str
    data_b64: str


#: The payload ``TypedDict``s, in generation order: referenced shapes first, so
#: the generated TypeScript reads top to bottom like the Python does.
RESULT_TYPEDDICTS: tuple[type, ...] = (
    ReadPageParams,
    GetElementsParams,
    ScreenshotParams,
    ElementRow,
    HeadingRow,
    LinkRow,
    FormRow,
    SecurityNote,
    TruncationRow,
    SnapshotResult,
    ElementsResult,
    ScreenshotResult,
)

#: ``method`` -> the ``TypedDict`` describing that method's RESULT. Only the read
#: methods are listed; the acting methods land in Ship 3. A method with no entry
#: is not undocumented by accident — it is not implemented yet, and
#: :class:`~iron_jarvis.browser.service.BrowserRuntime` raises for it by name.
RESULT_SHAPES: dict[str, type] = {
    METHOD_READ_PAGE: SnapshotResult,
    METHOD_GET_ELEMENTS: ElementsResult,
    METHOD_SCREENSHOT: ScreenshotResult,
}

#: ``method`` -> the ``TypedDict`` describing that method's PARAMS.
PARAM_SHAPES: dict[str, type] = {
    METHOD_READ_PAGE: ReadPageParams,
    METHOD_GET_ELEMENTS: GetElementsParams,
    METHOD_SCREENSHOT: ScreenshotParams,
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



def _int_param(value: Any) -> int | None:
    """``value`` as a positive int, or ``None`` for anything else.

    Silently dropping a malformed limit is right *here* and wrong at the tool
    layer: this function builds a wire frame, and a ``max_chars`` of ``"lots"``
    put on the wire would be a limit the content script cannot compare against,
    so it would walk the page unbounded. The tool layer is where a bad argument
    earns words; the frame builder's job is to never emit one.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


#: The limit keys ``read_page_params`` will put on the wire. A whitelist, so a
#: caller handing over a dict with an extra key cannot invent a param the add-on
#: has never heard of and will silently ignore.
_READ_PAGE_LIMIT_KEYS: frozenset[str] = frozenset(
    {
        "max_chars",
        "max_elements",
        "max_headings",
        "max_links",
        "max_name_chars",
        "max_ax_depth",
        "max_ax_nodes",
    }
)


def read_page_params(
    tab_id: int | None = None,
    mode: str = DEFAULT_SNAPSHOT_MODE,
    limits: dict[str, int] | None = None,
) -> ReadPageParams:
    """Build ``read_page`` params.

    ``limits`` is :meth:`~iron_jarvis.browser.snapshot.SnapshotLimits.to_params`
    output. It is passed in rather than computed here because the effective limits
    depend on the mode AND on what the caller asked for, and that resolution is
    the snapshot model's job — this module holds the numbers, not the policy that
    narrows them. Omitting it sends the mode alone, which is what the plan's own
    frame example shows.

    ``tab_id`` is OMITTED when ``None`` rather than sent as null: absent means
    "the active tab", and a null would have to be given that meaning a second
    time, in the add-on, in TypeScript, where ``0`` is also falsy.
    """
    params: ReadPageParams = {"mode": str(mode or DEFAULT_SNAPSHOT_MODE)}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    for key, value in (limits or {}).items():
        clean = _int_param(value)
        if clean is not None and key in _READ_PAGE_LIMIT_KEYS:
            params[key] = clean  # type: ignore[literal-required]
    return params


def get_elements_params(
    tab_id: int | None = None,
    *,
    query: str | None = None,
    role: str | None = None,
    limit: int | None = None,
) -> GetElementsParams:
    """Build ``get_elements`` params. Empty filters are omitted, not sent blank.

    An empty ``query`` sent as ``""`` is indistinguishable in the add-on from "no
    filter" only if the add-on remembers to check for it. Omitting the key makes
    that impossible to get wrong.
    """
    params: GetElementsParams = {}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    text = str(query or "").strip()
    if text:
        params["query"] = text
    role_text = str(role or "").strip()
    if role_text:
        params["role"] = role_text
    resolved_limit = _int_param(limit)
    if resolved_limit is not None:
        params["limit"] = min(resolved_limit, MAX_ELEMENTS)
    return params


def screenshot_params(tab_id: int | None = None, *, full_page: bool = False) -> ScreenshotParams:
    """Build ``screenshot`` params. ``full_page`` is always present."""
    params: ScreenshotParams = {"full_page": bool(full_page)}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    return params


def is_snapshot_mode(value: Any) -> bool:
    """Whether ``value`` is one of the three modes, exactly as spelled.

    No case folding and no aliases. ``"Interactive"`` is refused so the model is
    told the vocabulary once instead of learning a private dialect that the
    content script — which compares the raw string — will not honour.
    """
    return isinstance(value, str) and value in SNAPSHOT_MODES


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
    "DEFAULT_SNAPSHOT_MODE",
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
    "FULL_TEXT_CHARS",
    "LOCAL_UI_METHODS",
    "MAX_AX_DEPTH",
    "MAX_AX_NODES",
    "MAX_ELEMENTS",
    "MAX_FRAME_BYTES",
    "MAX_HEADINGS",
    "MAX_LINKS",
    "MAX_NAME_CHARS",
    "MAX_TEXT_CHARS",
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
    "MODE_FULL",
    "MODE_INTERACTIVE",
    "MODE_SUMMARY",
    "PAGE_ACTION_METHODS",
    "PAIRING_DEADLINE_S",
    "PAIRING_ID_PREFIX",
    "PARAM_SHAPES",
    "PASSWORD_AUTOCOMPLETE",
    "PAYMENT_AUTOCOMPLETE",
    "PROTOCOL_VERSION",
    "READ_METHODS",
    "REMEDIES",
    "REQUEST_ID_PREFIX",
    "RESTRICTED_INBOUND_FRAMES",
    "RESULT_SHAPES",
    "RESULT_TYPEDDICTS",
    "SENSITIVE_AUTOCOMPLETE",
    "SNAPSHOT_ID_PREFIX",
    "SNAPSHOT_MODES",
    "SUMMARY_TEXT_CHARS",
    "UNSUPPORTED_HOSTS",
    "UNSUPPORTED_SCHEMES",
    # frame and payload shapes
    "CommandFrame",
    "ConnectionReplacedFrame",
    "DirectiveFrame",
    "ElementRow",
    "ElementsResult",
    "ErrorEnvelope",
    "EventFrame",
    "FormRow",
    "GetElementsParams",
    "HeadingRow",
    "HelloFrame",
    "LinkRow",
    "PairedFrame",
    "PairingAckFrame",
    "PairingRequiredFrame",
    "ReadPageParams",
    "ReadyFrame",
    "ResponseFrame",
    "ScreenshotParams",
    "ScreenshotResult",
    "SecurityNote",
    "SnapshotResult",
    "TruncationRow",
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
    "get_elements_params",
    "hello_frame",
    "is_snapshot_mode",
    "paired_frame",
    "pairing_ack_frame",
    "pairing_required_frame",
    "read_page_params",
    "ready_frame",
    "response_frame",
    "screenshot_params",
    "unsupported_page_scheme",
]
