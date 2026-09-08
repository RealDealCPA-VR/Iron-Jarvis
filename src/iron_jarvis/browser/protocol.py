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

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from ..computeruse.policy import _PASSWORD_AUTOCOMPLETE, _PAYMENT_AUTOCOMPLETE
from ..core.jsonish import loads_object
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
#: One turn's worth of news for the side panel. Unsolicited, like a directive, and
#: for the same reason: the panel is a VIEW of a conversation the daemon is running,
#: so the daemon narrates and the panel paints. See :data:`ALL_PANEL_EVENTS`.
FRAME_PANEL_EVENT = "browser.panel_event"

#: Extension -> daemon.
FRAME_HELLO = "browser.hello"
FRAME_RESPONSE = "browser.response"
FRAME_EVENT = "browser.event"
FRAME_PAIRING_ACK = "browser.pairing_ack"
#: The side panel asking for something: open, send, stop, steer, approve, deny,
#: close. Fire-and-forget UPWARD, exactly like ``browser.event``, and deliberately
#: NOT a request/response pair -- see the note on :data:`ALL_PANEL_ACTIONS`.
FRAME_PANEL = "browser.panel"

DAEMON_TO_EXTENSION: tuple[str, ...] = (
    FRAME_COMMAND,
    FRAME_DIRECTIVE,
    FRAME_PAIRED,
    FRAME_PAIRING_REQUIRED,
    FRAME_READY,
    FRAME_CONNECTION_REPLACED,
    FRAME_PANEL_EVENT,
)

EXTENSION_TO_DAEMON: tuple[str, ...] = (
    FRAME_HELLO,
    FRAME_RESPONSE,
    FRAME_EVENT,
    FRAME_PAIRING_ACK,
    FRAME_PANEL,
)

#: Every legal ``type`` value, both directions.
ALL_FRAME_TYPES: tuple[str, ...] = DAEMON_TO_EXTENSION + EXTENSION_TO_DAEMON

#: The ONLY frame an unpaired (restricted) socket may send, per D06A. Anything
#: else closes it with 1008. Named here rather than spelled at the state machine
#: so the extension's socket code and the daemon's guard read the same constant.
#:
#: ``browser.panel`` is POINTEDLY absent. A side panel belongs to a browser that has
#: already been paired by a human press, so an unpaired socket asking the daemon to
#: run a chat turn is not an early panel -- it is a local process that never paired,
#: reaching the model through a window meant for the user's browser.
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
# The side panel (BROWSER-SIDEBAR-PLAN section 3, D33)
# --------------------------------------------------------------------------- #

#: ``browser.panel`` actions -- the whole vocabulary the side panel may speak.
#:
#: The panel is an EVENT-DRIVEN VIEW, not an RPC client. No action here is answered
#: with a correlated reply, and none carries an id the daemon has to remember: the
#: answer to ``send`` is the stream of ``browser.panel_event`` frames that follows,
#: the same way ``browser.directive`` already flows the other way unasked. That is
#: not a shortcut. The comment at the top of the id-prefix section explains why the
#: daemon owns the ONLY pending-future map on this socket; a second correlation map
#: minted at the extension's end would hand an add-on the ability to resolve a
#: future the daemon is waiting on, which is precisely what ``evt_`` exists to
#: prevent.
PANEL_ACTION_OPEN = "open"
PANEL_ACTION_SEND = "send"
PANEL_ACTION_STOP = "stop"
PANEL_ACTION_STEER = "steer"
PANEL_ACTION_APPROVE = "approve"
PANEL_ACTION_DENY = "deny"
PANEL_ACTION_CLOSE = "close"

ALL_PANEL_ACTIONS: tuple[str, ...] = (
    PANEL_ACTION_OPEN,
    PANEL_ACTION_SEND,
    PANEL_ACTION_STOP,
    PANEL_ACTION_STEER,
    PANEL_ACTION_APPROVE,
    PANEL_ACTION_DENY,
    PANEL_ACTION_CLOSE,
)

