"""``/mcp``: Jarvis as an MCP SERVER, for the first time (v1.238.0, plan 12.1/12.3).

Ship 4's whole claim is Test 7 — "at least two execution paths use the same
canonical BrowserService… do not implement browser behavior separately for the
second harness". A test file for an outward MCP server can be written so that it
proves nothing at all: assert a JSON shape, assert a tool list is non-empty, and
a second implementation with its own gates would pass every line. So the pins
here are chosen for what they would catch:

* **THE REPOSITORY'S OWN CLIENT DRIVES THE SERVER.** ``mcp/client.py``'s
  ``HttpTransport`` has spoken Streamable HTTP against real servers since v1.x;
  it is pointed at this server's route through a ``TestClient`` and performs its
  real handshake — ``initialize``, capture ``mcp-session-id`` from the RESPONSE
  HEADERS, ``notifications/initialized``, then ``tools/list`` and ``tools/call``.
  A server that answered a plausible-looking shape but put the session id in the
  body, or answered the notification, would fail here and nowhere else. This is
  also why no ``mcp`` SDK was added: the two halves check each other.
* **THERE IS NO SECOND EXECUTION PATH.** ``tools/call`` is asserted to reach
  ``ToolRegistry.invoke`` with ``allowed_names`` equal to the very set
  ``tools/list`` advertised — the v1.227.0 roster gate — and the ledger row it
  writes is read back out of the database. A direct ``tool.execute`` in the route
  would answer identically to a shape assertion and fail both of these.
* **THE CAPABILITY FILTER RUNS BEFORE DISCOVERY, NOT ONLY BEFORE EXECUTION**
  (D09A). A pane without Browser receives not one ``browser_*`` definition, AND a
  call naming one is refused 403. Both, because either alone is the bug.
* **EVERY WRONG CREDENTIAL IS REFUSED BY ITS OWN CHECK** (D17A, plan 12.3). The
  install bearer, a REAL browser pairing token minted by the real
  ``PairingStore``, and no token at all: three 401s with three different
  sentences and three different ``reason`` values. A single "not a pane token"
  refusal would pass a test that only asserted the status code.
* **THE 401 THAT MATTERS AFTER A RESTART SAYS WHAT TO DO.** A pane token that no
  store knows answers with the plan's own sentence, because that is the state
  every user hits the first time they restart the app with a harness running.
* **THE BLOCKING CHECK IS OFF THE EVENT LOOP.** ``authorize_mcp`` reaches SQLite
  through ``PairingStore.verify``; on the loop that is the v1.153.1 outage the
  user reads as "Daemon offline". Pinned by having the verifier record the thread
  it ran on — no timing assertion anywhere in this file.
* **THE STDIO SHIM RELAYS, AND ONLY RELAYS.** It is driven with an injected
  poster (no socket), and the two things a relay gets wrong are pinned: a
  notification produces no output line, and an HTTP refusal arrives as a JSON-RPC
  error carrying the daemon's own sentence rather than as silence. Its two
  duplicated constants and its SSE parser are pinned against the real definitions
  they were copied from, since the shim must stay import-free to run under a
  harness's own Python.

The routes are registered on a BARE ``FastAPI`` app here, not through
``create_app``: ``daemon/app.py`` belonged to the coordinating session, which
wired ``_routes.mcpserver.register(app, d)`` after this landed. That is the same
documented workaround ``tests/test_helpdocs_v1198.py`` uses.

**AND THAT WORKAROUND HID THE ONE DEFECT THAT MATTERED.** A bare app has no
middleware, so nothing in this file could see that ``TokenAuthMiddleware``
refused a valid pane token *before* the route ran on every install with
``IRONJARVIS_TOKEN`` set — which is every packaged install. All 43 tests here
passed while ``/mcp`` was unreachable in the product. The fixture below even
sets ``IRONJARVIS_TOKEN``, which makes the authed case LOOK covered. It is not
covered here and cannot be: whatever this file proves about the route, the
question "can a credential reach it in the real app" is answered only in
``tests/test_mcp_auth_real_app_v1238.py``, which drives ``create_app``. Add a
route behaviour here; add an app-level one there.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.browser.panetokens import (
    INSTALL_BEARER_MESSAGE,
    MCP_TOKEN_ENV,
    MCP_URL_ENV,
    MISSING_TOKEN_MESSAGE,
    PAIRING_TOKEN_MESSAGE,
    PANE_TOKEN_EXPIRED_MESSAGE,
    REASON_INSTALL_BEARER,
    REASON_NO_TOKEN,
    REASON_PAIRING_TOKEN,
    REASON_UNKNOWN_TOKEN,
    PaneTokenStore,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.routes import mcpserver as mcp_routes
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcp.client import HttpTransport, MCPClient
from iron_jarvis.mcpserver import jsonrpc, stdio_shim
from iron_jarvis.mcpserver.server import (
    MCP_APPROVAL_TIMEOUT_S,
    MCP_ASK_DENIED_MESSAGE,
    MCP_ASK_TIMEOUT_MESSAGE,
    MCP_NO_ASK_SURFACE_MESSAGE,
    MCP_NOT_CONNECTED_LINE,
    MCP_SCOPE_LINE,
    MCP_SNAPSHOT_LINE,
    MCP_UNTRUSTED_LINE,
    NO_CAPABILITY_MESSAGE,
    NO_SESSION_MESSAGE,
    capability_refused_message,
    granted_capabilities,
    harness_session_id,
    permitted_tool_names,
    server_instructions,
)
from iron_jarvis.mcpserver.session import (
    MAX_SESSIONS_PER_PANE,
    SESSION_HEADER,
    McpSessionRegistry,
)

PANE_ID = "term_pane_one"
OTHER_PANE_ID = "term_pane_two"
INSTALL_TOKEN = "install-bearer-for-the-whole-app"


class FakePane:
    """A pane as :meth:`PaneTokenStore.resolve` reads one: id, cwd, capabilities, alive.

    Not a real ``TerminalSession``: that class does not carry ``capabilities``
    until the terminals lane lands the field, and a test that waited for it would
    be a test of another agent's file. ``resolve`` reads exactly these three
    attributes, so this is the whole contract.
    """

    def __init__(self, pane_id: str, capabilities: dict, cwd: str = "") -> None:
        self.id = pane_id
        self.capabilities = dict(capabilities)
        self.cwd = cwd
        self.alive = True


class FakeTerminals:
    """``TerminalManager.get`` and nothing else — the only method either side uses."""

    def __init__(self, panes: dict) -> None:
        self.panes = dict(panes)

    def get(self, pane_id: str):
        return self.panes.get(pane_id)


class Harness:
    """A bare app with ``/mcp`` on it, plus the pieces a test needs to drive it."""

    def __init__(self, platform, panes: dict, capabilities: dict) -> None:
        self.platform = platform
        self.panes = panes
        platform.terminals = FakeTerminals(panes)
        self.tokens = PaneTokenStore(pane_lookup=platform.terminals.get)
        self.sessions = McpSessionRegistry()
        platform.pane_tokens = self.tokens
        platform.mcp_sessions = self.sessions
        self.d = SimpleNamespace(platform=platform)
        self.app = FastAPI()
        mcp_routes.register(self.app, self.d)
        self.client = TestClient(self.app)
        self.token = self.tokens.mint(PANE_ID, capabilities)

    # -- raw HTTP ---------------------------------------------------------
    def post(self, payload: dict, *, token: str | None = None, session_id: str = "",
             accept: str = "application/json, text/event-stream"):
        headers = {"Accept": accept}
        candidate = self.token if token is None else token
        if candidate:
            headers["Authorization"] = f"Bearer {candidate}"
        if session_id:
            headers[SESSION_HEADER] = session_id
        return self.client.post("/mcp", json=payload, headers=headers)

    def initialize(self, *, token: str | None = None) -> str:
        """Handshake by hand; returns the session id off the RESPONSE HEADER."""
        response = self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": CLIENT_PROTOCOL_VERSION,
                           "clientInfo": {"name": "pytest", "version": "1"}},
            },
            token=token,
        )
        assert response.status_code == 200, response.text
        return response.headers.get(SESSION_HEADER, "")

    # -- the repository's own client --------------------------------------
    def mcp_client(self, *, token: str | None = None) -> MCPClient:
        """``mcp/client.py``'s real ``HttpTransport``, pointed at this server."""
        candidate = self.token if token is None else token
        transport = HttpTransport(
            "http://testserver/mcp",
            headers={"Authorization": f"Bearer {candidate}"},
            client_factory=lambda: self.client,
        )
        return MCPClient(transport, name="iron-jarvis")


