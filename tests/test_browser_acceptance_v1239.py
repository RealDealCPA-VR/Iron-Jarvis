"""The ten acceptance behaviours, driven end to end over the REAL product (v1.239.0, D30).

Plan section 21 maps ten behaviours onto per-behaviour test files. This is the
eleventh file, and its value is precisely that it is not those ten: they drive
components — a backend with a stand-in socket, a tool with a transport double, a
route mounted on a bare ``FastAPI()`` — and each of them can be green while the
assembled product cannot do the thing at all. Ship 4 shipped exactly that: an
``/mcp`` route with 89 green tests that returned 401 to every credential on every
packaged install, because every one of those tests bypassed the middleware.

So everything here is driven through:

* ``create_app`` **with ``IRONJARVIS_TOKEN`` set** — the shipped configuration,
  never the developer's. Auth on, the origin guard on, the whole middleware
  stack on. A route that only answers with auth off is not a route this file can
  see working.
* the real ``/browser/ws`` socket, answered by the deterministic scripted peer
  (``tests/_fakes/browser_peer``), which speaks the actual protocol and decides
  staleness from the PAGE side — where the live node is — rather than by the
  daemon being patched into reporting it.
* the real ``POST /chat`` turn, whose model is scripted at
  ``platform.router.complete`` so a named tool call really arrives at
  ``chat_turn``'s own tool loop: the armed-set gate, the browser filters, the
  permission engine, the ledger write and the tool messages the model then reads
  are all the shipped ones. The only thing faked is the model's choice of call,
  which is the one thing a test must choose.
* the real ``POST /mcp`` with a real pane capability token minted by the app's
  OWN ``PaneTokenStore``.

**Offline, always.** No network and no real Chrome. Where a behaviour genuinely
cannot be driven offline it is said plainly in the docstring and what CAN be
driven is pinned instead — see :func:`test_6_a_download_becomes_a_usable_file`,
which cannot make Chromium fetch a file and therefore drives everything from the
add-on's completion report onwards, on a real file, through the real verifier.

**Every receive is bounded.** A ``TestClient`` WebSocket receive has no timeout.
An unbounded wait converts a protocol bug into a hung release gate, and nobody
can tell a deadlock from a slow runner. The peer's own waits raise
``PeerProtocolError``; this file's waits go through :func:`wait_until`, which
raises a named assertion. Nothing here asserts an elapsed duration: every budget
is a bound, never a performance promise.

**Spies take ``(*args, **kw)`` and call through**, per this repository's
monkeypatch rule, so a signature change is a red test in the lane that owns it
rather than a silent miss here.

What this file deliberately does NOT do: assert wording that a per-behaviour file
already owns. A copy of a remedy sentence here would keep passing after the real
sentence was improved. It asserts the BEHAVIOUR — the tab appears, the field is
typed into, the secret is nowhere, the disabled capability is absent AND refused
— and leaves the exact phrasing to the file that owns it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from iron_jarvis.browser import protocol as P
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import PROTOCOL_VERSION as MCP_PROTOCOL_VERSION
from iron_jarvis.mcpserver.session import SESSION_HEADER
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

from ._fakes.browser_peer import BrowserPeer, FakeElement, FakeTab, ScriptedBrowser

#: The install bearer this app is configured with. A real one is opaque; this
#: only has to be distinguishable from the pane token in an assertion — and it
#: has to be SET, because that is the shipped configuration and the one under
#: which Ship 4's two credential defects were invisible.
INSTALL_TOKEN = "install-bearer-for-the-acceptance-run"

#: Attempts, and the seconds each waits, while the test thread waits for daemon
#: state to reflect a frame. A budget, never a deadline: the assertion that
#: follows names what never happened, and nothing asserts how long it took.
WAIT_ATTEMPTS = 200
WAIT_STEP_S = 0.05

#: MCP's Accept header. Both media types, as the specification requires.
MCP_ACCEPT = "application/json, text/event-stream"

#: A credential-shaped string, deliberately unmistakable, so a substring search
#: over a whole payload PROVES absence instead of suggesting it. It carries none
#: of the risk classifier's own vocabulary ("password", "secret", "card"): a
#: marker spelled that way would escalate every ordinary typing call, and each
#: test would then be asserting the approval path while claiming the plain one.
SECRET = "hunter2-ACCEPTANCE-NEVER-WRITTEN-DOWN-4b71"

#: The three tabs of acceptance test 2, as the spec names them.
GOOGLE = "https://www.google.com/"
GITHUB = "https://github.com/"
IRS = "https://www.irs.gov/forms-pubs/about-form-1120-s"


def wait_until(predicate, what: str) -> None:
    """Block until ``predicate()`` holds, or fail BY NAME.

    Bounded by :data:`WAIT_ATTEMPTS`, because the daemon runs on the client's own
    portal thread and nothing here can await it. The bound is what keeps a
    regression a red test instead of a pinned CI core.
    """
    for _ in range(WAIT_ATTEMPTS):
        if predicate():
            return
        time.sleep(WAIT_STEP_S)
    raise AssertionError(f"never happened: {what}")


def three_tabs() -> ScriptedBrowser:
    """Google, GitHub and IRS.gov — the page model acceptance test 2 names.

    Each tab carries its own text and its own element registry, so a cross-tab
    comparison (test 5) can only pass by actually reading both tabs.
    """
    return ScriptedBrowser(
        [
            FakeTab(
                id=1,
                title="Google",
                url=GOOGLE,
                active=True,
                text="Google Search. I'm Feeling Lucky.",
                headings=[{"level": 1, "text": "Google"}],
                elements=[
                    FakeElement(
                        id="q",
                        role="textbox",
                        name="Search",
                        field_type="search",
                    ),
                    FakeElement(id="lucky", role="button", name="I'm Feeling Lucky"),
                ],
            ),
            FakeTab(
                id=2,
                title="GitHub",
                url=GITHUB,
                text="GitHub. Where the world builds software. 100 million developers.",
                headings=[{"level": 1, "text": "Build software better, together"}],
                elements=[FakeElement(id="signin", role="button", name="Sign in")],
            ),
            FakeTab(
                id=3,
                title="About Form 1120-S | Internal Revenue Service",
                url=IRS,
                text="Form 1120-S is used by corporations that elect to be S corporations.",
                headings=[{"level": 1, "text": "About Form 1120-S"}],
                elements=[FakeElement(id="pdf", role="link", name="Form 1120-S PDF")],
            ),
        ]
    )


class _FakePane:
    """A pane as ``PaneTokenStore.resolve`` and ``_pane_browser_allowed`` read one.

    Creating a REAL pane would spawn a shell for a test about HTTP authorisation
    and tool routing. The store's documented seam is ``pane_lookup``, and the
    capability is read LIVE off this object through the same ``capability()``
    accessor the checklist renders and the MCP grant reads — so the gate under
    test is the shipped one, not a second reading of a dict.
    """

    def __init__(self, pane_id: str, capabilities: dict[str, bool]) -> None:
        self.id = pane_id
        self.capabilities = dict(capabilities)
        self.cwd = ""
        self.alive = True

    def capability(self, name: str) -> bool:
        return self.capabilities.get(name, False) is True


class Bridge:
    """The shipped product, assembled: a real app, a real browser, two real lanes.

    Nothing in this class is a mock of anything under test. ``create_app`` builds
    the whole middleware stack with an install bearer configured; the peer speaks
    the real protocol on the real socket; the setting is written through the real
    ``PUT /settings``; the chat lane runs the real turn with the model's CHOICE of
    tool call scripted at the router; the MCP lane presents a real pane token
    minted by the app's own store.
    """

    PANE_ID = "term_acceptance_pane"

    def __init__(self, tmp_path: Path, monkeypatch, *, access: str = "interactive") -> None:
        monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
        self.app = create_app(str(tmp_path))
        self.client = TestClient(self.app)
        # The shipped credential rides every HTTP call: with auth on and no
        # header, every route in this file answers 401 and nothing is proved.
        self.client.headers["Authorization"] = f"Bearer {INSTALL_TOKEN}"
        self.client.__enter__()
        self.platform = self.app.state.platform
        self.peer: BrowserPeer | None = None
        #: Every system prompt the scripted model was handed, in order.
        self.systems: list[str] = []
        #: ``(tool_name, ctx.session_id, sorted(allowed_names))`` per registry
        #: invocation, from whichever lane made it.
        self.invocations: list[tuple[str, str, list[str]]] = []
        self.pane = _FakePane(self.PANE_ID, {"browser": True})
        self.platform.pane_tokens.pane_lookup = lambda pane_id: (
            self.pane if pane_id == self.PANE_ID else None
        )
        self.pane_token = self.platform.pane_tokens.mint(self.PANE_ID, {"browser": True})
        if access:
            self.set_access(access)

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self.peer is not None:
            self.peer.close()
            self.peer = None
        self.client.__exit__(None, None, None)

    def set_access(self, access: str) -> None:
        """Write ``browser_access`` through the real route, and prove it took."""
        response = self.client.put("/settings", json={"values": {"browser_access": access}})
        assert response.status_code == 200, response.text
        assert self.platform.browser.access() == access, (
            f"PUT /settings did not take: the runtime reads "
            f"{self.platform.browser.access()!r}, wanted {access!r}"
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {INSTALL_TOKEN}"}

    # --- the browser -------------------------------------------------------

    def connect(self, page: ScriptedBrowser | None = None, *, token: str = "") -> BrowserPeer:
        """Open a socket and run the REAL pairing handshake unless a token is given.

        A token means a RECONNECT — the add-on already holds a credential — which
        is the second half of acceptance test 1 and must not re-pair.
        """
        peer = BrowserPeer(
            self.client,
            page=page if page is not None else three_tabs(),
            headers=self.headers,
            token=token,
        )
        peer.__enter__()
        if not token:
            peer.pair()
        peer.expect_ready()
        self.peer = peer
        wait_until(lambda: self.platform.browser.connected, "the daemon adopted the socket")
        return peer

    def status(self) -> dict[str, Any]:
        response = self.client.get("/browser/status")
        assert response.status_code == 200, response.text
        return response.json()

    def await_active_tab(self) -> dict[str, Any]:
        """Block until the transport has cached an announced tab, or name it."""
        wait_until(
            lambda: bool(self.platform.browser.backend.active_tab),
            "the daemon cached a tab from browser.event",
        )
        return dict(self.platform.browser.backend.active_tab or {})

    # --- observability -----------------------------------------------------

    def watch_registry(self) -> None:
        """Record every ``registry.invoke``, from EVERY lane, and call through.

        This is the seam chat, the agent runtime, the workflow engine and the MCP
        server all pass through, which is why it is the one worth watching: a lane
        that executed a tool itself would record nothing here. The spy takes
        ``(*args, **kw)`` and calls through.
        """
        real_invoke = self.platform.registry.invoke

        async def spy_invoke(*args, **kw):
            name = args[0] if args else kw.get("name", "")
            ctx = args[2] if len(args) > 2 else kw.get("ctx", None)
            allowed = kw.get("allowed_names") or set()
            self.invocations.append(
                (str(name), str(getattr(ctx, "session_id", "")), sorted(allowed))
            )
            return await real_invoke(*args, **kw)

        self.platform.registry.invoke = spy_invoke

    def ledger_rows(self) -> list[ToolInvocation]:
        with Session(self.platform.engine) as session:
            return list(session.exec(select(ToolInvocation)).all())

    def events(self) -> list[dict[str, Any]]:
        return [
            {"type": event.type, "payload": event.payload}
            for event in self.platform.event_bus.history
        ]

    # --- lane A: the real chat turn ---------------------------------------

    def chat(
        self,
        *,
        tools: list[str],
        script: list[Any],
        message: str = "do the browser thing",
        **body: Any,
    ) -> tuple[dict[str, Any], list[str]]:
        """Drive one REAL ``POST /chat`` turn whose model asks for ``script``.

        Args:
            tools: the armed set, exactly as the dashboard sends it.
            script: one entry per model round. Each entry is either a list of
                ``(tool_name, args)`` — ``args`` may be a callable taking the
                tool outputs seen so far, which is how a click names the snapshot
                id the previous read returned — or a string, the model's final
                answer.
            message: the user's own words. They reach the risk gate as
                ``request_text`` (Q03's action justification), so a test whose
                behaviour depends on that names it.

        Returns:
            ``(response_json, tool_outputs)`` where ``tool_outputs`` is what the
            MODEL was shown, read off the transcript of the next round — the tool
            messages themselves, not a value this file computed.
        """
        rounds = list(script)
        seen: list[str] = []
        state = {"i": 0}

        async def fake_complete(*args, **kw):
            index = state["i"]
            state["i"] += 1
            self.systems.append(str(kw.get("system", "")))
            for msg in kw.get("messages") or []:
                if getattr(msg, "role", "") == "tool":
                    content = str(getattr(msg, "content", "") or "")
                    if content not in seen:
                        seen.append(content)
            step = rounds[index] if index < len(rounds) else "done"
            if isinstance(step, str):
                return RouteResult(LLMResponse(text=step), "mock", "mock")
            calls = []
            for order, (name, raw_args) in enumerate(step):
                resolved = raw_args(seen) if callable(raw_args) else dict(raw_args)
                calls.append(ToolCall(id=f"c{index}_{order}", name=name, arguments=resolved))
            return RouteResult(LLMResponse(text="", tool_calls=calls), "mock", "mock")

        self.platform.router.complete = fake_complete
        response = self.client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": message}], "tools": tools, **body},
        )
        assert response.status_code == 200, response.text
        return response.json(), seen

    # --- lane B: the real outward MCP server -------------------------------

    def mcp(self, payload: dict[str, Any], *, session_id: str = "", token: str | None = None):
        headers = {"Accept": MCP_ACCEPT}
        candidate = self.pane_token if token is None else token
        if candidate:
            headers["Authorization"] = f"Bearer {candidate}"
        if session_id:
            headers[SESSION_HEADER] = session_id
        return self.client.post("/mcp", json=payload, headers=headers)

    def mcp_initialize(self, *, token: str | None = None) -> str:
        """The real handshake; returns the session id off the RESPONSE HEADER."""
        response = self.mcp(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "clientInfo": {"name": "acceptance", "version": "1"},
                },
            },
            token=token,
        )
        assert response.status_code == 200, response.text
        session_id = response.headers.get(SESSION_HEADER, "")
        assert session_id, (
            "initialize returned no Mcp-Session-Id response header, which is the "
            "only place an MCP client looks for it"
        )
        self.mcp(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id=session_id,
            token=token,
        )
        return session_id

    def mcp_tools(self, *, session_id: str = "", token: str | None = None) -> list[dict[str, Any]]:
        sid = session_id or self.mcp_initialize(token=token)
        response = self.mcp(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session_id=sid,
            token=token,
        )
        assert response.status_code == 200, response.text
        return list(response.json()["result"]["tools"])

    def mcp_call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        session_id: str = "",
        token: str | None = None,
    ):
        sid = session_id or self.mcp_initialize(token=token)
        return self.mcp(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments or {})},
            },
            session_id=sid,
            token=token,
        )

    def mcp_text(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """One ``tools/call``, asserted successful, returning its text block."""
        response = self.mcp_call(name, arguments)
        assert response.status_code == 200, response.text
        body = response.json()
        assert "error" not in body, body
        result = body["result"]
        assert result.get("isError") is False, result
        blocks = result.get("content") or []
        assert blocks and blocks[0].get("type") == "text", result
        return str(blocks[0]["text"])


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    made = Bridge(tmp_path, monkeypatch)
    try:
        yield made
    finally:
        made.close()


def outputs_for(outputs: list[str], needle: str) -> str:
    """The one tool output containing ``needle``, or a named failure."""
    matches = [text for text in outputs if needle in text]
    assert matches, f"no tool output mentions {needle!r}; outputs were {outputs!r}"
    return matches[0]


def snapshot_id_in(text: str) -> str:
    """The snapshot id a read tool printed, so a later call can name it."""
    for line in text.splitlines():
        if line.startswith("Snapshot "):
            return line.split()[1]
    raise AssertionError(f"no snapshot id in the read output: {text!r}")


def element_id_for(text: str, name: str) -> str:
    """The element id whose accessible name is ``name``, read off the output."""
    for line in text.splitlines():
        if name in line and "[" in line:
            token = line[line.index("[") + 1 : line.index("]")] if "]" in line else ""
            if token:
                return token
        if name in line:
            head = line.strip().split()[0].strip(":-")
            if head:
                return head
    raise AssertionError(f"no element line for {name!r} in: {text!r}")


# =========================================================================== #
# 1. Connection, disconnect, reconnect
# =========================================================================== #
def test_1_a_browser_pairs_disconnects_and_reconnects_on_the_same_credential(bridge):
    """The whole lifecycle on the real socket, with auth on.

    The reconnect half is the one with teeth: a second connection presenting the
    SAME pairing token must be adopted without pairing again. If it had to
    re-pair, every browser restart would put a Pair button in front of the user
    and the credential would be decorative.

    ``connected`` is read from ``GET /browser/status`` — the route the card polls
    — rather than from the runtime object, because a status route that cannot see
    a live browser is the failure the user actually experiences.
    """
    peer = bridge.connect()
    token = peer.token
    assert token, "pairing produced no credential"
    assert bridge.status()["connected"] is True, bridge.status()
    assert bridge.status()["paired"] is True, bridge.status()

    peer.close()
    bridge.peer = None
    wait_until(
        lambda: bridge.status()["connected"] is False,
        "the daemon noticed the browser went away",
    )

    again = bridge.connect(token=token)
    assert again.pairing_request_id == "", (
        "the daemon asked a browser that already holds a credential to pair "
        "again; every browser restart would then need the user"
    )
    assert bridge.status()["connected"] is True, bridge.status()
    # And it WORKS, not merely connects: a socket the daemon adopted but cannot
    # command is a green connection over a dead capability.
    _body, outputs = bridge.chat(
        tools=["browser_list_tabs"], script=[[("browser_list_tabs", {})], "listed"]
    )
    assert outputs_for(outputs, GITHUB)


def test_1b_a_forgotten_browser_cannot_reconnect_on_the_old_credential(bridge):
    """Forget revokes. Without this, "Forget this browser" is a button that
    changes a label while the add-on keeps reconnecting with a live token.

    The assertion is on the DAEMON's own answer to the attempt, not on the shape
    of the client's exception: the route closes a socket carrying a dead
    credential with 1008 before accepting it, and the TestClient may surface that
    on the connect or on the first receive. Either shape is the refusal, and
    asserting which one would pin the client library rather than the product.

    Every assertion about the attempt is made WHILE THE STALE SOCKET IS STILL
    OPEN, and that ordering is the test. Asserting after the ``finally`` measured
    post-close state instead: a daemon that HAD adopted the revoked credential
    would be closed a line later, and whether ``connected`` was still true when
    the assertion ran came down to whether the portal thread had processed the
    disconnect yet. Measured -- forcing ``browser_ws_token_ok`` to return True
    turned the old form red only incidentally, on the single gate that decides
    whether "Forget this browser" is real or a label. ``paired`` cannot cover for
    it either: ``forget`` clears the pairing store whether or not the socket was
    accepted.

    The peer is still closed in a ``finally``, for a reason this file cares about
    more than most: a peer left open when an assertion fails hangs the app's
    shutdown instead of failing the test, which is precisely the hung-release-gate
    outcome every bound here exists to prevent (this was measured -- a mutation
    made it hang).
    """
    peer = bridge.connect()
    token = peer.token
    assert bridge.client.post("/browser/forget").json()["forgotten"] is True
    peer.close()
    bridge.peer = None

    stale = BrowserPeer(bridge.client, headers=bridge.headers, token=token)
    try:
        refused: BaseException | None = None
        try:
            stale.connect()
            stale.expect_ready()
        except Exception as exc:  # noqa: BLE001 - the refusal, in whichever shape
            refused = exc
        assert refused is not None, (
            "the daemon told a socket carrying a REVOKED credential that it is "
            "the authoritative browser: Forget only changed a label"
        )
        assert not [f for f in stale.frames if f.get("type") == P.FRAME_READY], (
            "browser.ready reached a socket presenting a revoked credential"
        )
        # Read while the stale socket is still open, so an adoption is visible
        # rather than already cleaned up by the close below.
        assert bridge.status()["connected"] is False, (
            "a revoked credential still opened the browser socket, so Forget only "
            "changed a label"
        )
    finally:
        try:
            stale.close()
        except Exception:
            pass

    assert bridge.status()["connected"] is False, bridge.status()
    assert bridge.status()["paired"] is False, bridge.status()


# =========================================================================== #
# 2. Tab discovery
# =========================================================================== #
def test_2_tab_discovery_lists_every_open_tab_through_a_real_chat_turn(bridge):
    """Google, GitHub and IRS.gov reach the MODEL, not just the tool.

    Asserted on the tool MESSAGE the next round was handed, which is the only
    thing the model ever sees. A tool that returned the tabs into a result nobody
    put in the transcript would pass a tool-level test and answer "I cannot see
    your tabs" in the product.
    """
    bridge.connect()
    body, outputs = bridge.chat(
        tools=["browser_list_tabs"],
        script=[[("browser_list_tabs", {})], "there are three tabs"],
        message="what tabs do I have open?",
    )

    listed = outputs_for(outputs, GOOGLE)
    assert GITHUB in listed and IRS in listed, listed
    assert "Google" in listed and "GitHub" in listed
    assert body["tools_used"] == ["browser_list_tabs"], body["tools_used"]


# =========================================================================== #
# 3. Current-page understanding
# =========================================================================== #
def test_3_the_active_tab_is_ambient_and_the_page_can_be_read(bridge):
    """Two halves of one behaviour, and both are needed.

    The AMBIENT half: the model is told which page the user is looking at without
    spending a tool call, so "summarise this page" resolves to a tab. That block
    is asserted on the system prompt the scripted model was actually handed.

    The READ half: ``browser_read_page`` returns that page's text to the model.
    Together they are "understand the page I am on"; either alone is not.
    """
    peer = bridge.connect()
    # A real tab switch is BOTH: the browser's active tab moves, and the add-on
    # says so. Emitting the event alone would leave the peer answering about the
    # tab the user left, and the read half would then pass or fail for a reason
    # that has nothing to do with the behaviour.
    peer.page.activate(3)
    peer.emit_event(
        P.EVENT_TAB_ACTIVATED,
        {"tab_id": 3, "title": "About Form 1120-S | Internal Revenue Service", "url": IRS},
    )
    bridge.await_active_tab()

    _body, outputs = bridge.chat(
        tools=["browser_read_page"],
        script=[[("browser_read_page", {})], "it is the 1120-S page"],
        message="what page am I looking at?",
    )

    assert bridge.systems, "the turn never reached the model"
    ambient = bridge.systems[0]
    assert "About Form 1120-S" in ambient and IRS in ambient, (
        "the ambient browser block never named the active tab, so the model "
        f"could not resolve 'this page' at all: {ambient!r}"
    )
    read = outputs_for(outputs, "Form 1120-S is used by corporations")
    assert IRS in read


# =========================================================================== #
# 4. Interaction: read, find a field, type, press Enter, see the navigation
# =========================================================================== #
def test_4_read_find_a_field_type_press_enter_and_see_the_navigation(bridge):
    """The whole interaction loop in ONE real chat turn, then the page moves.

    The type call names the snapshot id the READ returned, resolved from the tool
    message the model was shown — the same way a model would have to. A test that
    passed the id from its own page model would prove the peer can talk to
    itself.

    "See the navigation" is driven the way the add-on drives it: the page emits
    ``navigation_completed`` after the submit, and the daemon must then report the
    NEW url as the active tab. The daemon does not learn a same-tab navigation
    any other way — that is exactly why the event exists.
    """
    peer = bridge.connect()
    # The user is looking at the Google tab, and the add-on has said so. Without
    # that the daemon holds no active tab, and a navigation in a tab it has never
    # been told about must NOT become "the active tab" — a background tab moving
    # is not the user moving.
    peer.emit_event(P.EVENT_TAB_ACTIVATED, {"tab_id": 1, "title": "Google", "url": GOOGLE})
    bridge.await_active_tab()
    bridge.watch_registry()

    def type_args(seen: list[str]) -> dict[str, Any]:
        read = outputs_for(seen, "Google Search")
        return {
            "tab_id": 1,
            "target": {"element_id": "q"},
            "snapshot_id": snapshot_id_in(read),
            "text": "form 1120-s instructions",
            "press_enter": True,
        }

    _body, outputs = bridge.chat(
        tools=["browser_read_page", "browser_type"],
        script=[
            [("browser_read_page", {"tab_id": 1})],
            [("browser_type", type_args)],
            "searched",
        ],
        message="search Google for form 1120-s instructions",
    )

    typed = outputs_for(outputs, "Typed")
    assert "Enter was pressed" in typed, typed
    assert [name for name, *_ in bridge.invocations] == ["browser_read_page", "browser_type"]
    assert (P.METHOD_TYPE_TEXT, ) in [(method,) for method, _ in peer.commands], peer.commands

    # The page moves, and the daemon hears about it the only way it can.
    peer.page.tabs[0].url = "https://www.google.com/search?q=form+1120-s"
    peer.page.tabs[0].title = "form 1120-s instructions - Google Search"
    peer.emit_event(
        P.EVENT_NAVIGATION_COMPLETED,
        {
            "tab_id": 1,
            "url": "https://www.google.com/search?q=form+1120-s",
            "title": "form 1120-s instructions - Google Search",
        },
        seq=2,
    )
    wait_until(
        lambda: "search?q=" in str((bridge.platform.browser.backend.active_tab or {}).get("url")),
        "the daemon saw the navigation the submit caused",
    )
    assert "search?q=" in bridge.status()["active_tab"]["url"], bridge.status()


# =========================================================================== #
# 5. Cross-tab inspection and comparison
# =========================================================================== #
def test_5_two_tabs_are_read_in_one_turn_and_do_not_bleed_into_each_other(bridge):
    """A comparison is only possible if each read answers about ITS OWN tab.

    The failure this catches is a daemon that resolves every read to the ACTIVE
    tab: both outputs then describe the same page, the model reports them as
    identical, and nothing is red anywhere. So both texts are asserted present,
    and each is asserted ABSENT from the other's output.
    """
    bridge.connect()
    _body, outputs = bridge.chat(
        tools=["browser_read_page"],
        script=[
            [
                ("browser_read_page", {"tab_id": 2}),
                ("browser_read_page", {"tab_id": 3}),
            ],
            "GitHub is a code host; the IRS page is about Form 1120-S",
        ],
        message="compare my GitHub tab with my IRS tab",
    )

    github = outputs_for(outputs, "Where the world builds software")
    irs = outputs_for(outputs, "Form 1120-S is used by corporations")
    assert github is not irs, "both reads produced the same output object"
    assert IRS not in github, (
        "the GitHub read carried the IRS page too — every read resolved to one "
        f"tab, so a comparison compares a page with itself: {github!r}"
    )
    assert GITHUB not in irs, irs


# =========================================================================== #
# 6. Browser plus filesystem: a download becomes a usable file
# =========================================================================== #
def test_6_a_download_becomes_a_usable_file(bridge, tmp_path):
    """WHAT CANNOT BE DRIVEN OFFLINE, stated rather than faked.

    A real Chromium download needs a real Chrome, a real network and a real
    server, and none of the three exists here. So the half this file can drive is
    everything from the add-on's completion report onwards, which is also where
    every failure this feature has ever had lives: the daemon VERIFIES the
    reported path, attaches it to the acting result that started the transfer,
    and the existing file tools read it. The file is real, on real disk, written
    by this test — nothing here fakes a filesystem either.

    What is deliberately NOT claimed: that Chrome saves where it says it does.
    That is the live-drive step of Ship 3, and no offline test can stand in for
    it.
    """
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    saved = downloads / "form1120s.txt"
    saved.write_text("Closing balance 1,234.56\n", encoding="utf-8")
    reported = {
        "download_id": 11,
        "filename": str(saved),
        "source_url": "https://www.irs.gov/pub/irs-pdf/f1120s.pdf",
        "final_url": "https://cdn.irs.gov/f1120s.pdf",
        "tab_id": 3,
        "bytes": saved.stat().st_size,
        "mime": "text/plain",
        "timestamp": "2026-09-07T12:00:00Z",
    }

    peer = bridge.connect()
    # THE ADD-ON PUTS IT ON THE CLICK'S OWN RESULT, and that is the whole
    # agent-facing capability: ``extensions/chrome/src/background/downloads.ts``
    # says so in as many words — the click handler awaits the completion and it
    # rides ``ClickResult.download``, because an event the model never sees is a
    # path it cannot hand to ``read_document``. The peer models exactly that, by
    # wrapping its own handler (``*args, **kw``, calls through) rather than by
    # the daemon being taught to invent one.
    real_click = peer._h_click

    def click_that_downloads(*args, **kw):
        result = real_click(*args, **kw)
        result["download"] = dict(reported)
        return result

    peer._h_click = click_that_downloads
    # And the add-on ALSO emits the completion event, as it really does. Both
    # channels carrying one ``download_id`` must produce ONE file, or the model
    # is told about the same statement twice and files it twice.
    peer.emit_event(P.EVENT_DOWNLOAD_COMPLETED, dict(reported))
    wait_until(
        lambda: bool(bridge.platform.browser.backend.recent_downloads),
        "the daemon recorded the completed download",
    )

    def click_args(seen: list[str]) -> dict[str, Any]:
        read = outputs_for(seen, "Form 1120-S is used by corporations")
        return {
            "tab_id": 3,
            "target": {"element_id": "pdf"},
            "snapshot_id": snapshot_id_in(read),
        }

    _body, outputs = bridge.chat(
        tools=["browser_read_page", "browser_click", "read_document"],
        script=[
            [("browser_read_page", {"tab_id": 3})],
            [("browser_click", click_args)],
            # The model is handed the verified path in the click's own answer and
            # reads it with an ordinary file tool. That composition IS the
            # feature: plan 10.3 adds no browser_download tool and no new file
            # tool.
            [("read_document", lambda seen: {"path": _downloaded_path(seen)})],
            "the statement says the closing balance is 1,234.56",
        ],
        message="download the 1120-S and tell me what it says",
    )

    clicked = outputs_for(outputs, "downloaded a file to")
    assert str(saved) in clicked, clicked
    assert "Closing balance 1,234.56" in outputs_for(outputs, "Closing balance"), (
        "the verified absolute path did not reach a reader, so a download the "
        "user can see is a file Jarvis cannot open"
    )
    remembered = bridge.platform.browser.backend.recent_downloads
    assert len(remembered) == 1, (
        "one completed transfer, reported on both channels, was remembered "
        f"twice; the model would file the same statement twice: {remembered!r}"
    )


def _downloaded_path(seen: list[str]) -> str:
    """The absolute path the click's answer named, read the way a model would."""
    line = outputs_for(seen, "downloaded a file to")
    tail = line.split("downloaded a file to", 1)[1]
    return tail.split(" - you can", 1)[0].strip()


# =========================================================================== #
# 7. Harness independence: two execution paths, ONE BrowserService
# =========================================================================== #
def test_7_the_chat_lane_and_the_mcp_lane_are_one_service_and_one_registry(bridge):
    """The test with teeth, and it has three parts because one would not do.

    1. **One service object.** The tool the registry hands each lane is the SAME
       instance, holding the SAME ``BrowserRuntime`` the socket adopted. Two
       registries or two runtimes would answer this test's other two parts
       identically while the harness drove a different browser.
    2. **One execution path.** Both lanes are observed at ``registry.invoke`` —
       the seam chat, the agent runtime and the workflow engine already share. A
       route that ran ``tool.execute`` itself, which is exactly how a second
       implementation gets built, records nothing there and fails here.
    3. **One answer.** The same call over both lanes returns the same text
       against the same page. Compared byte for byte on ``browser_list_tabs``,
       whose output carries no per-call identifier.

    WHERE THE TWO LANES DO DIFFER, said out loud rather than papered over, since
    that is what this behaviour asks for. It is not the execution path and it is
    not the service — it is the CONSENT. Eight of the fourteen tools are
    ``ask``-tier. In a chat turn the armed set the user (or autoselect) put on
    that turn rides into ``invoke`` as its ``session_allow``, so an armed
    ``browser_click`` runs without a card; over MCP nothing is ever self-granted
    (D17A), so the same call pauses on an approval card and waits for a human.
    The same request can therefore RUN on one lane and WAIT on the other. That is
    the designed difference between a surface the user is standing in front of
    and a credential handed to an external process — but it is a difference, and
    a harness author who expects parity should read it here. It is not asserted:
    the assertion would be a call that waits for a human, which is the one thing
    an offline gate must never contain.
    """
    bridge.connect()
    bridge.watch_registry()

    chat_tool = bridge.platform.registry.get("browser_list_tabs")
    assert chat_tool is not None
    assert chat_tool.browser is bridge.platform.browser, (
        "the registered tool holds a different BrowserService than the one the "
        "socket adopted — the two lanes would drive two browsers"
    )

    _body, outputs = bridge.chat(
        tools=["browser_list_tabs"], script=[[("browser_list_tabs", {})], "listed"]
    )
    chat_text = outputs_for(outputs, GOOGLE)
    mcp_text = bridge.mcp_text("browser_list_tabs", {})

    assert mcp_text == chat_text, (
        "the same tool, on the same page, answered the two lanes differently. "
        "That is a second execution path, however small the difference looks:\n"
        f"chat: {chat_text!r}\nmcp:  {mcp_text!r}"
    )

    lanes = {session for _name, session, _allowed in bridge.invocations}
    assert lanes == {"chat", f"mcp:{Bridge.PANE_ID}"}, (
        "both lanes must reach registry.invoke, and be attributable there: "
        f"{bridge.invocations!r}"
    )
    assert [name for name, *_ in bridge.invocations] == [
        "browser_list_tabs",
        "browser_list_tabs",
    ]
    for name, session, allowed in bridge.invocations:
        assert name in allowed, (
            f"the {session} lane invoked {name} without it being in the roster it "
            "passed as allowed_names; the v1.227.0 roster gate is bypassed there"
        )


def test_7b_the_mcp_roster_is_the_roster_it_advertised(bridge):
    """Discovery and execution must agree, or a harness is told about a tool it
    cannot call — which reads to its model as a broken tool rather than a
    setting (D09A asks for the filter in both places)."""
    bridge.connect()
    bridge.watch_registry()
    listed = {tool["name"] for tool in bridge.mcp_tools()}
    assert listed, "the pane holds Browser and was advertised nothing"

    bridge.mcp_text("browser_get_active_tab", {})
    _name, _session, allowed = bridge.invocations[-1]
    assert set(allowed) == listed, (
        f"advertised {sorted(listed)} but armed {allowed} at call time"
    )


# =========================================================================== #
# 8. Stale element, then a fresh snapshot succeeds
# =========================================================================== #
def test_8_a_stale_element_is_refused_and_a_fresh_read_then_works(bridge):
    """Both halves in ONE turn, because the first alone is satisfied by a tool
    that refuses everything.

    The page is moved from the PAGE side (``advance_page_version``), the way a
    real mutation batch moves it, and the peer answers staleness the way the
    content script does — from where the live node is. A test that patched the
    daemon's snapshot cache would prove the daemon can format ``STALE_ELEMENT``,
    not that it detects the condition.

    The recovery is the half that makes the refusal usable: the remedy the
    refusal names has to actually WORK on the next call, or the model is stuck in
    a loop being told to re-read a page that never yields a usable id.
    """
    peer = bridge.connect()

    def click_the_newest_read(seen: list[str]) -> dict[str, Any]:
        read = [text for text in seen if "Google Search" in text][-1]
        return {
            "tab_id": 1,
            "target": {"element_id": "lucky"},
            "snapshot_id": snapshot_id_in(read),
        }

    def move_the_page_then_click(seen: list[str]) -> dict[str, Any]:
        # The page changes between the read and the click — the ordinary case on
        # any live page, and the whole reason element ids are per-snapshot. Done
        # here, in the argument builder, because that is the moment BETWEEN the
        # two calls.
        peer.page.advance_page_version(1)
        return click_the_newest_read(seen)

    _body, outputs = bridge.chat(
        tools=["browser_read_page", "browser_click"],
        script=[
            [("browser_read_page", {"tab_id": 1})],
            [("browser_click", move_the_page_then_click)],
            [("browser_read_page", {"tab_id": 1})],
            [("browser_click", click_the_newest_read)],
            "clicked it in the end",
        ],
        message="click I'm Feeling Lucky",
    )

    refusals = [index for index, text in enumerate(outputs) if "browser_read_page" in text
                and "Clicked" not in text]
    successes = [index for index, text in enumerate(outputs) if text.startswith("Clicked")]
    assert refusals, (
        f"the click on a page that had moved was not refused at all: {outputs!r}"
    )
    assert successes, (
        "no click ever succeeded, so 'call browser_read_page and try again' is a "
        f"remedy that does not work: {outputs!r}"
    )
    assert min(refusals) < min(successes), (
        "the successful click came before the refusal; the sequence under test "
        "did not happen in the order it claims"
    )


def test_8b_a_refused_stale_click_changes_nothing_in_the_page(bridge):
    """The refusal must cost the page nothing.

    Held separate from the recovery above so the two failures read differently: a
    daemon that refuses a CURRENT snapshot is a different defect from one that
    lets a stale click land, and a single test would report either as the same
    red. ``page_version`` is the page's own counter — the peer moves it on every
    real click — so an unchanged counter is the page saying nothing happened to
    it, rather than this file inferring it from a sentence.
    """
    peer = bridge.connect()

    first = bridge.chat(
        tools=["browser_read_page"], script=[[("browser_read_page", {"tab_id": 1})], "read"]
    )[1]
    stale_id = snapshot_id_in(outputs_for(first, "Google Search"))
    peer.page.advance_page_version(1)
    settled = peer.page.tabs[0].page_version

    body, outputs = bridge.chat(
        tools=["browser_click"],
        script=[
            [
                (
                    "browser_click",
                    {
                        "tab_id": 1,
                        "target": {"element_id": "lucky"},
                        "snapshot_id": stale_id,
                    },
                )
            ],
            "it refused",
        ],
        message="click I'm Feeling Lucky",
    )

    refusal = outputs[-1] if outputs else ""
    assert "browser_read_page" in refusal, (
        "the stale refusal does not name the one call that fixes it, so the "
        f"model's next move is to retry the same dead id: {refusal!r}"
    )
    assert peer.page.tabs[0].page_version == settled, (
        "the page moved during a refused click, so the refusal was reported "
        "AFTER the element was acted on — the worst available order"
    )
    assert body["tools_used"] == [], (
        f"a refused click is reported as a tool that ran: {body['tools_used']}"
    )


# =========================================================================== #
# 9. A sensitive field: never exposed, never logged
# =========================================================================== #
def test_9_a_sensitive_field_is_never_read_and_the_typed_text_is_never_written_down(
    bridge,
):
    """One behaviour, three places it could leak, all of them checked.

    * the READ — a password box is reported as present and sensitive, with no
      value, ever;
    * the ANSWER — typing returns a length, never the characters;
    * everything AT REST — the ledger row (``args_json`` and ``output``), every
      published event payload, and the HTTP response body.

    The whole secret string is searched for across each, so absence is proven
    rather than suggested.
    """
    page = ScriptedBrowser(
        [
            FakeTab(
                id=9,
                title="Sign in | bank.example",
                url="https://bank.example/login",
                active=True,
                text="Sign in to your account",
                elements=[
                    FakeElement(id="user", role="textbox", name="Username"),
                    FakeElement(
                        id="pw", role="textbox", name="Password", field_type="password"
                    ),
                ],
            )
        ]
    )
    bridge.connect(page)

    def type_args(seen: list[str]) -> dict[str, Any]:
        read = outputs_for(seen, "Sign in to your account")
        return {
            "tab_id": 9,
            "target": {"element_id": "user"},
            "snapshot_id": snapshot_id_in(read),
            "text": SECRET,
        }

    body, outputs = bridge.chat(
        tools=["browser_read_page", "browser_type"],
        script=[
            [("browser_read_page", {"tab_id": 9})],
            [("browser_type", type_args)],
            "typed",
        ],
        message="sign me in",
    )

    read = outputs_for(outputs, "Sign in to your account")
    assert "sensitive" in read.lower(), (
        f"the password field was not reported as sensitive at all: {read!r}"
    )
    assert SECRET not in read

    haystacks: dict[str, str] = {
        "the chat response body": json.dumps(body),
        "the tool messages the model saw": json.dumps(outputs),
        "the event stream": json.dumps(bridge.events(), default=str),
        "the ledger": json.dumps(
            [
                {"tool": row.tool, "args": row.args_json, "output": row.output}
                for row in bridge.ledger_rows()
            ]
        ),
    }
    for where, text in haystacks.items():
        assert SECRET not in text, f"the typed text was written into {where}"
    assert any(
        row.tool == "browser_type" and "REDACTED" in row.args_json
        for row in bridge.ledger_rows()
    ), (
        "no ledger row shows browser_type's arguments as redacted, so either the "
        "row is missing (the action is unauditable) or the text was stored"
    )


# =========================================================================== #
# 10. Capability disabled: absent from discovery AND refused on invocation
# =========================================================================== #
def test_10_browser_access_off_removes_the_tools_and_refuses_them_anyway(bridge):
    """The global switch, in both places D09A requires.

    ABSENT: the turn arms no ``browser_*`` name except ``browser_get_status``,
    the documented deviation that exists so a default install can answer "is my
    browser connected?" rather than answering from nothing.

    REFUSED: naming one anyway is refused. Discovery filtering alone is a
    suggestion — history carries earlier turns' calls and local models invent
    names — so the tool's own re-check inside ``execute`` is what makes the
    setting a rule.
    """
    bridge.connect()
    bridge.set_access("off")

    body, outputs = bridge.chat(
        tools=["browser_read_page", "browser_get_status", "browser_click"],
        script=[
            [("browser_read_page", {"tab_id": 1}), ("browser_click", {"target": {"css": "#x"}})],
            "refused",
        ],
        message="read my page",
    )

    armed = [
        name
        for name in (bridge.systems[0].split("armed these tools")[-1] if bridge.systems else "")
        .replace(",", " ")
        .split()
        if name.startswith("browser_")
    ]
    assert armed in ([], ["browser_get_status"], ["browser_get_status."]), (
        f"a browser tool was offered while Browser access is off: {armed}"
    )
    assert body["tools_used"] == [], (
        f"a browser tool RAN while Browser access is off: {body['tools_used']}"
    )
    for text in outputs:
        assert "Google Search" not in text, (
            f"a page was read while Browser access is off: {text!r}"
        )


def test_10a_the_tools_own_gate_refuses_a_name_that_was_armed_before_the_switch(
    bridge,
):
    """THE REFUSAL THAT IS NOT THE DISCOVERY FILTER'S, reached the way a real
    turn reaches it.

    ``test_10`` alone cannot see this and was measured not to: with the tool's
    own access check defeated, it stayed green, because the filter had already
    removed the name and the registry refused it as *not armed*. Two gates, one
    of them provable — which is the shape D09A exists to prevent.

    So this turn arms the tool while Browser is ON, and the user switches Browser
    OFF between the model's first round and its second. The name is in the armed
    set and the registry lets it through, so what refuses the call is the
    daemon's own live re-read of the setting. That is not a contrived sequence:
    ``access()`` is documented as read LIVE on every call precisely because a
    ``PUT /settings`` must take effect at once, mid-turn included.

    TWO INDEPENDENT GATES REFUSE IT, measured by mutation rather than assumed:
    defeating ``_BrowserTool._refuse_if_unavailable`` alone leaves this green,
    and so does defeating ``BrowserRuntime.require`` alone — the pin only turns
    red when BOTH are defeated. That is defence in depth working exactly as the
    tools module describes it, and it is worth stating here so a reader does not
    mistake this test for a pin on one of them. What it pins is the PROPERTY:
    after the switch, no page reaches the model.
    """
    bridge.connect()

    def switch_it_off_then_read(seen: list[str]) -> dict[str, Any]:
        # Written on the config object rather than through ``PUT /settings``:
        # this runs inside the model call, on the daemon's own event loop, and a
        # ``TestClient`` request from that thread answers "This method cannot be
        # called from the event loop thread". The setting is the same object the
        # route writes, and ``BrowserRuntime.access()`` re-reads it live on every
        # call — which is the property under test.
        bridge.platform.config.browser_access = "off"
        assert bridge.platform.browser.access() == "off"
        return {"tab_id": 1}

    body, outputs = bridge.chat(
        tools=["browser_read_page"],
        script=[
            [("browser_read_page", {"tab_id": 1})],
            [("browser_read_page", switch_it_off_then_read)],
            "stopped",
        ],
        message="read my page twice",
    )

    assert any("Google Search" in text for text in outputs), (
        "the first read failed too, so this test proves nothing about the second"
    )
    assert body["tools_used"] == ["browser_read_page"], (
        "the read AFTER Browser was switched off is counted as a tool that ran: "
        f"{body['tools_used']}"
    )
    reads = [text for text in outputs if "Google Search" in text]
    assert len(reads) == 1, (
        "a page was read after the user switched Browser off. The armed set was "
        "computed before the switch, so the discovery filter cannot refuse this "
        "call — the tool's own live access check is the whole gate here"
    )


def test_10b_a_pane_without_the_browser_capability_sees_nothing_and_is_refused(bridge):
    """The PANE switch, over the real ``/mcp`` with the real middleware stack.

    Both halves, and the second is the one that matters: a harness that is shown
    no browser tools can still NAME one, because a model that has driven Jarvis
    before remembers the names.

    WHAT THE SHIPPED ANSWER ACTUALLY IS, rather than what a test might prefer:
    this pane grants only capabilities MCP does not serve in this version, so the
    server refuses the whole session with a 403 that names the remedy — the box
    to tick and the relaunch — instead of handshaking and then listing nothing.
    Both readings satisfy "absent from discovery"; only one of them tells the
    harness's author what to do, and it is the one that ships. What this test
    pins is that neither ``tools/list`` nor ``tools/call`` yields a browser tool,
    and that the refused call executed NOTHING.
    """
    bridge.connect()
    bridge.pane.capabilities = {"files": True, "browser": False}
    token = bridge.platform.pane_tokens.mint(Bridge.PANE_ID, {"files": True})
    before = [row.tool for row in bridge.ledger_rows()]

    session_id = bridge.mcp_initialize(token=token)
    listing = bridge.mcp(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session_id=session_id,
        token=token,
    )
    assert listing.status_code == 403, listing.text
    assert "browser_" not in listing.text, (
        f"a pane without Browser was offered a browser tool: {listing.text}"
    )
    assert "Browser" in listing.json()["detail"], (
        "the refusal does not name the setting that fixes it, so the harness's "
        f"author has nothing to act on: {listing.text}"
    )

    refused = bridge.mcp_call(
        "browser_list_tabs", {}, session_id=session_id, token=token
    )
    assert refused.status_code == 403, refused.text
    assert [row.tool for row in bridge.ledger_rows()] == before, (
        "a refused MCP call still reached the tool: the ledger grew"
    )


def test_10c_the_same_pane_with_browser_ticked_is_served(bridge):
    """The other side of 10b, because a refusal that is permanent is not a gate.

    Without this, ``test_10b`` is equally satisfied by an ``/mcp`` that refuses
    every pane — which is exactly the state Ship 4 shipped in and which 89 green
    tests could not see.
    """
    bridge.connect()
    listed = {tool["name"] for tool in bridge.mcp_tools()}
    assert any(name.startswith("browser_") for name in listed), listed
    assert bridge.mcp_text("browser_list_tabs", {}), "the served pane got nothing back"