#: ``browser.panel_event`` names -- everything the daemon narrates to the panel.
#:
#: ``steered`` is its own event rather than a flag on ``delta`` because of what the
#: panel must NOT do: a steer note is shown as PENDING until the turn's next tool
#: round actually consumes it (plan section 5), and only the daemon knows when that
#: happened. Without a frame that says so, the panel would have to guess -- and the
#: guess it would make is the one that tells the user their correction landed before
#: it did.
PANEL_EVENT_STATE = "state"
PANEL_EVENT_DELTA = "delta"
PANEL_EVENT_TOOL = "tool"
PANEL_EVENT_APPROVAL = "approval"
PANEL_EVENT_STEERED = "steered"
PANEL_EVENT_DONE = "done"
PANEL_EVENT_ERROR = "error"

ALL_PANEL_EVENTS: tuple[str, ...] = (
    PANEL_EVENT_STATE,
    PANEL_EVENT_DELTA,
    PANEL_EVENT_TOOL,
    PANEL_EVENT_APPROVAL,
    PANEL_EVENT_STEERED,
    PANEL_EVENT_DONE,
    PANEL_EVENT_ERROR,
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
# Acting vocabularies (Ship 3, plan sections 8.5 and 8.6)
# --------------------------------------------------------------------------- #

#: The four ``scroll`` directions, exactly as spelled. Checked daemon-side by
#: :func:`is_scroll_direction` before a frame is built, for the same reason
#: :data:`SNAPSHOT_MODES` is: the content script compares the raw string, so a
#: ``"Down"`` that reached the page would scroll nowhere and answer success.
SCROLL_UP = "up"
SCROLL_DOWN = "down"
SCROLL_TOP = "top"
SCROLL_BOTTOM = "bottom"
SCROLL_DIRECTIONS: tuple[str, ...] = (SCROLL_UP, SCROLL_DOWN, SCROLL_TOP, SCROLL_BOTTOM)

#: The three ways a target may be addressed, in the order section 8.6 prefers
#: them. ``element_id`` first is not a style preference: it is the only form that
#: carries the element's ACCESSIBLE NAME back to the risk classifier
#: (:func:`iron_jarvis.computeruse.policy.escalate_browser`), and a CSS selector
#: gives that classifier nothing to read. A "Delete account" button addressed as
#: ``{"css": "#btn-7"}`` is a button whose escalation vocabulary is empty.
TARGET_ELEMENT_ID = "element_id"
TARGET_ROLE = "role"
TARGET_CSS = "css"
TARGET_FORMS: tuple[str, ...] = (TARGET_ELEMENT_ID, TARGET_ROLE, TARGET_CSS)

#: Every key a target object may carry. ``name`` rides with ``role`` and is not a
#: form of its own — a bare name with no role is ambiguous on a page with a link
#: and a button that read the same, and picking one silently is how the wrong
#: control gets clicked.
TARGET_KEYS: tuple[str, ...] = ("element_id", "role", "name", "css")

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


class PanelFrame(TypedDict):
    """Extension -> daemon: the side panel asks for something.

    Three keys and no id, because there is nothing to correlate: the panel does not
    wait for an answer to this frame, it watches ``browser.panel_event``. ``params``
    is not optional -- an action with nothing to say carries ``{}``, so no call site
    has to decide between an absent key and an empty one.
    """

    type: str
    action: str
    params: dict[str, Any]


class PanelEventFrame(TypedDict):
    """Daemon -> extension: one thing that happened in the panel's conversation.

    The mirror of :class:`PanelFrame`, and the only way a turn reaches the panel.
    ``event`` is one of :data:`ALL_PANEL_EVENTS` and ``payload`` is that event's own
    shape.
    """

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
    PanelFrame,
    PanelEventFrame,
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
    FRAME_PANEL: PanelFrame,
    FRAME_PANEL_EVENT: PanelEventFrame,
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


class Target(TypedDict):
    """How ONE element is addressed by an acting call (section 8.6).

    Exactly one form per call: ``element_id``, or ``role`` + ``name``, or ``css``.
    Every key is optional in the TYPE because a TypedDict cannot express "exactly
    one of three"; :func:`normalise_target` enforces it and refuses two forms with
    a message naming the conflict. A permissive reader that took the first key it
    recognised would act on the target the model did NOT mean — two forms
    disagree precisely when the model is unsure, which is the moment a wrong click
    costs the most.

    Coordinates are deliberately absent (section 30 of the decision record).
    """

    element_id: NotRequired[str]
    role: NotRequired[str]
    name: NotRequired[str]
    css: NotRequired[str]


class TargetRef(TypedDict):
    """What was ACTUALLY acted on, echoed back by the page.

    Not the same object as :class:`Target`: that is what the model asked for, this
    is what the content script resolved. They differ whenever a role+name or CSS
    target matched something other than what the model pictured, and that
    difference is the only evidence a later reader of the ledger has. Section 10.4
    requires this in every acting result for exactly that reason.
    """

    element_id: str
    role: str
    name: str


class ActivateTabParams(TypedDict):
    """``activate_tab`` params. ``tab_id`` is REQUIRED here, uniquely.

    Every other acting method treats an absent ``tab_id`` as "the active tab".
    Activating the tab that is already active is a call with no meaning, so this
    one method has nothing to default to.
    """

    tab_id: int


class ScrollParams(TypedDict):
    """``scroll`` params. ``direction`` is one of :data:`SCROLL_DIRECTIONS`."""

    direction: str
    tab_id: NotRequired[int]
    amount: NotRequired[int]


class CreateTabParams(TypedDict):
    """``create_tab`` params. ``active`` is always sent, never inferred."""

    active: bool
    url: NotRequired[str]


class CloseTabParams(TypedDict):
    """``close_tab`` params. ``tab_id`` is required.

    Closing "whatever is active" is how the user loses the tab they were reading,
    and a closed tab is the one page state in this protocol that no retry
    restores — which is why the tool declares IRREVERSIBLE.
    """

    tab_id: int


class ClickParams(TypedDict):
    """``click`` params.

    ``snapshot_id`` is optional and ENFORCED when present. Absent, the daemon uses
    the tab's newest snapshot and says so in the result; with no snapshot at all
    the call is refused ``STALE_SNAPSHOT`` with the remedy "call browser_read_page
    first". Acting on a page nobody has read is acting blind.
    """

    target: Target
    tab_id: NotRequired[int]
    snapshot_id: NotRequired[str]


class TypeTextParams(TypedDict):
    """``type_text`` params.

    ``text`` crosses the socket because it has to be typed, and it is the one
    field in this protocol that is redacted before it is ever WRITTEN down:
    ``browser_type.redact_args`` replaces it unconditionally, so ``args_json`` —
    stored at rest, returned by session export, included in backups — never holds
    it. Unconditional, not "when the field looks sensitive": a conditional
    redactor would have to resolve the element before the ledger write, and any
    resolution failure would then log the plaintext.
    """

    target: Target
    text: str
    clear: bool
    press_enter: bool
    tab_id: NotRequired[int]
    snapshot_id: NotRequired[str]


class PressKeyParams(TypedDict):
    """``press_key`` params. ``target`` is optional — a key can go to the page."""

    key: str
    tab_id: NotRequired[int]
    target: NotRequired[Target]
    snapshot_id: NotRequired[str]


class NavigateParams(TypedDict):
    """``navigate`` params.

    The URL has already passed :func:`unsupported_page_scheme` and the live
    ``ComputerUsePolicy.domain_allowed`` on the daemon side before this object is
    built. Both checks are pre-send on purpose: a refusal decided in the page is a
    refusal the daemon cannot explain, and the domain allowlist the user
    configured for computer use is the same allowlist here — one configuration,
    not two.
    """

    url: str
    tab_id: NotRequired[int]


class DownloadPayload(TypedDict):
    """The ``download_completed`` event payload, as the ADD-ON sends it (10.3).

    ``filename`` is Chromium's ``DownloadItem.filename``, documented as an
    absolute local path and readable with the ``downloads`` permission alone — no
    native messaging host, and no filesystem access invented inside the add-on.

    It is still only a CLAIM until the daemon checks it. The bus payload of 10.2
    carries ``local_path``, and that key is added daemon-side after verifying the
    string is absolute; it is deliberately NOT a field here, so nothing can put a
    relative path (or a path a compromised add-on invented) into a ``local_path``
    the file tools would then trust. The wire says what the browser reported; the
    daemon says what it verified.

    ``final_url`` is the post-redirect URL and differs from ``source_url``
    whenever a download link bounces through a CDN — which is most of them, and is
    why both are reported rather than one.
    """

    download_id: int
    filename: str
    source_url: str
    final_url: NotRequired[str]
    tab_id: NotRequired[int]
    bytes: NotRequired[int]
    mime: NotRequired[str]
    timestamp: NotRequired[str]


class ActivateTabResult(TypedDict):
    """The ``activate_tab`` result."""

    tab_id: int
    title: str
    url: str
    activated: bool


class ScrollResult(TypedDict):
    """The ``scroll`` result. ``scrolled_to`` names where the page ended up.

    ``page_version`` rides on a LOCAL_UI result because scrolling can load more of
    an infinite list, which adds interactive nodes and bumps the version. A model
    that scrolled and then clicked an element id from before the scroll would
    otherwise be refused ``STALE_ELEMENT`` with no idea which of its own calls
    caused it.
    """

    tab_id: int
    scrolled_to: str
    page_version: int
    url: NotRequired[str]
    title: NotRequired[str]


class CreateTabResult(TypedDict):
    """The ``create_tab`` result."""

    tab_id: int
    url: str
    title: str


class CloseTabResult(TypedDict):
    """The ``close_tab`` result. ``closed`` is always ``true`` on success."""

    tab_id: int
    closed: bool


class ClickResult(TypedDict):
    """The ``click`` result.

    ``navigated`` is the page telling the daemon that the click left the page. It
    is what invalidates the tab's cached snapshot, so a model that clicks a link
    and then reuses an element id is answered ``STALE_SNAPSHOT`` with a remedy
    instead of clicking whatever now occupies that slot.

    ``download`` carries the completed download the click started (section 10.3).
    It is on the RESULT and not only on the event because a model that asked for a
    file needs the path in the answer to the call it made — an event it never sees
    is a path it cannot hand to ``read_document``.
    """

    tab_id: int
    clicked: TargetRef
    url: str
    page_version: int
    navigated: bool
    title: NotRequired[str]
    download: NotRequired[DownloadPayload]


class TypeTextResult(TypedDict):
    """The ``type_text`` result. It NEVER echoes ``text`` — there is no key for it.

    Absent by construction rather than by a caller remembering to strip it: a
    result shape with a ``text`` field is a result shape that will eventually
    carry a password into the ledger's ``output`` column, which is capped and
    stored but not redacted.
    """

    tab_id: int
    typed_into: TargetRef
    cleared: bool
    submitted: bool
    page_version: int
    url: NotRequired[str]
    title: NotRequired[str]
    navigated: NotRequired[bool]


class PressKeyResult(TypedDict):
    """The ``press_key`` result."""

    tab_id: int
    key: str
    page_version: int
    navigated: bool
    url: NotRequired[str]
    title: NotRequired[str]


class NavigateResult(TypedDict):
    """The ``navigate`` result. ``status`` is the tab's load state."""

    tab_id: int
    url: str
    title: str
    page_version: int
    status: str


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
    Target,
    TargetRef,
    ActivateTabParams,
    ScrollParams,
    CreateTabParams,
    CloseTabParams,
    ClickParams,
    TypeTextParams,
    PressKeyParams,
    NavigateParams,
    DownloadPayload,
    ActivateTabResult,
    ScrollResult,
    CreateTabResult,
    CloseTabResult,
    ClickResult,
    TypeTextResult,
    PressKeyResult,
    NavigateResult,
)