@pytest.fixture()
def harness(platform, monkeypatch):
    """A pane holding Browser, a live browser package, and an install bearer set."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
    platform.config.browser_access = "interactive"
    panes = {
        PANE_ID: FakePane(PANE_ID, {"browser": True}, cwd=str(platform.config.home)),
        OTHER_PANE_ID: FakePane(OTHER_PANE_ID, {"browser": True}),
    }
    return Harness(platform, panes, {"browser": True})


@pytest.fixture()
def no_browser_harness(platform, monkeypatch):
    """A pane whose Capabilities record Files but NOT Browser."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
    platform.config.browser_access = "interactive"
    panes = {PANE_ID: FakePane(PANE_ID, {"files": True, "browser": False})}
    return Harness(platform, panes, {"files": True})


# --------------------------------------------------------------------------- #
# 1. The repository's own client drives the server, end to end, in-process.
# --------------------------------------------------------------------------- #
def test_the_repos_own_http_client_completes_the_handshake_and_lists_tools(harness):
    """``HttpTransport`` handshakes and lists — the strongest wire pin available.

    That transport captures ``mcp-session-id`` from the initialize RESPONSE
    HEADERS and sends it on every later request; a server that returned the id in
    the body only would 400 on this ``tools/list`` with "send initialize first".
    """
    client = harness.mcp_client()
    tools = _run(client.list_tools())
    names = sorted(tool["name"] for tool in tools)
    assert names, "the client's own handshake produced no tools"
    assert all(name.startswith("browser_") for name in names), names
    assert "browser_get_status" in names
    assert client.transport._session_id, (
        "the client captured no Mcp-Session-Id — the server must return it as a "
        "RESPONSE HEADER, which is the only place this client looks"
    )
    for tool in tools:
        assert tool.get("inputSchema"), f"{tool['name']} advertised no inputSchema"


def test_the_repos_own_client_calls_a_tool_and_reads_the_result(harness):
    """``tools/call`` through the same client: MCP ``content`` blocks and ``isError``."""
    client = harness.mcp_client()
    result = _run(client.call_tool("browser_get_status", {}))
    assert result.get("isError") is False, result
    blocks = result.get("content") or []
    assert blocks and blocks[0]["type"] == "text"
    assert "browser is not connected" in blocks[0]["text"].lower(), blocks


def _run(coro):
    """Run one coroutine to completion (the tests are sync; the client is async)."""
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 2. No second execution path: the SAME registry, the SAME allowed_names.
# --------------------------------------------------------------------------- #
def test_tools_call_goes_through_registry_invoke_with_the_listed_roster(harness):
    """The armed set at call time IS the advertised set (v1.227.0's roster gate).

    Spied at ``registry.invoke`` — the one seam chat, the agent runtime and the
    workflow engine all pass through — and the spy CALLS THROUGH, so the tool
    really runs. A route that executed the tool itself would record nothing here.
    """
    seen: list[dict] = []
    real_invoke = harness.platform.registry.invoke

    async def spy_invoke(*args, **kw):  # spies take (*args, **kw) — CLAUDE.md
        seen.append({"args": args, "kw": kw})
        return await real_invoke(*args, **kw)

    harness.platform.registry.invoke = spy_invoke
    client = harness.mcp_client()
    listed = {tool["name"] for tool in _run(client.list_tools())}
    result = _run(client.call_tool("browser_get_status", {}))
    assert result.get("isError") is False, result
    assert len(seen) == 1, f"tools/call did not reach registry.invoke exactly once: {seen}"
    call = seen[0]
    assert call["args"][0] == "browser_get_status"
    assert call["kw"]["allowed_names"] == listed, (
        "the call's roster gate must be the very set tools/list advertised; "
        f"listed={sorted(listed)} armed={sorted(call['kw']['allowed_names'])}"
    )
    assert call["kw"]["session_allow"] is None, (
        "an ALLOW-tier call carries no session grant at all: a grant exists only "
        "as the answer a human gave to an approval card, and browser_get_status "
        "never raises one. A blanket grant here would be the self-grant D17A "
        f"forbids. got={call['kw']['session_allow']!r}"
    )
    assert "deny_reason" not in call["kw"], call["kw"]


def test_an_mcp_call_is_ledgered_and_attributable_to_its_pane(harness):
    """The ledger row exists and names the harness (plan §10's D24 mapping).

    Read back out of the real database rather than from an event spy: the row is
    what ``/audit`` and the session export show the user, and "the call appears in
    the Jarvis audit view" is step 5 of this ship's live drive.
    """
    client = harness.mcp_client()
    _run(client.call_tool("browser_get_status", {}))
    with session_scope(harness.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)).all())
    mine = [r for r in rows if r.tool == "browser_get_status"]
    assert mine, "an MCP tool call wrote no ToolInvocation row"
    assert mine[-1].session_id == harness_session_id(PANE_ID), mine[-1].session_id
    assert mine[-1].ok is True
    # agent_run_id NAMES A RUN OR NAMES NOTHING (v1.238.0 review). It used to
    # carry the same ``mcp:<pane>`` string as the session id, so the indexed
    # column every agent view resolves to an ``AgentRun`` pointed at a row that
    # does not exist. Plan section 10 maps the harness onto session_id alone.
    assert not (mine[-1].agent_run_id or ""), (
        "an MCP call wrote an agent_run_id that names no AgentRun: "
        f"{mine[-1].agent_run_id!r}"
    )


# --------------------------------------------------------------------------- #
# 3. Capability filtering: discovery AND execution (D09A).
# --------------------------------------------------------------------------- #
def test_a_pane_without_browser_receives_no_browser_definition(no_browser_harness):
    """403 at DISCOVERY, and not one ``browser_*`` spec anywhere in the answer."""
    session_id = no_browser_harness.initialize()
    response = no_browser_harness.post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session_id=session_id,
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["detail"] == NO_CAPABILITY_MESSAGE
    assert "browser_" not in response.text, (
        "a refusal must not leak the roster it refused"
    )


def test_a_pane_without_browser_is_refused_at_call_time_too(no_browser_harness):
    """403 at EXECUTION. Either gate alone is the bug D09A names."""
    session_id = no_browser_harness.initialize()
    response = no_browser_harness.post(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "browser_get_status", "arguments": {}},
        },
        session_id=session_id,
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == NO_CAPABILITY_MESSAGE


def test_permitted_tool_names_is_fail_closed_on_the_grant_itself(no_browser_harness):
    """Gate 2 lives in the FILTER, not only in the route's 403 branch.

    ``dispatch`` refuses a grant with no enforced capability before it ever
    filters, so a filter that had quietly stopped reading the grant would still
    look correct from the outside. This pins the filter directly: it is the
    function ``tools/call`` derives ``allowed_names`` from, and a leak here would
    arm a roster the pane was never granted.
    """
    grant = no_browser_harness.tokens.resolve(no_browser_harness.token)
    assert grant is not None and grant.allows("browser") is False
    assert permitted_tool_names(no_browser_harness.d, grant) == []


def test_a_tool_outside_the_grant_is_refused_by_name(harness):
    """A pane WITH Browser still cannot reach a non-browser tool.

    ``read_file`` is registered and would run for a chat turn that armed it; over
    MCP it is outside every enforced capability family, so it is a 403 that names
    the tool rather than a tool result that reads as a broken tool.
    """
    assert harness.platform.registry.get("read_file") is not None, (
        "this pin needs a registered non-browser tool to be meaningful"
    )
    session_id = harness.initialize()
    response = harness.post(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "x"}},
        },
        session_id=session_id,
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == capability_refused_message("read_file")


