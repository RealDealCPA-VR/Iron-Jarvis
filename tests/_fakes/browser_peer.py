"""The deterministic fake Chromium peer for the Browser Bridge (D30).

This is **not** a mock of ``BrowserService``. It connects to the real
``/browser/ws`` route through ``TestClient.websocket_connect`` and answers real
``browser.command`` frames from a scripted page model, so every test that uses it
exercises the real route, the real origin guard, the real pairing state machine
and the real backend. A mock of the service would pass while all four of those
were broken — which is exactly what D30 refuses.

Why the page model is scripted rather than random: staleness has to be driven
*honestly*. A test that patches the daemon's snapshot cache to force
``STALE_ELEMENT`` proves the daemon can format that error, not that it detects the
condition. :meth:`ScriptedBrowser.advance_page_version` moves the page the way a
real navigation or mutation batch would, and the peer then reports staleness the
way the content script does — from the page side, where the live node is.

Four rules this file obeys, each drawn from a real incident in this repository:

* **Every receive sits inside a bounded loop that raises a named assertion on
  exhaustion.** A ``TestClient`` WebSocket receive has *no timeout*. An unbounded
  one converts a protocol bug into a hung release gate, which is far more
  expensive than a red test: nobody can tell a deadlock from a slow runner.
* **The daemon side is driven with ``client.portal.call(...)``** — the established
  idiom for reaching async daemon code from the sync test thread.
* **No assertion measures elapsed time.** The waits below are synchronisation, and
  each one is bounded by a frame or attempt budget, never asserted against.
* **Any spy takes ``*args, **kw`` and calls through.** This peer installs none, but
  a test that adds one around it must.

Threading, and why. Frames arrive unprompted (a ``browser.command`` is sent while
the test thread is blocked inside ``portal.call``), so a single-threaded peer would
deadlock: the daemon waits for an answer the test thread cannot send because it is
waiting for the daemon. So one daemon thread pumps the socket — answering commands
immediately and posting every other frame to a queue — and the test thread reads
that queue. A failure inside the pump is *captured*, not raised into a thread
nobody watches, and re-raised on the test thread by :meth:`raise_if_failed`, which
every public method calls. An assertion that fires in a background thread and is
never re-raised is a green test over a broken peer.

Typical use::

    with TestClient(app) as client:
        peer = BrowserPeer(client, headers=auth_headers)
        peer.connect()
        peer.pair()                       # real pairing handshake, real token
        tabs = peer.serve(runtime.list_tabs)   # runs on the daemon, peer answers
        peer.close()

Result payload shapes are the extension's half of the contract and are documented
per handler below; the daemon-side tool output shapes of plan section 8.6 are built
*from* these by the service and tool lanes.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserErrorCode, browser_error
from iron_jarvis.browser.identity import PINNED_EXTENSION_ID, extension_origin

#: Total frames one peer will handle before it declares the test runaway. Generous
#: enough for any realistic scenario and finite, so a protocol loop fails the test
#: instead of pinning a CI core until the job times out.
MAX_FRAMES = 500

#: Attempts, and the seconds each waits, when the test thread expects one frame.
#: A budget rather than a deadline: the assertion names what never arrived.
FRAME_WAIT_ATTEMPTS = 200
FRAME_WAIT_STEP_S = 0.05

DEFAULT_EXTENSION_VERSION = "1.235.0"


class PeerProtocolError(AssertionError):
    """A named assertion: the peer gave up waiting, or saw a frame it cannot honour.

    An ``AssertionError`` subclass so pytest reports it as a failed assertion
    rather than an error in the fixture, and named so the failure line says which
    side of the socket went wrong.
    """


# --------------------------------------------------------------------------- #
# The scripted page model
# --------------------------------------------------------------------------- #


@dataclass
class FakeElement:
    """One entry in a tab's interactive registry, in the D13 element shape.

    ``value`` is deliberately absent. Section 9.4 collects no field value for any
    input, ever, which is stricter than D13B requires and removes a whole class of
    leak — a fake that carried values would let a test pass while the real content
    script leaked one.
    """

    id: str
    role: str = "button"
    name: str = ""
    text: str = ""
    visible: bool = True
    enabled: bool = True
    field_type: str = ""
    autocomplete: str = ""

    @property
    def sensitive(self) -> bool:
        """Whether the scrubber would mark this control sensitive (D13B)."""
        return self.field_type == "password" or self.autocomplete in P.SENSITIVE_AUTOCOMPLETE

    def snapshot_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "text": self.text,
            "visible": self.visible,
            "enabled": self.enabled,
        }
        if self.field_type:
            row["type"] = self.field_type
        if self.autocomplete:
            row["autocomplete"] = self.autocomplete
        if self.sensitive:
            row["sensitive"] = True
            row["value"] = None
        return row


@dataclass
class FakeTab:
    """One tab in the scripted browser."""

    id: int
    title: str = ""
    url: str = "https://example.com/"
    active: bool = False
    window_id: int = 1
    status: str = "complete"
    page_version: int = 1
    text: str = ""
    headings: list[dict[str, Any]] = field(default_factory=list)
    elements: list[FakeElement] = field(default_factory=list)
    scrolled_to: str = "top"

    @property
    def supported(self) -> bool:
        """False for a page Chrome closes to add-ons (plan section 9.7)."""
        return not P.unsupported_page_scheme(self.url)

    def row(self, *, host_permission: bool) -> dict[str, Any]:
        """The tab row ``list_tabs`` and ``active_tab`` return.

        With no host grant Chrome hands the add-on a tab whose ``url`` and ``title``
        are empty strings. The peer reports them as ``None`` plus
        ``needs_host_permission: true`` rather than as ``""``, because an empty
        string reads to a model as "this tab has no title" — a lie — while a null
        with a flag beside it reads as "not readable yet, and here is why".
        """
        return {
            "id": self.id,
            "title": self.title if host_permission else None,
            "url": self.url if host_permission else None,
            "active": self.active,
            "window_id": self.window_id,
            "status": self.status,
            "supported": self.supported,
            "needs_host_permission": not host_permission,
        }


class ScriptedBrowser:
    """The peer's page model: tabs, element registries, and ``page_version``.

    Every mutation a test wants to drive is a method here, so a test never reaches
    into the daemon to fake a browser-side condition.
    """

    def __init__(
        self,
        tabs: list[FakeTab] | None = None,
        *,
        host_permission: bool = True,
        extension_id: str = PINNED_EXTENSION_ID,
        extension_version: str = DEFAULT_EXTENSION_VERSION,
    ) -> None:
        self.tabs: list[FakeTab] = list(tabs) if tabs is not None else [default_tab()]
        self.host_permission = host_permission
        self.extension_id = extension_id
        self.extension_version = extension_version
        self._next_tab_id = max((tab.id for tab in self.tabs), default=0) + 1
        self._next_snapshot = 0
        #: snapshot_id -> (tab_id, page_version at capture). The peer answers
        #: staleness from here, which is where the live node would be.
        self.snapshots: dict[str, tuple[int, int]] = {}
        if self.tabs and not any(tab.active for tab in self.tabs):
            self.tabs[0].active = True

    # --- lookup -----------------------------------------------------------

    def tab(self, tab_id: int | None) -> FakeTab | None:
        if tab_id is None:
            return self.active_tab()
        for tab in self.tabs:
            if tab.id == tab_id:
                return tab
        return None

    def active_tab(self) -> FakeTab | None:
        for tab in self.tabs:
            if tab.active:
                return tab
        return self.tabs[0] if self.tabs else None

    # --- mutations a test drives ------------------------------------------

    def advance_page_version(self, tab_id: int | None = None, *, by: int = 1) -> int:
        """Move the page on, the way a navigation or mutation batch would.

        This is how a test drives staleness honestly: every snapshot taken before
        the call now names an older ``page_version``, so the peer answers
        ``STALE_ELEMENT`` from the page side rather than the daemon being patched
        into reporting it.
        """
        tab = self.tab(tab_id)
        if tab is None:
            raise PeerProtocolError(f"advance_page_version: no tab {tab_id}")
        tab.page_version += by
        return tab.page_version

    def open_tab(self, url: str = "https://example.com/new", title: str = "New tab") -> FakeTab:
        tab = FakeTab(id=self._next_tab_id, url=url, title=title)
        self._next_tab_id += 1
        self.tabs.append(tab)
        return tab

    def activate(self, tab_id: int) -> FakeTab:
        target = self.tab(tab_id)
        if target is None:
            raise PeerProtocolError(f"activate: no tab {tab_id}")
        for tab in self.tabs:
            tab.active = tab is target
        return target

    def close(self, tab_id: int) -> None:
        self.tabs = [tab for tab in self.tabs if tab.id != tab_id]

    def grant_host_permission(self, granted: bool = True) -> None:
        self.host_permission = granted

    # --- snapshots --------------------------------------------------------

    def take_snapshot(self, tab: FakeTab, mode: str = "interactive") -> dict[str, Any]:
        """Build a snapshot and remember which ``page_version`` it was taken at."""
        self._next_snapshot += 1
        snapshot_id = f"snap_{self._next_snapshot:08x}"
        self.snapshots[snapshot_id] = (tab.id, tab.page_version)
        rows = [element.snapshot_row() for element in tab.elements]
        snapshot: dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "page_version": tab.page_version,
            "tab_id": tab.id,
            "title": tab.title,
            "url": tab.url,
            "mode": mode,
            "truncated": False,
            "text": tab.text,
            "headings": list(tab.headings),
            "elements": [] if mode == "summary" else rows,
            "forms": [],
            "links": [],
            "security": None,
        }
        return snapshot

    def resolve_target(
        self,
        tab: FakeTab,
        target: dict[str, Any] | None,
        snapshot_id: str | None,
    ) -> FakeElement:
        """Resolve a target to a live element, or raise the right staleness error.

        The ordering matters and mirrors section 9.3: an unknown snapshot is
        ``STALE_SNAPSHOT`` (call read_page), a known snapshot on a moved page is
        ``STALE_ELEMENT`` (call read_page and use the NEW id), and only then is a
        missing id ``ELEMENT_NOT_FOUND``. Collapsing the first two would tell a
        model to re-read when it should re-read *and* re-target, or the reverse.
        """
        if snapshot_id is not None:
            known = self.snapshots.get(snapshot_id)
            if known is None or known[0] != tab.id:
                raise _PeerError(BrowserErrorCode.STALE_SNAPSHOT)
            if known[1] != tab.page_version:
                raise _PeerError(BrowserErrorCode.STALE_ELEMENT)
        element_id = (target or {}).get("element_id")
        if element_id:
            for element in tab.elements:
                if element.id == element_id:
                    if not element.visible or not element.enabled:
                        raise _PeerError(
                            BrowserErrorCode.ELEMENT_NOT_FOUND,
                            element_id=element_id,
                            snapshot_id=snapshot_id or "",
                        )
                    return element
            raise _PeerError(
                BrowserErrorCode.ELEMENT_NOT_FOUND,
                element_id=element_id,
                snapshot_id=snapshot_id or "",
            )
        role = (target or {}).get("role")
        name = (target or {}).get("name")
        if role or name:
            for element in tab.elements:
                if (not role or element.role == role) and (not name or element.name == name):
                    return element
        raise _PeerError(
            BrowserErrorCode.ELEMENT_NOT_FOUND,
            element_id=str((target or {}).get("element_id") or target or "?"),
            snapshot_id=snapshot_id or "",
        )


class _PeerError(Exception):
    """Internal: a handler's way of saying "answer this command with that code"."""

    def __init__(self, code: BrowserErrorCode, **fmt: Any) -> None:
        self.code = code
        self.fmt = fmt
        super().__init__(code.value)