#: ``method`` -> the ``TypedDict`` describing that method's RESULT. Only the read
#: methods are listed; the acting methods land in Ship 3. A method with no entry
#: is not undocumented by accident — it is not implemented yet, and
#: :class:`~iron_jarvis.browser.service.BrowserRuntime` raises for it by name.
RESULT_SHAPES: dict[str, type] = {
    METHOD_READ_PAGE: SnapshotResult,
    METHOD_GET_ELEMENTS: ElementsResult,
    METHOD_SCREENSHOT: ScreenshotResult,
    METHOD_ACTIVATE_TAB: ActivateTabResult,
    METHOD_SCROLL: ScrollResult,
    METHOD_CREATE_TAB: CreateTabResult,
    METHOD_CLOSE_TAB: CloseTabResult,
    METHOD_CLICK: ClickResult,
    METHOD_TYPE_TEXT: TypeTextResult,
    METHOD_PRESS_KEY: PressKeyResult,
    METHOD_NAVIGATE: NavigateResult,
}

#: ``method`` -> the ``TypedDict`` describing that method's PARAMS.
PARAM_SHAPES: dict[str, type] = {
    METHOD_READ_PAGE: ReadPageParams,
    METHOD_GET_ELEMENTS: GetElementsParams,
    METHOD_SCREENSHOT: ScreenshotParams,
    METHOD_ACTIVATE_TAB: ActivateTabParams,
    METHOD_SCROLL: ScrollParams,
    METHOD_CREATE_TAB: CreateTabParams,
    METHOD_CLOSE_TAB: CloseTabParams,
    METHOD_CLICK: ClickParams,
    METHOD_TYPE_TEXT: TypeTextParams,
    METHOD_PRESS_KEY: PressKeyParams,
    METHOD_NAVIGATE: NavigateParams,
}