def test_unticking_browser_takes_effect_on_the_next_call_without_a_new_token(harness):
    """The grant is read LIVE (D19): the user unticks Browser mid-run and the
    harness's very next call is refused, with the same token."""
    session_id = harness.initialize()
    first = harness.post(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
        session_id=session_id,
    )
    assert first.status_code == 200, first.text
    harness.panes[PANE_ID].capabilities["browser"] = False
    second = harness.post(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
        session_id=session_id,
    )
    assert second.status_code == 403, second.text
    assert second.json()["detail"] == NO_CAPABILITY_MESSAGE


def test_global_access_off_leaves_only_the_status_tool(harness):
    """Gate 1 is the chat lane's own filter, deviation included.

    ``browser_access = off`` strips every ``browser_*`` name except
    ``browser_get_status`` — the documented exception on
    ``chat_turn._BROWSER_STATUS_TOOL``. Asserted through the MCP path so the two
    lanes are proven to share one implementation rather than two that agree today.
    """
    harness.platform.config.browser_access = "off"
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}},
        session_id=session_id,
    )
    assert response.status_code == 200, response.text
    names = sorted(t["name"] for t in response.json()["result"]["tools"])
    assert names == ["browser_get_status"], names


def test_read_only_access_hides_the_acting_tools(harness):
    """``read_only`` admits the read tier only, read off each tool's ``min_access``."""
    harness.platform.config.browser_access = "read_only"
    names = permitted_tool_names(harness.d, harness.tokens.resolve(harness.token))
    assert "browser_read_page" in names
    assert "browser_click" not in names, names
    assert "browser_navigate" not in names, names


def test_granted_capabilities_ignores_the_four_this_ship_does_not_enforce(harness):
    """Files/Shell/Extensions/Memory grant nothing over MCP — and say so."""
    harness.panes[PANE_ID].capabilities = {"files": True, "shell": True, "browser": True}
    grant = harness.tokens.resolve(harness.token)
    assert granted_capabilities(grant) == ["browser"]
    assert MCP_SCOPE_LINE in server_instructions(harness.d, grant)


# --------------------------------------------------------------------------- #
# 4. Authentication: pane tokens ONLY (D17A, plan 12.3).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("attempt", ["header", "query"])
def test_no_token_is_401_with_its_own_sentence(harness, attempt):
    """The credential may ride a header or ``?token=``; absent, it is 401."""
    if attempt == "header":
        response = harness.post({"jsonrpc": "2.0", "id": 8, "method": "ping"}, token="")
    else:
        response = harness.client.post("/mcp?token=", json={"jsonrpc": "2.0", "id": 8,
                                                            "method": "ping"})
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == MISSING_TOKEN_MESSAGE
    assert body["reason"] == REASON_NO_TOKEN


def test_the_install_bearer_is_refused_by_its_own_check(harness):
    """The token every OTHER route in the app requires is refused here (plan 12.3)."""
    response = harness.post({"jsonrpc": "2.0", "id": 9, "method": "ping"},
                            token=INSTALL_TOKEN)
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == INSTALL_BEARER_MESSAGE
    assert body["reason"] == REASON_INSTALL_BEARER


def test_a_real_browser_pairing_token_is_refused_by_its_own_check(harness):
    """A REAL token from the real ``PairingStore`` — not a string that looks like one.

    ``/browser/ws`` accepts exactly this credential; ``/mcp`` must not, and the
    refusal must be the PAIRING one rather than the generic unknown-token 401, or
    the cross-rejection would be indistinguishable from a typo.
    """
    pairing = harness.platform.browser.pairing
    assert pairing is not None, "the platform built no pairing store"
    request = pairing.open_request(extension_id="abc")
    pairing_token = pairing.mint(request.request_id)
    assert pairing.verify(pairing_token) is not None, "the token is not live"
    response = harness.post({"jsonrpc": "2.0", "id": 10, "method": "ping"},
                            token=pairing_token)
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == PAIRING_TOKEN_MESSAGE
    assert body["reason"] == REASON_PAIRING_TOKEN


def test_an_unknown_pane_token_says_what_to_do_after_a_restart(harness):
    """The restart sentence, verbatim from plan 12.2.

    Tokens are memory-only, so this is the state EVERY user reaches the first
    time they restart Iron Jarvis with a harness still running.
    """
    response = harness.post({"jsonrpc": "2.0", "id": 11, "method": "ping"},
                            token="a-token-no-store-has-ever-seen")
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == PANE_TOKEN_EXPIRED_MESSAGE
    assert body["reason"] == REASON_UNKNOWN_TOKEN


def test_a_revoked_pane_token_stops_working_immediately(harness):
    """Closing the pane closes the door (D19), through the store's own revocation."""
    harness.initialize()
    harness.tokens.revoke_pane(PANE_ID)
    response = harness.post({"jsonrpc": "2.0", "id": 12, "method": "ping"})
    assert response.status_code == 401, response.text
    assert response.json()["reason"] == REASON_UNKNOWN_TOKEN


def test_the_pairing_check_runs_off_the_event_loop(harness, monkeypatch):
    """``authorize_mcp`` reaches SQLite; the route must hop threads (v1.153.1).

    No timing assertion, and no assumption about WHICH thread is the loop: a
    ``TestClient`` runs the app on a worker thread of its own, so comparing
    against ``threading.main_thread()`` would pass whether or not the hop
    happened. Instead ``dispatch`` — which really does run on the loop — records
    the loop's thread, and the verifier must have run on a different one.
    """
    verify_threads: list[int] = []
    loop_threads: list[int] = []
    real_verify = harness.platform.browser.pairing.verify
    real_dispatch = mcp_routes.dispatch

    def spy_verify(*args, **kw):  # spies take (*args, **kw)
        verify_threads.append(threading.get_ident())
        return real_verify(*args, **kw)

    async def spy_dispatch(*args, **kw):
        loop_threads.append(threading.get_ident())
        return await real_dispatch(*args, **kw)

    harness.platform.browser.pairing.verify = spy_verify
    monkeypatch.setattr(mcp_routes, "dispatch", spy_dispatch)
    harness.post({"jsonrpc": "2.0", "id": 13, "method": "ping"})
    assert verify_threads, "the pairing cross-check never ran"
    assert loop_threads, "the request never reached dispatch"
    assert verify_threads[0] != loop_threads[0], (
        "the blocking pairing check ran on the event loop's own thread"
    )


# --------------------------------------------------------------------------- #
# 5. Sessions.
# --------------------------------------------------------------------------- #
def test_initialize_returns_the_session_id_as_a_response_header(harness):
    """The header, not just the body — the one place the client looks."""
    response = harness.post(
        {"jsonrpc": "2.0", "id": 14, "method": "initialize",
         "params": {"protocolVersion": CLIENT_PROTOCOL_VERSION}},
    )
    assert response.status_code == 200, response.text
    assert response.headers.get(SESSION_HEADER), response.headers
    result = response.json()["result"]
    assert result["protocolVersion"] == CLIENT_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "iron-jarvis"


def test_the_initialized_notification_gets_no_body(harness):
    """A notification carries no id and MUST get no response, or the handshake breaks."""
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        session_id=session_id,
    )
    assert response.status_code == 202, response.text
    assert response.content in (b"", None), response.content
    assert harness.sessions.get(session_id, pane_id=PANE_ID).initialized is True


def test_a_call_before_initialize_says_so(harness):
    """No session id, no work — with a sentence naming the fix."""
    response = harness.post({"jsonrpc": "2.0", "id": 15, "method": "tools/list",
                             "params": {}})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == NO_SESSION_MESSAGE