def default_tab() -> FakeTab:
    """One believable tab, so a test that only needs "a browser" writes one line."""
    return FakeTab(
        id=42,
        title="Example Domain",
        url="https://example.com/",
        active=True,
        text="Example Domain. This domain is for use in illustrative examples.",
        headings=[{"level": 1, "text": "Example Domain"}],
        elements=[
            FakeElement(id="e1", role="link", name="More information", text="More information"),
            FakeElement(id="e2", role="button", name="Sign in", text="Sign in"),
        ],
    )


# --------------------------------------------------------------------------- #
# The peer
# --------------------------------------------------------------------------- #


class BrowserPeer:
    """A fake Chromium add-on on the real ``/browser/ws`` socket.

    Args:
        client: the live ``TestClient``. Its ``portal`` is how :meth:`serve`
            reaches the daemon's async code, and its HTTP methods are how
            :meth:`pair` calls ``POST /browser/pair``.
        page: the scripted page model. Defaults to one believable tab.
        headers: install-bearer headers for the HTTP half (``POST /browser/pair``).
            The socket never sends them: the install bearer is refused at
            ``/browser/ws`` by design.
        token: a pairing token to connect with. Empty means connect unpaired,
            with ``?pairing=1``.
        origin: the ``Origin`` header. Defaults to the pinned extension origin,
            which is the only non-loopback origin the guard admits, and only on
            this path.
    """

    def __init__(
        self,
        client: Any,
        *,
        page: ScriptedBrowser | None = None,
        headers: dict[str, str] | None = None,
        token: str = "",
        origin: str | None = None,
        path: str = "/browser/ws",
    ) -> None:
        self.client = client
        self.page = page if page is not None else ScriptedBrowser()
        self.headers = dict(headers or {})
        self.token = token
        self.origin = extension_origin() if origin is None else origin
        self.path = path

        #: Every frame the peer received, in order — the record a test asserts on.
        self.frames: list[dict[str, Any]] = []
        #: ``(method, params)`` per command handled, in order.
        self.commands: list[tuple[str, dict[str, Any]]] = []
        #: The pairing request id from ``browser.pairing_required``, once seen.
        self.pairing_request_id: str = ""
        #: The plaintext token, if the daemon delivered one on this socket.
        self.paired_token: str = ""
        #: Directives received, as ``(action, params)``.
        self.directives: list[tuple[str, dict[str, Any]]] = []

        #: Methods the peer answers with a scripted failure: method -> code.
        self.fail_next: dict[str, BrowserErrorCode] = {}
        #: Methods the peer receives and deliberately never answers. This is how a
        #: test drives an in-flight command across a connection replacement or a
        #: command timeout — the only honest way to prove the daemon fails a
        #: pending future instead of leaving it to hang.
        self.never_answer: set[str] = set()

        self._ws: Any | None = None
        self._session_cm: Any | None = None
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pump: threading.Thread | None = None
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._handled = 0

    # --- lifecycle --------------------------------------------------------

    @property
    def url(self) -> str:
        """The socket URL, with the query the pairing state machine keys off."""
        if self.token:
            return f"{self.path}?token={self.token}"
        return f"{self.path}?pairing=1"

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._stop.is_set()

    def connect(self) -> BrowserPeer:
        """Open the socket, start the pump, and send ``browser.hello`` when paired.

        ``hello`` is sent only on a token-bearing connection: an unpaired socket is
        RESTRICTED and any frame other than ``browser.pairing_ack`` closes it with
        1008, so a peer that greeted first would be testing the close path by
        accident on every pairing test.
        """
        if self._ws is not None:
            raise PeerProtocolError("BrowserPeer.connect called twice")
        headers = {"Origin": self.origin}
        self._session_cm = self.client.websocket_connect(self.url, headers=headers)
        self._ws = self._session_cm.__enter__()
        self._stop.clear()
        self._pump = threading.Thread(target=self._run, name="browser-peer", daemon=True)
        self._pump.start()
        if self.token:
            self.send_hello()
        return self

    def send_hello(self) -> None:
        """Send ``browser.hello`` — who I am and whether I hold the host grant."""
        self.send(
            P.hello_frame(
                self.page.extension_id,
                self.page.extension_version,
                self.page.host_permission,
            )
        )

    def close(self, code: int = 1000) -> None:
        """Close the socket and join the pump, then re-raise any captured failure.

        The pump's blocked ``receive`` wakes because closing the session makes the
        app send a close frame, which the pump reads and exits on. Nothing here
        waits on a wall clock for that.
        """
        self._stop.set()
        try:
            if self._session_cm is not None:
                self._session_cm.__exit__(None, None, None)
        except Exception:  # pragma: no cover - the socket may already be gone
            pass
        finally:
            self._session_cm = None
            self._ws = None
        if self._pump is not None:
            self._pump.join(timeout=FRAME_WAIT_ATTEMPTS * FRAME_WAIT_STEP_S)
            self._pump = None
        self.raise_if_failed()

    def __enter__(self) -> BrowserPeer:
        return self.connect()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- failures ---------------------------------------------------------

    def raise_if_failed(self) -> None:
        """Re-raise, on the test thread, anything the pump thread hit.

        Called by every public method. An assertion that fires in a background
        thread and is never re-raised is a green test over a broken peer, and this
        is the whole guard against that.
        """
        failure, self._failure = self._failure, None
        if failure is not None:
            raise failure

    # --- the pump ---------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._handled >= MAX_FRAMES:
                self._failure = PeerProtocolError(
                    f"BrowserPeer handled {MAX_FRAMES} frames without the test finishing; "
                    "the protocol is looping"
                )
                return
            ws = self._ws
            if ws is None:
                return
            try:
                frame = ws.receive_json()
            except Exception:
                # A close, a disconnect, or a torn-down app. Either the test is
                # done or it is asserting the close, and both are read from
                # `self.frames` on the test thread.
                return
            self._handled += 1
            try:
                self._handle(frame)
            except BaseException as exc:  # noqa: BLE001 - captured, re-raised on the test thread
                self._failure = exc
                return

    def _handle(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)
        kind = frame.get("type", "")
        if kind == P.FRAME_COMMAND:
            self._answer_command(frame)
            return
        if kind == P.FRAME_DIRECTIVE:
            self._answer_directive(frame)
            return
        if kind == P.FRAME_PAIRING_REQUIRED:
            self.pairing_request_id = str(frame.get("request_id", ""))
            # The one frame a restricted socket may send. Sent immediately, because
            # the daemon's pairing deadline is running.
            self.send(P.pairing_ack_frame(self.pairing_request_id))
        elif kind == P.FRAME_PAIRED:
            self.paired_token = str(frame.get("token", ""))
        self._inbox.put(frame)

    def _answer_directive(self, frame: dict[str, Any]) -> None:
        action = str(frame.get("action", ""))
        self.directives.append((action, dict(frame.get("params") or {})))
        request_id = str(frame.get("id", ""))
        if action == P.DIRECTIVE_REQUEST_HOST_PERMISSIONS:
            # A real add-on opens setup.html and one button there calls
            # chrome.permissions.request; the grant is reported back here.
            self.page.grant_host_permission(True)
            self.send(P.response_frame(request_id, {"granted": True}))
            return
        if action == P.DIRECTIVE_DISCONNECT:
            self.send(P.response_frame(request_id, {"disconnecting": True}))
            return
        self.send(
            P.error_response_frame(
                request_id, BrowserErrorCode.EXTENSION_ERROR, detail=f"unknown directive {action}"
            )
        )

    def _answer_command(self, frame: dict[str, Any]) -> None:
        request_id = str(frame.get("id", ""))
        method = str(frame.get("method", ""))
        params = dict(frame.get("params") or {})
        self.commands.append((method, params))
        if method in self.never_answer:
            return
        scripted = self.fail_next.pop(method, None)
        if scripted is not None:
            self.send(P.error_response_frame(request_id, scripted))
            return
        handler = self._handlers().get(method)
        if handler is None:
            self.send(
                P.error_response_frame(
                    request_id,
                    BrowserErrorCode.EXTENSION_ERROR,
                    detail=f"unknown method {method}",
                )
            )
            return
        try:
            result = handler(params)
        except _PeerError as exc:
            self.send(P.error_response_frame(request_id, exc.code, **exc.fmt))
            return
        self.send(P.response_frame(request_id, result))

    # --- sending and expecting -------------------------------------------

    def send(self, frame: dict[str, Any]) -> None:
        """Send one frame. Used by the pump and by a test driving a raw frame."""
        ws = self._ws
        if ws is None:
            raise PeerProtocolError("BrowserPeer.send on a closed socket")
        ws.send_json(frame)

    def expect_frame(
        self,
        frame_type: str = "",
        *,
        what: str = "",
        strict: bool = False,
    ) -> dict[str, Any]:
        """Take the next non-command frame of ``frame_type``, or fail by name.

        Bounded by :data:`FRAME_WAIT_ATTEMPTS` attempts of
        :data:`FRAME_WAIT_STEP_S`. The bound exists because a ``TestClient``
        WebSocket receive has no timeout: without it a daemon that never sends the
        frame hangs the release gate instead of failing a test. Nothing asserts how
        long the wait actually took.

        By default earlier frames of other types are skipped, because the daemon is
        entitled to interleave (``browser.ready`` may land either side of
        ``browser.paired``) and a peer that failed on that would pin an ordering the
        protocol never promised. Pass ``strict=True`` where the order IS the claim —
        ``browser.connection_replaced`` must arrive *before* the close, and a test
        that skipped past a missing one would report a pass.
        """
        self.raise_if_failed()
        label = what or frame_type or "a frame"
        skipped: list[str] = []
        for _ in range(FRAME_WAIT_ATTEMPTS):
            self.raise_if_failed()
            try:
                frame = self._inbox.get(timeout=FRAME_WAIT_STEP_S)
            except queue.Empty:
                continue
            if not frame_type or frame.get("type") == frame_type:
                return frame
            if strict:
                raise PeerProtocolError(
                    f"expected {label} next from the daemon, got {frame.get('type')!r}: {frame!r}"
                )
            skipped.append(str(frame.get("type")))
        self.raise_if_failed()
        raise PeerProtocolError(
            f"the daemon never sent {label}; skipped {skipped}; frames seen: "
            f"{[f.get('type') for f in self.frames]}"
        )

    def wait_for_commands(self, count: int = 1, *, what: str = "") -> list[tuple[str, dict]]:
        """Block until the peer has handled ``count`` commands, bounded.

        Used by a test that drives the daemon from another thread and needs the
        command to have *arrived* before it does something else — replacing the
        connection, for instance.
        """
        label = what or f"{count} command(s)"
        for _ in range(FRAME_WAIT_ATTEMPTS):
            self.raise_if_failed()
            if len(self.commands) >= count:
                return list(self.commands)
            self._stop.wait(FRAME_WAIT_STEP_S)
        self.raise_if_failed()
        raise PeerProtocolError(
            f"the daemon never sent {label}; commands seen: {[m for m, _ in self.commands]}"
        )

    # --- pairing ----------------------------------------------------------

    def pair(self, *, expect_status: int = 200) -> str:
        """Run the real D06A pairing handshake and return the plaintext token.

        The sequence is the daemon's, not the peer's: ``browser.pairing_required``
        arrives, the peer acks it (the pump does that the moment it lands, because
        the daemon's pairing deadline is already running), the test posts
        ``POST /browser/pair`` with the request id, and the token comes back on
        this socket in the one frame that ever carries it. The response *body* is
        asserted not to carry it, because a token in a JSON body ends up in a
        browser devtools log and in every HTTP trace.
        """
        required = self.expect_frame(P.FRAME_PAIRING_REQUIRED, what="browser.pairing_required")
        request_id = str(required.get("request_id", ""))
        if not request_id:
            raise PeerProtocolError(f"pairing_required carried no request_id: {required!r}")
        response = self.client.post(
            "/browser/pair", json={"request_id": request_id}, headers=self.headers
        )
        if response.status_code != expect_status:
            raise PeerProtocolError(
                f"POST /browser/pair answered {response.status_code}: {response.text}"
            )
        body = response.json() if response.content else {}
        leaked = [key for key, value in body.items() if isinstance(value, str) and len(value) > 20]
        if leaked:
            raise PeerProtocolError(
                f"POST /browser/pair may have leaked the token in {leaked}: {body!r}"
            )
        paired = self.expect_frame(P.FRAME_PAIRED, what="browser.paired")
        self.token = str(paired.get("token", ""))
        if not self.token:
            raise PeerProtocolError(f"browser.paired carried no token: {paired!r}")
        return self.token

    def expect_ready(self) -> dict[str, Any]:
        """Assert this socket was told it is the authoritative connection (D08)."""
        return self.expect_frame(P.FRAME_READY, what="browser.ready")

    def expect_connection_replaced(self) -> dict[str, Any]:
        """Assert this socket was told a newer one took over, before the close."""
        return self.expect_frame(
            P.FRAME_CONNECTION_REPLACED, what="browser.connection_replaced"
        )

    # --- driving the daemon ----------------------------------------------

    def serve(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an async daemon callable to completion while the peer answers it.

        ``client.portal.call`` is the established idiom for reaching async daemon
        code from the sync test thread; the pump thread answers the commands that
        call emits. A callable that emits *no* command works too, which is why the
        pump runs continuously rather than being started per call — a per-call pump
        would block forever on the first refusal that never reaches the browser.
        """
        self.raise_if_failed()
        try:
            return self.client.portal.call(lambda: call(*args, **kwargs))
        finally:
            self.raise_if_failed()

    # --- command handlers -------------------------------------------------

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {
            P.METHOD_STATUS: self._h_status,
            P.METHOD_LIST_TABS: self._h_list_tabs,
            P.METHOD_ACTIVE_TAB: self._h_active_tab,
            P.METHOD_READ_PAGE: self._h_read_page,
            P.METHOD_GET_ELEMENTS: self._h_get_elements,
            P.METHOD_SCREENSHOT: self._h_screenshot,
            P.METHOD_ACTIVATE_TAB: self._h_activate_tab,
            P.METHOD_SCROLL: self._h_scroll,
            P.METHOD_CREATE_TAB: self._h_create_tab,
            P.METHOD_CLOSE_TAB: self._h_close_tab,
            P.METHOD_CLICK: self._h_click,
            P.METHOD_TYPE_TEXT: self._h_type_text,
            P.METHOD_PRESS_KEY: self._h_press_key,
            P.METHOD_NAVIGATE: self._h_navigate,
        }

    def _tab_for(self, params: dict[str, Any], *, need_page: bool) -> FakeTab:
        """Resolve ``tab_id`` (absent means the active tab) and gate on the grant.

        ``need_page`` is the host-permission line drawn by plan section 6: tab
        *metadata* survives a missing grant, and everything that touches the page
        answers ``PERMISSION_DENIED`` with the remedy that names the button to
        press. Getting that boundary wrong in the fake would hide the real
        failure mode entirely, because the tests would never see it.
        """
        if need_page and not self.page.host_permission:
            raise _PeerError(BrowserErrorCode.PERMISSION_DENIED)
        raw = params.get("tab_id")
        tab = self.page.tab(int(raw) if raw is not None else None)
        if tab is None:
            raise _PeerError(BrowserErrorCode.TAB_NOT_FOUND, tab_id=raw)
        if need_page and not tab.supported:
            raise _PeerError(
                BrowserErrorCode.UNSUPPORTED_PAGE,
                scheme=P.unsupported_page_scheme(tab.url),
            )
        if need_page and tab.status != "complete":
            raise _PeerError(BrowserErrorCode.PAGE_NOT_READY, tab_id=tab.id)
        return tab

    def _h_status(self, params: dict[str, Any]) -> dict[str, Any]:
        active = self.page.active_tab()
        return {
            "connected": True,
            "host_permission": self.page.host_permission,
            "extension_id": self.page.extension_id,
            "extension_version": self.page.extension_version,
            "tab_count": len(self.page.tabs),
            "active_tab": active.row(host_permission=self.page.host_permission) if active else None,
        }

    def _h_list_tabs(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = [tab.row(host_permission=self.page.host_permission) for tab in self.page.tabs]
        return {"tabs": rows, "count": len(rows)}

    def _h_active_tab(self, params: dict[str, Any]) -> dict[str, Any]:
        active = self.page.active_tab()
        if active is None:
            raise _PeerError(BrowserErrorCode.TAB_NOT_FOUND, tab_id="active")
        return active.row(host_permission=self.page.host_permission)

    def _h_read_page(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        return self.page.take_snapshot(tab, str(params.get("mode") or "interactive"))

    def _h_get_elements(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        snapshot = self.page.take_snapshot(tab)
        rows = snapshot["elements"]
        role = params.get("role")
        query = (params.get("query") or "").lower()
        if role:
            rows = [row for row in rows if row.get("role") == role]
        if query:
            rows = [row for row in rows if query in str(row.get("name", "")).lower()]
        limit = params.get("limit")
        truncated = False
        if isinstance(limit, int) and limit >= 0 and len(rows) > limit:
            rows, truncated = rows[:limit], True
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "page_version": tab.page_version,
            "elements": rows,
            "count": len(rows),
            "truncated": truncated,
        }

    def _h_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        # A 1x1 PNG: enough for the artifact path to be real without a fixture file.
        return {
            "tab_id": tab.id,
            "media_type": "image/png",
            "data_b64": (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4z8AAAAMBAQDN"
                "hb0OAAAAAElFTkSuQmCC"
            ),
        }

    def _h_activate_tab(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=False)
        self.page.activate(tab.id)
        return {
            "tab_id": tab.id,
            "title": tab.title if self.page.host_permission else None,
            "url": tab.url if self.page.host_permission else None,
            "activated": True,
        }

    def _h_scroll(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        tab.scrolled_to = str(params.get("direction") or "down")
        return {"tab_id": tab.id, "scrolled_to": tab.scrolled_to, "page_version": tab.page_version}

    def _h_create_tab(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self.page.open_tab(str(params.get("url") or "https://example.com/new"))
        if params.get("active", True):
            self.page.activate(tab.id)
        return {"tab_id": tab.id, "url": tab.url, "title": tab.title}

    def _h_close_tab(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=False)
        self.page.close(tab.id)
        return {"tab_id": tab.id, "closed": True}

    def _h_click(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        element = self.page.resolve_target(tab, params.get("target"), params.get("snapshot_id"))
        # A click that changes the page moves page_version, exactly as a real
        # mutation batch would, so a follow-up call with the old snapshot is stale.
        tab.page_version += 1
        return {
            "tab_id": tab.id,
            "clicked": {"element_id": element.id, "role": element.role, "name": element.name},
            "url": tab.url,
            "page_version": tab.page_version,
            "navigated": False,
        }

    def _h_type_text(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        element = self.page.resolve_target(tab, params.get("target"), params.get("snapshot_id"))
        tab.page_version += 1
        # The typed text is never echoed back: it is redacted in the ledger and
        # must not reappear through the result either.
        return {
            "tab_id": tab.id,
            "typed_into": {"element_id": element.id, "role": element.role, "name": element.name},
            "cleared": bool(params.get("clear")),
            "submitted": bool(params.get("press_enter")),
            "page_version": tab.page_version,
        }

    def _h_press_key(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        if params.get("target") is not None:
            self.page.resolve_target(tab, params.get("target"), params.get("snapshot_id"))
        tab.page_version += 1
        return {
            "tab_id": tab.id,
            "key": str(params.get("key") or ""),
            "page_version": tab.page_version,
            "navigated": False,
        }

    def _h_navigate(self, params: dict[str, Any]) -> dict[str, Any]:
        tab = self._tab_for(params, need_page=True)
        url = str(params.get("url") or "")
        if not url:
            raise _PeerError(BrowserErrorCode.NAVIGATION_FAILED, url=url)
        tab.url = url
        tab.title = f"Page at {url}"
        tab.page_version += 1
        self.page.snapshots = {
            sid: known for sid, known in self.page.snapshots.items() if known[0] != tab.id
        }
        return {
            "tab_id": tab.id,
            "url": tab.url,
            "title": tab.title,
            "page_version": tab.page_version,
            "status": tab.status,
        }

    # --- events the extension originates ---------------------------------

    def emit_event(self, event: str, payload: dict[str, Any] | None = None, *, seq: int = 1) -> None:
        """Send a ``browser.event`` frame — a tab activation, navigation or download.

        ``evt_`` ids are minted here because the extension mints them; using the
        ``req_`` prefix would let a stray event resolve a pending command future.
        """
        self.send(P.event_frame(f"{P.EVENT_ID_PREFIX}{seq}", event, payload or {}))


def error_envelope(code: BrowserErrorCode, **fmt: Any) -> dict[str, str]:
    """The envelope a test expects back for ``code`` — built by the real builder.

    Exported so a test never types a remedy sentence by hand: a copy would keep
    passing after the real wording was improved, and the wording is the part the
    model reads.
    """
    return browser_error(code, **fmt)


__all__ = [
    "DEFAULT_EXTENSION_VERSION",
    "FRAME_WAIT_ATTEMPTS",
    "FRAME_WAIT_STEP_S",
    "MAX_FRAMES",
    "BrowserPeer",
    "FakeElement",
    "FakeTab",
    "PeerProtocolError",
    "ScriptedBrowser",
    "default_tab",
    "error_envelope",
]