#: ``event`` name -> the ``TypedDict`` describing that event's PAYLOAD. Only the
#: download event has a shape worth pinning: the other two carry tab identity the
#: add-on already sends on every response. Listed so the generator emits it and
#: the add-on's downloads module compiles against the same object the daemon
#: parses (section 10.3).
EVENT_PAYLOAD_SHAPES: dict[str, type] = {
    EVENT_DOWNLOAD_COMPLETED: DownloadPayload,
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


def panel_frame(action: str, params: dict[str, Any] | None = None) -> PanelFrame:
    """Build a ``browser.panel`` frame (extension -> daemon).

    ``params`` is copied rather than referenced: the panel builds one of these per
    press, and a frame holding the caller's own dict is a frame whose contents can
    change after it was queued for the socket.
    """
    return {"type": FRAME_PANEL, "action": action, "params": dict(params or {})}


def panel_event_frame(event: str, payload: dict[str, Any] | None = None) -> PanelEventFrame:
    """Build a ``browser.panel_event`` frame (daemon -> extension)."""
    return {"type": FRAME_PANEL_EVENT, "event": event, "payload": dict(payload or {})}



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


def activate_tab_params(tab_id: int) -> ActivateTabParams:
    """Build ``activate_tab`` params. ``tab_id`` is required and validated here.

    Raises:
        BrowserError: ``TAB_NOT_FOUND`` when ``tab_id`` is not a positive integer.
        A bool is refused too, because ``True`` is an ``int`` in Python and
        ``activate_tab(True)`` would put ``tab_id: 1`` on the wire and activate a
        tab the caller never named.
    """
    resolved = _int_param(tab_id)
    if resolved is None:
        raise BrowserError(BrowserErrorCode.TAB_NOT_FOUND, tab_id=tab_id)
    return {"tab_id": resolved}


def close_tab_params(tab_id: int) -> CloseTabParams:
    """Build ``close_tab`` params. Same required-and-validated ``tab_id``."""
    resolved = _int_param(tab_id)
    if resolved is None:
        raise BrowserError(BrowserErrorCode.TAB_NOT_FOUND, tab_id=tab_id)
    return {"tab_id": resolved}


def scroll_params(
    tab_id: int | None = None,
    *,
    direction: str,
    amount: int | None = None,
) -> ScrollParams:
    """Build ``scroll`` params.

    Raises:
        BrowserError: ``EXTENSION_ERROR`` naming the four legal directions when
        ``direction`` is outside :data:`SCROLL_DIRECTIONS`. Refused HERE rather
        than in the page: the content script compares the raw string, so an
        unknown direction would scroll nowhere and answer success — a silent
        no-op that a model reads as "done".
    """
    if not is_scroll_direction(direction):
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            detail=(
                f"scroll direction {direction!r} is not one of "
                f"{', '.join(SCROLL_DIRECTIONS)}"
            ),
        )
    params: ScrollParams = {"direction": str(direction)}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    resolved_amount = _int_param(amount)
    if resolved_amount is not None:
        params["amount"] = resolved_amount
    return params