def test_a_session_id_is_not_transferable_between_panes(harness):
    """Pane A's session id, presented with pane B's token, resolves to nothing.

    Otherwise a harness could continue another pane's conversation by guessing an
    id, and the capability filter would be computed from the wrong pane.
    """
    session_id = harness.initialize()
    other_token = harness.tokens.mint(OTHER_PANE_ID, {"browser": True})
    response = harness.post(
        {"jsonrpc": "2.0", "id": 16, "method": "tools/list", "params": {}},
        token=other_token,
        session_id=session_id,
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == NO_SESSION_MESSAGE


def test_delete_closes_the_session_and_an_unknown_id_is_not_an_error(harness):
    session_id = harness.initialize()
    response = harness.client.request(
        "DELETE", "/mcp",
        headers={"Authorization": f"Bearer {harness.token}", SESSION_HEADER: session_id},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"closed": True}
    again = harness.client.request(
        "DELETE", "/mcp",
        headers={"Authorization": f"Bearer {harness.token}", SESSION_HEADER: session_id},
    )
    assert again.json() == {"closed": False}


def test_get_authenticates_before_it_refuses_the_verb(harness):
    """405 for a caller who holds a pane token; 401 for one who does not.

    A 405 in front of the credential check would tell an unauthorised prober that
    ``/mcp`` exists and what it is.
    """
    unauthorised = harness.client.get("/mcp")
    assert unauthorised.status_code == 401, unauthorised.text
    authorised = harness.client.get(
        "/mcp", headers={"Authorization": f"Bearer {harness.token}"}
    )
    assert authorised.status_code == 405, authorised.text


# --------------------------------------------------------------------------- #
# 6. Protocol shape.
# --------------------------------------------------------------------------- #
def test_an_unknown_method_is_a_protocol_error_naming_what_is_supported(harness):
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "id": 17, "method": "resources/list", "params": {}},
        session_id=session_id,
    )
    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == jsonrpc.METHOD_NOT_FOUND
    assert "tools/call" in error["data"]["supported"]


def test_a_client_that_asks_only_for_sse_gets_an_sse_body_the_client_can_parse(harness):
    """The other Streamable-HTTP body shape, parsed by the real client's own parser."""
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "id": 18, "method": "ping"},
        session_id=session_id,
        accept="text/event-stream",
    )
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    payload = HttpTransport._parse_body(response)
    assert payload["id"] == 18 and payload["result"] == {}


def test_malformed_json_is_a_parse_error_not_a_500(harness):
    response = harness.client.post(
        "/mcp",
        content=b"{not json",
        headers={"Authorization": f"Bearer {harness.token}",
                 "Content-Type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == jsonrpc.PARSE_ERROR


def test_the_route_refuses_honestly_when_the_store_is_not_wired(monkeypatch):
    """No ``pane_tokens`` at all: 503, never a 500 and never a 401.

    The distinction is the point. A 401 sends the user to check a credential that
    was never the problem; a 500 tells them nothing. An install where the store
    failed to build must say it is not ready.

    UPDATED AT v1.238.0: this used to pass the REAL platform and rely on it not
    having been wired yet. The coordinator wired it, so the premise evaporated and
    the test started asserting the opposite of the truth. It now builds a platform
    stand-in that genuinely lacks the attribute, which is what the test was always
    about — and, unlike the original, it cannot quietly stop testing anything the
    day the wiring lands.
    """
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
    app = FastAPI()
    unwired = SimpleNamespace()  # no pane_tokens, no mcp_sessions, no browser
    assert not hasattr(unwired, "pane_tokens"), "the stand-in must really lack it"
    mcp_routes.register(app, SimpleNamespace(platform=unwired))
    client = TestClient(app)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 19, "method": "ping"},
                           headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == mcp_routes.NOT_READY_MESSAGE


def test_the_real_platform_wires_the_store_so_that_refusal_is_unreachable(platform):
    """The other half, and the reason the test above needed a stand-in.

    ``platform.py`` must expose ``pane_tokens`` — and it must be the SAME object the
    TerminalManager holds, because the manager is what revokes on kill, purge_dead
    and kill_all. A second store would leave ``/mcp`` authorising tokens that
    nothing revokes: the pane closes, the harness keeps working.
    """
    assert getattr(platform, "pane_tokens", None) is not None, (
        "platform.py does not wire pane_tokens, so /mcp answers 503 on every call "
        "and the whole outward-harness path is dead"
    )
    assert platform.pane_tokens is platform.terminals.pane_tokens, (
        "the platform built its OWN token store instead of exposing the manager's. "
        "The manager revokes at pane close; a second store is a set of live "
        "credentials nothing revokes"
    )
    assert getattr(platform, "mcp_sessions", None) is not None


# --------------------------------------------------------------------------- #
# 7. Server instructions — D21's equivalent for a harness (plan 12.1).
# --------------------------------------------------------------------------- #
def test_instructions_name_the_pane_the_state_and_the_untrusted_rule(harness):
    """An external harness never sees the chat lane's ambient block, so the same
    facts must ride the one channel MCP gives a server."""
    grant = harness.tokens.resolve(harness.token)
    text = server_instructions(harness.d, grant)
    assert PANE_ID in text
    assert MCP_UNTRUSTED_LINE in text
    assert MCP_SCOPE_LINE in text
    assert MCP_NOT_CONNECTED_LINE in text, (
        "a disconnected browser must be stated, not left as silence"
    )
    assert MCP_SNAPSHOT_LINE in text, (
        "instructions are rendered once and can never be re-sent; the block has "
        "to say so or the model reads a stale capability line as current"
    )


def test_instructions_render_the_chat_lanes_own_browser_block_when_connected(harness):
    """ONE renderer for the browser's state: ``chat_turn._browser_section``.

    Driven with a runtime that reports connected and a cached tab, so the block's
    real heading and its real title line have to appear — a second, MCP-only
    renderer would pass a "mentions the tab" assertion and fail this one.
    """
    from iron_jarvis.daemon.chat_turn import BROWSER_HEADING, BROWSER_UNTRUSTED_LINE

    class _Connected:
        connected = True
        backend = SimpleNamespace(
            active_tab={"title": "Quarterly filing", "url": "https://example.test/q3"}
        )

        def access(self) -> str:
            return "interactive"

    harness.platform.browser = _Connected()
    text = server_instructions(harness.d, harness.tokens.resolve(harness.token))
    assert BROWSER_HEADING in text
    assert "Active tab: Quarterly filing" in text
    assert BROWSER_UNTRUSTED_LINE in text, "the chat lane's own fence rides with it"
    assert MCP_UNTRUSTED_LINE in text


def test_instructions_survive_a_browser_runtime_that_raises(harness):
    """The handshake must not fail over an ambient nicety."""

    class _Exploding:
        @property
        def connected(self):
            raise RuntimeError("boom")

    harness.platform.browser = _Exploding()
    text = server_instructions(harness.d, harness.tokens.resolve(harness.token))
    assert MCP_SCOPE_LINE in text


def test_the_initialize_result_carries_the_instructions(harness):
    """Rendered where a harness actually reads them, not only by direct call."""
    response = harness.post(
        {"jsonrpc": "2.0", "id": 20, "method": "initialize", "params": {}},
    )
    assert MCP_UNTRUSTED_LINE in response.json()["result"]["instructions"]


# --------------------------------------------------------------------------- #
# 8. The stdio shim (D17/D18).
# --------------------------------------------------------------------------- #
class FakePoster:
    """An injected transport: canned replies, every request recorded."""

    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def post(self, url, body, headers, timeout):  # takes what the shim sends
        self.calls.append({"url": url, "body": json.loads(body.decode("utf-8")),
                           "headers": dict(headers), "timeout": timeout})
        return self.replies.pop(0) if self.replies else stdio_shim.HttpReply(
            status=200, text="", headers={}
        )


def test_the_shim_relays_a_call_faithfully_and_carries_the_pane_token():
    """One line in, the same JSON-RPC out, with the token on the wire."""
    reply = stdio_shim.HttpReply(
        status=200,
        text=json.dumps({"jsonrpc": "2.0", "id": 3,
                         "result": {"content": [{"type": "text", "text": "ok"}],
                                    "isError": False}}),
        headers={"content-type": "application/json"},
    )
    poster = FakePoster([reply])
    shim = stdio_shim.StdioShim("http://127.0.0.1:8787/mcp", "pane-token", poster=poster)
    request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "browser_get_status", "arguments": {}}}
    line = shim.handle_line(json.dumps(request))
    assert poster.calls[0]["body"] == request, "the request was not relayed verbatim"
    assert poster.calls[0]["headers"]["Authorization"] == "Bearer pane-token"
    assert "text/event-stream" in poster.calls[0]["headers"]["Accept"]
    assert json.loads(line)["result"]["content"][0]["text"] == "ok"


def test_the_shim_writes_nothing_for_a_notification():
    """``notifications/initialized`` carries no id; a reply breaks the handshake."""
    poster = FakePoster([stdio_shim.HttpReply(status=202, text="", headers={})])
    shim = stdio_shim.StdioShim("http://x/mcp", "t", poster=poster)
    assert shim.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None
    assert poster.calls, "the notification must still be delivered upstream"


def test_the_shim_captures_the_session_id_and_returns_it_on_the_next_request():
    """Without this the daemon answers "send initialize first" to a harness that did."""
    poster = FakePoster([
        stdio_shim.HttpReply(status=200,
                             text=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
                             headers={"content-type": "application/json",
                                      "mcp-session-id": "sess-42"}),
        stdio_shim.HttpReply(status=200,
                             text=json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}}),
                             headers={"content-type": "application/json"}),
    ])
    shim = stdio_shim.StdioShim("http://x/mcp", "t", poster=poster)
    shim.handle_line('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
    shim.handle_line('{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')
    assert poster.calls[1]["headers"][stdio_shim.SESSION_HEADER] == "sess-42"


def test_the_shim_turns_a_401_body_into_a_json_rpc_error_the_user_can_act_on():
    """A refusal must arrive as words, not as a dead pipe."""
    poster = FakePoster([
        stdio_shim.HttpReply(status=401,
                             text=json.dumps({"detail": PANE_TOKEN_EXPIRED_MESSAGE,
                                              "reason": REASON_UNKNOWN_TOKEN}),
                             headers={"content-type": "application/json"}),
    ])
    shim = stdio_shim.StdioShim("http://x/mcp", "stale", poster=poster)
    line = shim.handle_line('{"jsonrpc":"2.0","id":7,"method":"tools/list","params":{}}')
    body = json.loads(line)
    assert body["id"] == 7
    assert PANE_TOKEN_EXPIRED_MESSAGE in body["error"]["message"]
    assert "401" in body["error"]["message"]


def test_the_shim_reports_an_unreachable_daemon_instead_of_hanging():
    poster = FakePoster([stdio_shim.HttpReply(status=0, text="URLError: refused",
                                              headers={})])
    shim = stdio_shim.StdioShim("http://127.0.0.1:8787/mcp", "t", poster=poster)
    body = json.loads(shim.handle_line('{"jsonrpc":"2.0","id":9,"method":"ping"}'))
    assert "cannot reach Iron Jarvis" in body["error"]["message"]


def test_the_shim_runs_a_whole_stream_and_skips_only_the_notification():
    """The pump: two requests and one notification produce exactly two output lines."""
    import io

    poster = FakePoster([
        stdio_shim.HttpReply(status=200,
                             text=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
                             headers={"content-type": "application/json",
                                      "mcp-session-id": "s1"}),
        stdio_shim.HttpReply(status=202, text="", headers={}),
        stdio_shim.HttpReply(status=200,
                             text=json.dumps({"jsonrpc": "2.0", "id": 2,
                                              "result": {"tools": []}}),
                             headers={"content-type": "application/json"}),
    ])
    shim = stdio_shim.StdioShim("http://x/mcp", "t", poster=poster)
    stdin = io.StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n'
    )
    stdout = io.StringIO()
    assert shim.run(stdin, stdout) == 0
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert [json.loads(line)["id"] for line in lines] == [1, 2]


def test_the_shim_refuses_to_start_without_a_token_and_says_where_it_comes_from():
    import io

    err = io.StringIO()
    code = stdio_shim.main(["shim"], env={MCP_URL_ENV: "http://x/mcp"}, stderr=err)
    assert code == 2
    assert MCP_TOKEN_ENV in err.getvalue()


def test_the_shims_env_names_match_the_store_that_mints_the_token():
    """The shim is import-free by design, so its copies are pinned here.

    A drift would mean a recipe setting one name and a shim reading another —
    which presents as "the harness has no Jarvis tools" with nothing in any log.
    """
    assert stdio_shim.MCP_URL_ENV == MCP_URL_ENV
    assert stdio_shim.MCP_TOKEN_ENV == MCP_TOKEN_ENV
    assert stdio_shim.SESSION_HEADER == SESSION_HEADER


def test_the_shims_sse_parser_agrees_with_the_repositorys_own_client():
    """Same body, same payload — the other half of the import-free duplication.

    Driven through ``jsonrpc.sse_body`` (what the server really writes) and
    ``HttpTransport._parse_body`` (what the client really reads).
    """
    payload = {"jsonrpc": "2.0", "id": 5, "result": {"tools": [{"name": "browser_click"}]}}
    body = jsonrpc.sse_body(payload)
    response = SimpleNamespace(headers={"content-type": "text/event-stream"}, text=body)
    assert HttpTransport._parse_body(response) == payload
    assert stdio_shim.payload_from_body("text/event-stream", body) == payload


# --------------------------------------------------------------------------- #
# 9. THE ASK TIER (v1.238.0 review, S2).
#
# ``_call_tool`` used to pass no ``session_allow`` and ask nobody, so the eight
# ask-tier acting tools were advertised by ``tools/list`` and could never once
# succeed: every call came back with the headless resolver's sentence, whose
# remedies are a chat-lane concept a harness cannot express and an install-wide
# switch that also removes the gate for chat. Six of fourteen advertised tools
# worked. The plan's own answer (11.2) is the approval card, so that is what an
# MCP ask now does - through the SAME ``platform.approvals`` registry and the
# SAME ``POST /chat/approvals/{id}`` answering route as chat and the agent
# runtime.
# --------------------------------------------------------------------------- #
ASK_TOOL = "browser_click"
ASK_ARGS = {"ref": "e12"}


def _answer_when_asked(approvals, decision: str, *, seen: list) -> "threading.Thread":
    """Answer the FIRST approval this registry files, from another thread.

    A ``TestClient`` request blocks the calling thread while the app waits on
    the card, which is the whole point - so the user's answer has to arrive from
    somewhere else, exactly as it does in life. ``ChatApprovals.resolve``
    marshals a foreign-thread answer onto the future's own loop (v1.209.0), and
    this test is that path's second caller.
    """
    stop = threading.Event()

    def _run() -> None:
        while not stop.wait(0.01):
            for approval_id in list(approvals.pending_ids()):
                if approvals.resolve(approval_id, decision):
                    seen.append(approval_id)
                    return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.stop = stop  # type: ignore[attr-defined]
    return thread


def _call_ask_tool(harness, *, request_id: int = 90):
    session_id = harness.initialize()
    return harness.post(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": ASK_TOOL, "arguments": dict(ASK_ARGS)},
        },
        session_id=session_id,
    )


def test_an_ask_tier_tool_asks_the_user_instead_of_refusing_itself(harness):
    """The card is filed, and an approved call is AUTHORISED - not refused.

    Two things are pinned and both are behaviour: an approval request really
    reaches ``platform.approvals`` naming this tool, and the ``invoke`` that
    follows carries the human's answer as ``session_allow`` with NO
    ``deny_reason``, so the permission engine authorises it. The tool then fails
    for its own honest reason (no browser is connected in a test), which is a
    different sentence from a permission refusal - and that difference is the
    assertion, because "permission denied" was the ONLY answer this path could
    give before.
    """
    seen: list[str] = []
    thread = _answer_when_asked(harness.platform.approvals, "once", seen=seen)
    invokes: list[dict] = []
    real_invoke = harness.platform.registry.invoke

    async def spy_invoke(*args, **kw):  # spies take (*args, **kw)
        invokes.append({"args": args, "kw": kw})
        return await real_invoke(*args, **kw)

    harness.platform.registry.invoke = spy_invoke
    try:
        response = _call_ask_tool(harness)
    finally:
        thread.stop.set()  # type: ignore[attr-defined]
    assert response.status_code == 200, response.text
    assert seen, "the ask-tier call never filed an approval for anyone to answer"
    assert invokes, "the call never reached the registry"
    grant = invokes[-1]["kw"]["session_allow"] or set()
    assert ASK_TOOL in grant, (
        "the user's answer must reach invoke as a session grant, or the engine "
        f"refuses the call they just approved. got={grant!r}"
    )
    assert "deny_reason" not in invokes[-1]["kw"], invokes[-1]["kw"]
    # The engine's own verdict on exactly what was passed - not a shape check.
    decision = harness.platform.permissions.authorize(
        ASK_TOOL, dict(ASK_ARGS), None, session_allow=grant
    )
    assert decision.allowed is True, decision.reason
    text = response.json()["result"]["content"][0]["text"]
    assert "permission denied" not in text.lower(), (
        "an approved call must not come back as a permission refusal: " + text
    )