def create_tab_params(url: str | None = None, *, active: bool = True) -> CreateTabParams:
    """Build ``create_tab`` params. An empty ``url`` is omitted, not sent blank.

    ``about:blank`` is not substituted for a missing URL: ``about:`` is on
    :data:`UNSUPPORTED_SCHEMES`, so a tab opened that way would be one the add-on
    can never read, and the model would be handed an id it cannot act on.
    """
    params: CreateTabParams = {"active": bool(active)}
    text = str(url or "").strip()
    if text:
        params["url"] = text
    return params


def click_params(
    target: Mapping[str, Any],
    tab_id: int | None = None,
    *,
    snapshot_id: str | None = None,
) -> ClickParams:
    """Build ``click`` params, with the target normalised to exactly one form."""
    params: ClickParams = {"target": normalise_target(target)}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    snap = str(snapshot_id or "").strip()
    if snap:
        params["snapshot_id"] = snap
    return params


def type_text_params(
    target: Mapping[str, Any],
    text: str,
    tab_id: int | None = None,
    *,
    clear: bool = False,
    press_enter: bool = False,
    snapshot_id: str | None = None,
) -> TypeTextParams:
    """Build ``type_text`` params. ``clear`` and ``press_enter`` are always sent.

    Both booleans are present on every frame rather than omitted when false. The
    add-on would have to default a missing key, and the two possible defaults are
    "leave the field's existing content" and "wipe it" — a divergence between the
    daemon's assumption and the add-on's would silently destroy whatever the user
    had already typed into that field.

    ``text`` is passed through verbatim, including empty: typing an empty string
    with ``clear=True`` is how a field is emptied, and dropping the key would turn
    that into a validation failure for a legitimate call.
    """
    params: TypeTextParams = {
        "target": normalise_target(target),
        "text": str(text if text is not None else ""),
        "clear": bool(clear),
        "press_enter": bool(press_enter),
    }
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    snap = str(snapshot_id or "").strip()
    if snap:
        params["snapshot_id"] = snap
    return params