def test_the_approval_card_carries_redacted_arguments_and_names_the_pane(harness):
    """What the user is shown, and what the bell can find it by.

    The registry keeps the args as display metadata and the event row persists
    them, so they must be the tool's own redaction; and the request is filed
    under the harness session id so ``pending_for`` can attribute the ask to the
    pane that is waiting.
    """
    filed: list[dict] = []
    real_request = harness.platform.approvals.request

    def spy_request(*args, **kw):  # spies take (*args, **kw)
        approval_id, fut = real_request(*args, **kw)
        filed.append({"args": args, "kw": kw, "id": approval_id})
        return approval_id, fut

    harness.platform.approvals.request = spy_request
    seen: list[str] = []
    thread = _answer_when_asked(harness.platform.approvals, "deny", seen=seen)
    try:
        _call_ask_tool(harness, request_id=91)
    finally:
        thread.stop.set()  # type: ignore[attr-defined]
    assert filed, "no approval was filed"
    call = filed[0]
    assert call["args"][0] == ASK_TOOL
    expected = harness.platform.registry.get(ASK_TOOL).redact_args(dict(ASK_ARGS))
    assert call["args"][1] == expected, (
        "the card must show the tool's OWN redaction of the arguments"
    )
    assert call["kw"].get("session_id") == harness_session_id(PANE_ID), call["kw"]


def test_the_ask_is_announced_as_an_event_so_the_bell_can_render_it(harness):
    """An MCP ask has no surface watching it, so it MUST ride the event bus.

    ``GET /chat/approvals/pending`` - what the NotificationBell polls - lists
    only asks with a matching ``approval.requested`` row, and the comm lane
    relays the same event to the phone. A card nobody can find is a call that
    times out.
    """
    from iron_jarvis.core.events import EventType

    events: list[tuple] = []
    real_publish = harness.platform.event_bus.publish

    async def spy_publish(*args, **kw):  # spies take (*args, **kw)
        events.append((args, kw))
        return await real_publish(*args, **kw)

    harness.platform.event_bus.publish = spy_publish
    thread = _answer_when_asked(harness.platform.approvals, "once", seen=[])
    try:
        _call_ask_tool(harness, request_id=92)
    finally:
        thread.stop.set()  # type: ignore[attr-defined]
    requested = [
        (a, k) for a, k in events
        if a and a[0] == EventType.APPROVAL_REQUESTED
    ]
    assert requested, (
        "the ask was never published as approval.requested, so the bell's "
        "pending list (which filters on exactly that row) cannot show it"
    )
    payload = requested[0][0][1]
    assert payload["tool"] == ASK_TOOL
    assert payload["approval_id"].startswith("apr_")
    assert requested[0][1].get("session_id") == harness_session_id(PANE_ID)
    resolved = [a for a, _ in events if a and a[0] == EventType.APPROVAL_RESOLVED]
    assert resolved, "the answer was never published as approval.resolved"


def test_a_denied_ask_is_refused_in_the_users_name_and_is_ledgered(harness):
    """Deny means: nothing ran, the model is told WHO refused, and the row exists.

    The refusal still goes through ``invoke`` with ``deny_reason=`` rather than
    returning early, because a decision a human made must reach the ledger the
    audit view reads - the v1.155.0 seam, used here exactly as chat uses it.
    """
    thread = _answer_when_asked(harness.platform.approvals, "deny", seen=[])
    try:
        response = _call_ask_tool(harness, request_id=93)
    finally:
        thread.stop.set()  # type: ignore[attr-defined]
    body = response.json()["result"]
    assert body["isError"] is True
    assert MCP_ASK_DENIED_MESSAGE in body["content"][0]["text"], body
    with session_scope(harness.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == ASK_TOOL]
    assert rows, "a refusal the user made was not ledgered"
    assert rows[-1].ok is False


def test_nobody_answering_is_an_honest_timeout_never_a_run(harness, monkeypatch):
    """No answer, no run - and a sentence that names the MCP remedy.

    The timeout is shortened here rather than waited out; the number itself is
    pinned by ``test_the_ask_timeout_fits_inside_the_shims_own_ceiling``.
    """
    import iron_jarvis.mcpserver.server as server_mod

    monkeypatch.setattr(server_mod, "MCP_APPROVAL_TIMEOUT_S", 0.05)
    ran: list[str] = []
    real_execute = harness.platform.registry.get(ASK_TOOL).execute

    async def spy_execute(*args, **kw):  # spies take (*args, **kw)
        ran.append(ASK_TOOL)
        return await real_execute(*args, **kw)

    harness.platform.registry.get(ASK_TOOL).execute = spy_execute
    response = _call_ask_tool(harness, request_id=94)
    body = response.json()["result"]
    assert body["isError"] is True
    assert MCP_ASK_TIMEOUT_MESSAGE in body["content"][0]["text"], body
    assert not ran, "an unanswered ask ran the tool anyway"


def test_the_ask_timeout_fits_inside_the_shims_own_round_trip_ceiling():
    """A pause longer than the client's ceiling is a card nobody can answer.

    The stdio shim bounds ONE round trip at ``DEFAULT_TIMEOUT_S``; an MCP ask
    blocks inside that trip. If the pause outlives it the harness reports
    "cannot reach Iron Jarvis" while the card is still on the user's screen, and
    their answer then resolves a request that has already gone. A RATIO, not a
    wall clock: no test here asserts how long anything takes.
    """
    assert MCP_APPROVAL_TIMEOUT_S < stdio_shim.DEFAULT_TIMEOUT_S, (
        f"an ask may pause {MCP_APPROVAL_TIMEOUT_S}s but the shim gives up at "
        f"{stdio_shim.DEFAULT_TIMEOUT_S}s"
    )


def test_with_no_approval_surface_the_refusal_names_something_a_harness_can_do(harness):
    """A platform with no approvals registry still fails closed - honestly.

    The engine's own sentence names ``allow_tools`` (a chat-lane concept an MCP
    harness cannot express) and the Settings switch (which also removes the gate
    for chat and every agent run). Neither is a remedy here, so neither is
    offered.
    """
    harness.platform.approvals = None
    response = _call_ask_tool(harness, request_id=95)
    body = response.json()["result"]
    assert body["isError"] is True
    text = body["content"][0]["text"]
    assert MCP_NO_ASK_SURFACE_MESSAGE in text, text
    assert "allow_tools" not in text, (
        "the chat lane's remedy was handed to a harness that cannot express it"
    )


def test_an_allow_tier_tool_never_files_a_card(harness):
    """The pause is for the ask tier only: a read tool must not stop to ask."""
    filed: list = []
    real_request = harness.platform.approvals.request

    def spy_request(*args, **kw):  # spies take (*args, **kw)
        filed.append(args)
        return real_request(*args, **kw)

    harness.platform.approvals.request = spy_request
    client = harness.mcp_client()
    result = _run(client.call_tool("browser_get_status", {}))
    assert result.get("isError") is False, result
    assert not filed, "an allow-tier tool asked for approval"


def test_the_eight_acting_tools_are_all_on_the_ask_tier_this_covers(harness):
    """The set this mechanism has to cover, named rather than assumed.

    If a future tool arrives on the ask tier, or one of these is quietly
    downgraded, that is a change to what an MCP harness can do without a human -
    and it should be a failing test, not a surprise.
    """
    from iron_jarvis.tools.permissions import PermissionMode

    listed = permitted_tool_names(harness.d, harness.tokens.resolve(harness.token))
    asking = sorted(
        name for name in listed
        if harness.platform.permissions.mode_for(
            harness.platform.registry.get(name).perm_key()
        ) is PermissionMode.ASK
    )
    assert asking == [
        "browser_activate_tab",
        "browser_click",
        "browser_close_tab",
        "browser_create_tab",
        "browser_navigate",
        "browser_press_key",
        "browser_scroll",
        "browser_type",
    ], asking


# --------------------------------------------------------------------------- #
# 10. NO ID, NO RESPONSE (v1.238.0 review, S2).
# --------------------------------------------------------------------------- #
def test_any_id_less_message_gets_202_and_no_body_whatever_its_method(harness):
    """``notifications/cancelled`` is what real clients send, routinely.

    Only the literal ``notifications/initialized`` used to be treated as a
    notification, so every other id-less message came back with a body carrying
    ``id: null`` - an unsolicited response a strict client cannot correlate, and
    the reason a harness reads this server as "keeps disconnecting".
    """
    session_id = harness.initialize()
    for method in ("notifications/cancelled", "notifications/progress", "ping"):
        response = harness.post(
            {"jsonrpc": "2.0", "method": method, "params": {}},
            session_id=session_id,
        )
        assert response.status_code == 202, (method, response.status_code, response.text)
        assert response.content in (b"", None), (method, response.content)


def test_an_id_less_tools_call_runs_nothing_at_all(harness):
    """It used to EXECUTE THE TOOL and answer with ``id: null``.

    Pinned on the ledger, not on the body: "no response" is easy to fake by
    dropping the payload after the work is done, and the work is the hazard.
    """
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "method": "tools/call",
         "params": {"name": "browser_get_status", "arguments": {}}},
        session_id=session_id,
    )
    assert response.status_code == 202, response.text
    assert response.content in (b"", None), response.content
    with session_scope(harness.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)).all())
    assert not [r for r in rows if r.tool == "browser_get_status"], (
        "an id-less tools/call executed the tool"
    )


def test_an_id_less_initialize_mints_no_session(harness):
    """Every id-less initialize used to burn one of the pane's session slots."""
    response = harness.post(
        {"jsonrpc": "2.0", "method": "initialize", "params": {}},
    )
    assert response.status_code == 202, response.text
    assert not response.headers.get(SESSION_HEADER), response.headers
    assert len(harness.sessions) == 0, (
        "an id-less initialize minted a session, burning one of the pane's "
        f"{MAX_SESSIONS_PER_PANE} slots for a message that expects no answer"
    )


def test_the_initialized_notification_still_marks_the_session(harness):
    """The one notification with a side effect keeps it (the handshake needs it)."""
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        session_id=session_id,
    )
    assert response.status_code == 202, response.text
    assert harness.sessions.get(session_id, pane_id=PANE_ID).initialized is True


# --------------------------------------------------------------------------- #
# 11. Malformed input: each shape, its own JSON-RPC error - never a 500.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload, code, status",
    [
        ([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], jsonrpc.INVALID_REQUEST, 400),
        ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, jsonrpc.INVALID_REQUEST, 400),
        ({"jsonrpc": "2.0", "id": 1}, jsonrpc.INVALID_REQUEST, 400),
        ({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1, 2]},
         jsonrpc.INVALID_PARAMS, 400),
        ("just a string", jsonrpc.INVALID_REQUEST, 400),
    ],
)
def test_every_malformed_shape_is_its_own_json_rpc_error(harness, payload, code, status):
    """A batch, a wrong version, no method, positional params, a bare string.

    Each is a DIFFERENT JSON-RPC error and every one is an envelope - never a
    traceback, never a 500, and never a half-processed batch.
    """
    response = harness.post(payload)  # type: ignore[arg-type]
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert "Traceback" not in response.text


def test_a_malformed_request_is_answered_with_the_id_it_carried(harness):
    """The client has to be able to correlate the refusal with its own call.

    JSON-RPC 2.0 requires a null id only when the id could not be DETECTED; here
    it was read fine and the message was rejected for something else, so echoing
    null makes the harness wait out its own timeout on a question already
    answered.
    """
    response = harness.post(
        {"jsonrpc": "2.0", "id": 77, "method": "tools/list", "params": "nope"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["id"] == 77, response.json()


def test_an_unreadable_id_is_answered_with_null(harness):
    """The converse: an id the spec does not allow is dropped, not echoed."""
    response = harness.post(
        {"jsonrpc": "2.0", "id": {"not": "scalar"}, "method": "ping", "params": 5},
    )
    assert response.status_code == 400, response.text
    assert response.json()["id"] is None, response.json()


def test_a_tool_that_explodes_is_a_json_rpc_error_not_a_500_page(harness):
    """A crash inside the registry reaches the harness as words it can relay."""

    async def boom(*args, **kw):  # spies take (*args, **kw)
        raise RuntimeError("the registry fell over")

    harness.platform.registry.invoke = boom
    session_id = harness.initialize()
    response = harness.post(
        {"jsonrpc": "2.0", "id": 78, "method": "tools/call",
         "params": {"name": "browser_get_status", "arguments": {}}},
        session_id=session_id,
    )
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["id"] == 78
    assert "RuntimeError: the registry fell over" in body["error"]["message"]
    assert "Traceback" not in response.text


# --------------------------------------------------------------------------- #
# 12. The instructions are a SNAPSHOT, and the untrusted rule is unconditional.
# --------------------------------------------------------------------------- #
def test_the_untrusted_rule_reaches_a_pane_that_has_not_ticked_browser_yet(
    no_browser_harness,
):
    """The ticking case, which is the one the plan supports and nothing pinned.

    Instructions are rendered ONCE, at initialize; the capability filter is
    evaluated live. A pane that gains Browser after the handshake serves all
    fourteen tools to a harness whose block was rendered when it had none - so
    the untrusted-page-content rule has to be in that block already, or the
    model reads whole attacker-controlled pages having never been told the rule.
    There is no way to re-send instructions: tools/list has no such field and
    this server opens no server->client stream.
    """
    grant = no_browser_harness.tokens.resolve(no_browser_harness.token)
    assert granted_capabilities(grant) == [], "this pin needs a pane WITHOUT Browser"
    text = server_instructions(no_browser_harness.d, grant)
    assert MCP_UNTRUSTED_LINE in text, (
        "a pane can gain Browser mid-session and the instructions can never be "
        "re-sent, so the untrusted-data rule must already be in them"
    )
    assert MCP_SNAPSHOT_LINE in text


def test_ticking_browser_mid_session_serves_tools_to_a_forewarned_harness(
    no_browser_harness,
):
    """The whole sequence, driven: handshake without Browser, tick it, list tools.

    The tools really do arrive on the SAME session (that is the live filter
    working as designed); the pin is that the instructions the harness already
    holds contained the rule before the first page could be read.
    """
    response = no_browser_harness.post(
        {"jsonrpc": "2.0", "id": 96, "method": "initialize", "params": {}},
    )
    session_id = response.headers.get(SESSION_HEADER, "")
    instructions = response.json()["result"]["instructions"]
    assert MCP_UNTRUSTED_LINE in instructions, instructions
    no_browser_harness.panes[PANE_ID].capabilities["browser"] = True
    listed = no_browser_harness.post(
        {"jsonrpc": "2.0", "id": 97, "method": "tools/list", "params": {}},
        session_id=session_id,
    )
    assert listed.status_code == 200, listed.text
    names = [t["name"] for t in listed.json()["result"]["tools"]]
    assert "browser_read_page" in names, (
        "the live filter must still serve the capability the user just ticked"
    )


# --------------------------------------------------------------------------- #
# 13. THE COMPARISON: the identical call, down both paths, on the REAL app.
#
# Every test above this line registers ``/mcp`` on a bare ``FastAPI``, which
# tests the ROUTE and not the product. This one builds the app the daemon
# actually serves - ``create_app`` with ``IRONJARVIS_TOKEN`` set, the middleware
# stack on, the real platform underneath - and drives ONE tool call down BOTH
# execution paths: the Build pane's chat stream, and an external MCP harness.
#
# The claim being pinned is the one the ship is for: an external harness meets
# the SAME gate as the user's own chat, never a weaker one. "Same" is asserted
# on behaviour that would diverge if either lane changed: both file an approval
# into the one registry, both authorise the approved call through the permission
# engine with the human's answer, and neither runs the tool before the answer.
# --------------------------------------------------------------------------- #
CHAT_ASK_MESSAGE = "click the Sign in button on the page"


def _headers(body: bytes, token: str) -> list:
    """Loopback host (v1.175.0's rebinding guard) and the install bearer."""
    return [
        (b"host", b"127.0.0.1:8787"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"authorization", f"Bearer {token}".encode()),
    ]


def _asgi_scope(path: str, body: bytes, token: str, extra: dict | None = None) -> dict:
    headers = _headers(body, token)
    for name, value in (extra or {}).items():
        headers.append((name.lower().encode(), str(value).encode()))
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": headers,
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }


async def _asgi_post(app, path: str, payload: dict, token: str,
                     extra: dict | None = None):
    """One buffered in-process request. Returns ``(status, headers, body)``."""
    raw = json.dumps(payload).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}

    status, headers, chunks = 0, {}, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
            headers.update(
                {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
            )
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await app(_asgi_scope(path, raw, token, extra), receive, send)
    return status, headers, b"".join(chunks)


async def _answer_the_first_card(platform, decision: str, answered: list) -> None:
    """Resolve the first pending approval, on this loop, while a call waits."""
    import asyncio as _asyncio

    for _ in range(2000):
        for approval_id in list(platform.approvals.pending_ids()):
            if platform.approvals.resolve(approval_id, decision):
                answered.append(approval_id)
                return
        await _asyncio.sleep(0.005)
    raise AssertionError("no approval was ever filed for anyone to answer")


async def _drive_chat_stream(app, body: dict, token: str, decide):
    """Consume ``/chat/stream`` AS IT STREAMS, answering each approval frame.

    Buffered clients (``TestClient``, ``httpx.ASGITransport``) hold the whole
    response, so the turn's pause could never be answered through one - the same
    finding ``tests/test_chat_approvals_v1187.py`` records. Hence the raw ASGI
    drive.
    """
    import asyncio as _asyncio

    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await _asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    queue: "_asyncio.Queue" = _asyncio.Queue()

    async def send(msg):
        await queue.put(msg)

    task = _asyncio.create_task(app(_asgi_scope("/chat/stream", raw, token),
                                    receive, send))
    frames: list = []
    buf = ""
    try:
        while True:
            getter = _asyncio.create_task(queue.get())
            done, _ = await _asyncio.wait(
                {getter, task}, return_when=_asyncio.FIRST_COMPLETED, timeout=60
            )
            if getter in done:
                msg = getter.result()
            else:
                getter.cancel()
                if task in done:
                    break
                raise AssertionError("the chat stream produced nothing")
            if msg["type"] != "http.response.body":
                continue
            buf += msg.get("body", b"").decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                event, data_lines = "message", []
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if not data_lines:
                    continue
                try:
                    data = json.loads("\n".join(data_lines))
                except ValueError:
                    continue
                frames.append((event, data))
                if event == "approval":
                    await decide(data)
            if not msg.get("more_body", False):
                break
    finally:
        await task
    return frames


def _click_calling_stream(tool: str, args: dict):
    """A ``router.stream`` stub: round 0 calls *tool*, round 1 answers."""
    rounds = {"n": 0}

    async def fake_stream(*args_, **kw):  # stubs take (*args, **kw)
        from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

        if rounds["n"] == 0:
            rounds["n"] += 1
            response = LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name=tool, arguments=dict(args))],
            )
        else:
            response = LLMResponse(text="done.")
        yield {"type": "final", "response": response,
               "provider": "mock", "model": "mock"}

    return fake_stream