def press_key_params(
    key: str,
    tab_id: int | None = None,
    *,
    target: Mapping[str, Any] | None = None,
    snapshot_id: str | None = None,
) -> PressKeyParams:
    """Build ``press_key`` params. ``target`` is optional; ``key`` is not.

    Raises:
        BrowserError: ``EXTENSION_ERROR`` on an empty ``key``. A frame with an
        empty key would reach the page, dispatch nothing, and answer success.
    """
    text = str(key or "").strip()
    if not text:
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            detail="press_key needs a key name, for example 'Enter' or 'Escape'",
        )
    params: PressKeyParams = {"key": text}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    if target:
        params["target"] = normalise_target(target)
    snap = str(snapshot_id or "").strip()
    if snap:
        params["snapshot_id"] = snap
    return params


def navigate_params(url: str, tab_id: int | None = None) -> NavigateParams:
    """Build ``navigate`` params.

    Raises:
        BrowserError: ``UNSUPPORTED_PAGE`` for a scheme Chrome closes to add-ons,
        and ``NAVIGATION_FAILED`` for an empty URL. The scheme check runs here as
        well as at the tool because this is the last point before the frame
        exists: a ``chrome://`` command that reached the add-on would be dropped
        by Chrome with no response frame at all, and the daemon would report an
        ``ACTION_TIMEOUT`` for a call that was refused instantly.
    """
    text = str(url or "").strip()
    if not text:
        raise BrowserError(BrowserErrorCode.NAVIGATION_FAILED, url=url)
    scheme = unsupported_page_scheme(text)
    if scheme:
        raise BrowserError(BrowserErrorCode.UNSUPPORTED_PAGE, scheme=scheme)
    params: NavigateParams = {"url": text}
    resolved_tab = _int_param(tab_id) if tab_id is not None else None
    if resolved_tab is not None:
        params["tab_id"] = resolved_tab
    return params