async def test_the_same_ask_tier_call_meets_the_same_gate_in_chat_and_over_mcp(
    tmp_path, monkeypatch
):
    """ONE call, TWO paths, on the app the daemon really serves.

    Driven through ``create_app`` with ``IRONJARVIS_TOKEN`` set, because a bare
    ``FastAPI`` with no middleware is not the product - and the product is where
    this ship was dead.

    What is compared, for the identical ``browser_click``:

    * BOTH lanes file an approval into ``platform.approvals``. Before this fix
      the MCP lane filed nothing and refused itself, so a harness saw six of the
      fourteen tools it was offered actually work.
    * BOTH lanes hand the human's answer to ``registry.invoke`` as
      ``session_allow`` containing the tool - the engine's own sanctioned lift -
      and neither passes ``agent_overrides``, which cannot raise a deny-floor
      tool and must not be used to fake consent.
    * NEITHER lane executes the tool before the answer.
    """
    import asyncio as _asyncio

    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.browser_access = "interactive"
    pane = FakePane(PANE_ID, {"browser": True}, cwd=str(tmp_path))
    platform.terminals.get = lambda pane_id, _p=pane: _p if pane_id == PANE_ID else None
    platform.pane_tokens.pane_lookup = platform.terminals.get
    pane_token = platform.pane_tokens.mint(PANE_ID, {"browser": True})
    platform.router.stream = _click_calling_stream(ASK_TOOL, ASK_ARGS)

    invokes: list[dict] = []
    # ORDER, recorded across both lanes: an ask must PRECEDE the invoke it
    # authorises, in each of them. ``invoke`` is where the permission decision
    # happens, so a call logged before its ask is a call that ran unasked.
    order: list[str] = []
    real_invoke = platform.registry.invoke
    real_request = platform.approvals.request

    async def spy_invoke(*args, **kw):  # spies take (*args, **kw)
        if args and args[0] == ASK_TOOL:
            order.append("invoke")
            invokes.append({"args": args, "kw": kw})
        return await real_invoke(*args, **kw)

    def spy_request(*args, **kw):  # spies take (*args, **kw)
        if args and args[0] == ASK_TOOL:
            order.append("ask")
        return real_request(*args, **kw)

    platform.registry.invoke = spy_invoke
    platform.approvals.request = spy_request

    # ---- lane 1: the Build pane's chat stream --------------------------------
    chat_cards: list[str] = []

    async def approve_frame(data):
        chat_cards.append(data["tool"])
        status, _, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "once"}, INSTALL_TOKEN
        )
        assert status == 200

    frames = await _drive_chat_stream(
        app,
        {
            "messages": [{"role": "user", "content": CHAT_ASK_MESSAGE}],
            "auto_tools": True,
            "pane_id": PANE_ID,
            "workspace_dir": str(tmp_path),
        },
        INSTALL_TOKEN,
        approve_frame,
    )
    assert chat_cards == [ASK_TOOL], (
        "the chat lane did not pause for a card on the ask-tier tool, so this "
        f"comparison has no baseline. frames={[f[0] for f in frames]}"
    )
    chat_invoke = invokes[-1]

    # ---- lane 2: an external MCP harness, same tool, same arguments ----------
    before = len(invokes)
    status, headers, raw = await _asgi_post(
        app, "/mcp",
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        pane_token,
    )
    assert status == 200, (status, raw[:400])
    mcp_session = headers.get(SESSION_HEADER.lower(), "")
    assert mcp_session, headers

    mcp_cards: list[str] = []
    call = _asyncio.create_task(
        _asgi_post(
            app, "/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": ASK_TOOL, "arguments": dict(ASK_ARGS)}},
            pane_token,
            {SESSION_HEADER: mcp_session},
        )
    )
    await _answer_the_first_card(platform, "once", mcp_cards)
    status, _, raw = await call
    assert status == 200, (status, raw[:400])
    assert mcp_cards, "the MCP lane never asked anybody"
    mcp_invoke = invokes[-1]
    assert mcp_invoke["args"][0] == ASK_TOOL
    assert len(invokes) == before + 1

    # ---- the comparison ------------------------------------------------------
    for lane, call_kw in (("chat", chat_invoke["kw"]), ("mcp", mcp_invoke["kw"])):
        grant = call_kw.get("session_allow") or set()
        assert ASK_TOOL in grant, f"{lane}: the answer never reached invoke: {grant!r}"
        assert not call_kw.get("deny_reason"), f"{lane}: {call_kw.get('deny_reason')}"
        assert platform.permissions.authorize(
            ASK_TOOL, dict(ASK_ARGS), None, session_allow=grant
        ).allowed is True, f"{lane}: the approved call would still be refused"

    # And the MCP lane passes NO overrides at all - positionally or otherwise.
    assert len(mcp_invoke["args"]) == 4, mcp_invoke["args"]
    assert "agent_overrides" not in mcp_invoke["kw"], mcp_invoke["kw"]

    # Nothing was authorised before a human answered, in EITHER lane: each
    # lane asked, then invoked, in that order and once each.
    assert order == ["ask", "invoke", "ask", "invoke"], order