def normalise_target(target: Mapping[str, Any] | None) -> Target:
    """The one target validator: exactly one form, or a refusal that names why.

    Section 8.6's rule, enforced in one place so no acting tool can spell it
    differently: ``{"element_id": "e17"}`` preferred, ``{"role", "name"}`` next,
    ``{"css"}`` last, and exactly ONE of the three per call.

    Two forms is refused rather than resolved by preference order. That looks
    stricter than it needs to be until you picture the call it refuses: a model
    that sends both ``element_id`` and ``css`` is a model that is unsure which is
    right, and silently honouring the first would act on the target it did not
    mean — on the user's real, logged-in page — with a result echoing the id that
    "won" and nothing recording the disagreement.

    Unknown keys are dropped rather than refused: an add-on or a caller a version
    ahead may send a key this daemon has never heard of, and failing the whole
    call over an extra field would break a flow that is otherwise well formed.

    A target that is not an OBJECT at all is refused here with the same code and
    the same three-form message, and that is not a formality. ``registry.invoke``'s
    shape gate deliberately accepts a STRING wherever a type is declared
    (``core.jsonish.json_type_ok``), so ``{"target": "e7"}`` — the single most
    likely wrong shape, because models stringify — reaches this function, and
    ``dict("e7")`` raises ``ValueError``, which is not a :class:`BrowserError` and
    therefore reached the model as a raw Python traceback: *dictionary update
    sequence element #0 has length 1; 2 is required*. Plan section 8.6 forbids
    exactly that — a model given a traceback has nothing to correct and retries
    the same shape. A string is first tried as JSON (a model that encoded the
    object is honoured), and anything else is a refusal that names the forms.

    Raises:
        BrowserError: ``ELEMENT_NOT_FOUND`` with an explicit message when the
        target is not an object, is empty, carries two forms, or names a role with
        no name.
    """
    if target is not None and not isinstance(target, Mapping):
        recovered = loads_object(target) if isinstance(target, str) else None
        if recovered is None:
            raise BrowserError(
                BrowserErrorCode.ELEMENT_NOT_FOUND,
                message=(
                    "The target must be an object, not "
                    f"{type(target).__name__}. Send exactly one of: "
                    '{"element_id": "e17"} from browser_read_page (preferred), '
                    '{"role": "button", "name": "Sign in"}, or {"css": "#submit"}.'
                ),
            )
        target = recovered
    row = {str(k): v for k, v in dict(target or {}).items() if k in TARGET_KEYS}
    element_id = str(row.get("element_id") or "").strip()
    role = str(row.get("role") or "").strip()
    name = str(row.get("name") or "").strip()
    css = str(row.get("css") or "").strip()

    forms = [
        form
        for form, present in (
            (TARGET_ELEMENT_ID, bool(element_id)),
            (TARGET_ROLE, bool(role or name)),
            (TARGET_CSS, bool(css)),
        )
        if present
    ]
    if not forms:
        raise BrowserError(
            BrowserErrorCode.ELEMENT_NOT_FOUND,
            message=(
                "No target was given. Name exactly one of: "
                '{"element_id": "e17"} from browser_read_page (preferred), '
                '{"role": "button", "name": "Sign in"}, or {"css": "#submit"}.'
            ),
        )
    if len(forms) > 1:
        raise BrowserError(
            BrowserErrorCode.ELEMENT_NOT_FOUND,
            message=(
                f"The target names {len(forms)} forms at once ({', '.join(forms)}); "
                "exactly one is allowed. Send the element_id from "
                "browser_read_page on its own."
            ),
        )
    if TARGET_ROLE in forms and not (role and name):
        raise BrowserError(
            BrowserErrorCode.ELEMENT_NOT_FOUND,
            message=(
                "A role target needs both role and name, for example "
                '{"role": "button", "name": "Sign in"}. Call browser_read_page '
                "to see what is on the page now."
            ),
        )

    clean: Target = {}
    if element_id:
        clean["element_id"] = element_id
    elif css:
        clean["css"] = css
    else:
        clean["role"] = role
        clean["name"] = name
    return clean


def target_label(target: Mapping[str, Any] | None) -> str:
    """The words a risk classifier can read off a target, or ``""``.

    Only a role+name target carries any: an ``element_id`` names nothing until the
    page resolves it, and a CSS selector names nothing ever. That asymmetry is the
    whole reason section 8.6 prefers ``element_id`` *and* the reason the acting
    tools must pass the RESOLVED accessible name to
    :func:`~iron_jarvis.computeruse.policy.escalate_browser` rather than relying on
    this. This function is the pre-resolution fallback, not the label.
    """
    row = dict(target or {})
    return str(row.get("name") or "").strip()


def is_scroll_direction(value: Any) -> bool:
    """Whether ``value`` is one of the four directions, exactly as spelled."""
    return isinstance(value, str) and value in SCROLL_DIRECTIONS


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
    "ALL_PANEL_ACTIONS",
    "ALL_PANEL_EVENTS",
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
    "FRAME_PANEL",
    "FRAME_PANEL_EVENT",
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
    "PANEL_ACTION_APPROVE",
    "PANEL_ACTION_CLOSE",
    "PANEL_ACTION_DENY",
    "PANEL_ACTION_OPEN",
    "PANEL_ACTION_SEND",
    "PANEL_ACTION_STEER",
    "PANEL_ACTION_STOP",
    "PANEL_EVENT_APPROVAL",
    "PANEL_EVENT_DELTA",
    "PANEL_EVENT_DONE",
    "PANEL_EVENT_ERROR",
    "PANEL_EVENT_STATE",
    "PANEL_EVENT_STEERED",
    "PANEL_EVENT_TOOL",
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
    "EVENT_PAYLOAD_SHAPES",
    "SCROLL_BOTTOM",
    "SCROLL_DIRECTIONS",
    "SCROLL_DOWN",
    "SCROLL_TOP",
    "SCROLL_UP",
    "TARGET_CSS",
    "TARGET_ELEMENT_ID",
    "TARGET_FORMS",
    "TARGET_KEYS",
    "TARGET_ROLE",
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
    "PanelEventFrame",
    "PanelFrame",
    "ReadPageParams",
    "ReadyFrame",
    "ResponseFrame",
    "ScreenshotParams",
    "ScreenshotResult",
    "SecurityNote",
    "SnapshotResult",
    "TruncationRow",
    "ActivateTabParams",
    "ActivateTabResult",
    "ClickParams",
    "ClickResult",
    "CloseTabParams",
    "CloseTabResult",
    "CreateTabParams",
    "CreateTabResult",
    "DownloadPayload",
    "NavigateParams",
    "NavigateResult",
    "PressKeyParams",
    "PressKeyResult",
    "ScrollParams",
    "ScrollResult",
    "Target",
    "TargetRef",
    "TypeTextParams",
    "TypeTextResult",
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
    "panel_event_frame",
    "panel_frame",
    "read_page_params",
    "ready_frame",
    "response_frame",
    "screenshot_params",
    "unsupported_page_scheme",
    "activate_tab_params",
    "click_params",
    "close_tab_params",
    "create_tab_params",
    "is_scroll_direction",
    "navigate_params",
    "normalise_target",
    "press_key_params",
    "scroll_params",
    "target_label",
    "type_text_params",
]
