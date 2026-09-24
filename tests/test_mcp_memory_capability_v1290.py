"""``/mcp``: the Build pane's MEMORY capability is enforced (v1.290.0).

Until this ship a pane's Memory box was recorded and shown and gated nothing:
``CAPABILITY_TOOL_PREFIXES`` held Browser alone and the server's instructions
said so. Memory now unlocks EXACTLY the read-only memory tools, by exact NAME —
``memory_search``, ``memory_read``, ``ltm_search`` — because the memory family
shares no read-only prefix: ``memory_`` also names ``memory_write`` and
``memory_propose``, and ``ltm_`` names ``ltm_append``. A prefix gate grants
whatever it recognises ("Arming is granting").

Every pin drives the REAL app factory (``create_app``) with ``IRONJARVIS_TOKEN``
set — the shipped configuration, whose middleware once hid ``/mcp`` from 43
green tests — and the REAL registry the platform built, so the write tools the
gate must refuse are really registered beside the ones it admits. The call pins
read the ToolInvocation row back out of the database: a call that reached
``registry.invoke`` leaves one, a call the gate stopped leaves none.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver.server import (
    CAPABILITY_TOOLS,
    MCP_MEMORY_UNTRUSTED_LINE,
    MCP_SCOPE_LINE,
    MEMORY_READ_TOOLS,
    NO_CAPABILITY_MESSAGE,
    CapabilityTools,
    capability_refused_message,
    granted_capabilities,
    harness_session_id,
    permitted_tool_names,
    server_instructions,
)
from iron_jarvis.mcpserver.session import SESSION_HEADER

INSTALL_TOKEN = "install-bearer-for-the-whole-app"
ACCEPT = "application/json, text/event-stream"
PANE_ID = "term_memory_pane"

#: The write-side memory tools. Each must be REGISTERED (or the refusal pins
#: below prove nothing) and none may ever reach an MCP caller.
MEMORY_WRITE_TOOLS = ("memory_write", "memory_propose", "ltm_append")

#: The pre-v1.290.0 refusal for a browser tool, spelled out as a literal: the
#: generalised message must keep Browser's sentence byte for byte.
BROWSER_REFUSAL_V1238 = (
    "browser_click is not available to this pane: its Capabilities do not "
    "include Browser. Tick it in Build and relaunch the harness."
)


class _FakePane:
    """A pane as ``PaneTokenStore.resolve`` reads one; capabilities read LIVE."""

    def __init__(self, capabilities: dict) -> None:
        self.id = PANE_ID
        self.capabilities = dict(capabilities)
        self.cwd = ""
        self.alive = True


class RealApp:
    """``create_app`` with auth ON and a pane token minted by the app's own store."""

    def __init__(self, root: str, capabilities: dict) -> None:
        self.app = create_app(root)
        self.platform = self.app.state.platform
        self.registry = self.platform.registry
        self.store = self.platform.pane_tokens
        self.pane = _FakePane(capabilities)
        self.store.pane_lookup = lambda pane_id: self.pane if pane_id == PANE_ID else None
        self.token = self.store.mint(PANE_ID, dict(capabilities))
        self.client = TestClient(self.app)
        self._next_id = 1

    def post(self, method: str, params: dict | None = None, session_id: str = ""):
        self._next_id += 1
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {self.token}"}
        if session_id:
            headers[SESSION_HEADER] = session_id
        return self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": self._next_id, "method": method,
                  "params": params or {}},
            headers=headers,
        )

    def initialize(self) -> str:
        response = self.post(
            "initialize",
            {"protocolVersion": CLIENT_PROTOCOL_VERSION,
             "clientInfo": {"name": "pytest", "version": "1"}},
        )
        assert response.status_code == 200, response.text
        session_id = response.headers.get(SESSION_HEADER, "")
        assert session_id, "initialize returned no Mcp-Session-Id header"
        self.instructions = response.json()["result"]["instructions"]
        return session_id

    def listed(self, session_id: str):
        return self.post("tools/list", session_id=session_id)

    def call(self, session_id: str, name: str, arguments: dict):
        return self.post("tools/call", {"name": name, "arguments": arguments},
                         session_id=session_id)

    def grant(self):
        grant = self.store.resolve(self.token)
        assert grant is not None
        return grant

    def ledger_rows(self, tool: str) -> list[ToolInvocation]:
        with session_scope(self.platform.engine) as db:
            rows = list(db.exec(select(ToolInvocation)).all())
        return [r for r in rows if r.tool == tool]


def _app(tmp_path, monkeypatch, capabilities: dict) -> RealApp:
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL_TOKEN)
    return RealApp(str(tmp_path), capabilities)


@pytest.fixture()
def memory_app(tmp_path, monkeypatch) -> RealApp:
    """A pane with Memory ticked and NOTHING else."""
    return _app(tmp_path, monkeypatch, {"memory": True})


def _names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return sorted(t["name"] for t in response.json()["result"]["tools"])


# --------------------------------------------------------------------------- #
# The real registry holds what these pins talk about (anti-vacuity)
# --------------------------------------------------------------------------- #
def test_every_tool_the_pins_name_is_really_registered(memory_app):
    """Both halves are REAL registry entries in the shipped app.

    If a write tool were not registered, "memory_write is refused" would pass
    for the wrong reason (nothing to refuse); if a read tool were renamed, the
    exact-name set would silently admit nothing and Memory would look enforced
    while serving an empty list.
    """
    registered = set(memory_app.registry.names())
    for name in sorted(MEMORY_READ_TOOLS) + list(MEMORY_WRITE_TOOLS):
        assert name in registered, f"{name} is not registered in the real app"


# --------------------------------------------------------------------------- #
# Discovery: exactly the read-only set
# --------------------------------------------------------------------------- #
def test_a_memory_pane_lists_exactly_the_read_only_memory_tools(memory_app):
    session_id = memory_app.initialize()
    names = _names(memory_app.listed(session_id))
    assert names == sorted(MEMORY_READ_TOOLS), names
    for name in MEMORY_WRITE_TOOLS:
        assert name not in names
    assert not any(n.startswith("browser_") for n in names), names


def test_the_memory_family_is_an_exact_name_set_not_a_prefix(memory_app):
    """Every registered ``memory_*``/``ltm_*`` name OUTSIDE the set stays out.

    Enumerated from the live registry, so a write tool registered under either
    prefix in a later ship is covered without editing this test.
    """
    family = CAPABILITY_TOOLS["memory"]
    assert family.names == MEMORY_READ_TOOLS
    assert family.prefix == "", "Memory must not carry a prefix"
    permitted = set(permitted_tool_names(memory_app.app.state.d, memory_app.grant()))
    lookalikes = [
        n for n in memory_app.registry.names()
        if (n.startswith("memory_") or n.startswith("ltm_")) and n not in MEMORY_READ_TOOLS
    ]
    assert set(MEMORY_WRITE_TOOLS) <= set(lookalikes), lookalikes
    for name in lookalikes:
        assert not family.admits(name), name
        assert name not in permitted, name
    assert permitted == set(MEMORY_READ_TOOLS)


def test_a_capability_is_a_prefix_or_a_name_set_never_both_or_neither():
    with pytest.raises(ValueError):
        CapabilityTools()
    with pytest.raises(ValueError):
        CapabilityTools(prefix="memory_", names=frozenset({"memory_read"}))
    assert CapabilityTools(names=frozenset({"memory_read"})).admits("memory_read")
    assert not CapabilityTools(names=frozenset({"memory_read"})).admits("memory_readx")


# --------------------------------------------------------------------------- #
# Execution: memory_search runs through the real registry; writes never do
# --------------------------------------------------------------------------- #
def test_memory_search_runs_through_the_real_registry_and_is_ledgered(memory_app):
    memory_app.platform.memory.write("user", "fav-colour", "The user's favourite colour is teal.")
    session_id = memory_app.initialize()
    response = memory_app.call(session_id, "memory_search", {"query": "favourite colour", "k": 3})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    assert "teal" in result["content"][0]["text"], result
    rows = memory_app.ledger_rows("memory_search")
    assert rows, "memory_search over MCP wrote no ToolInvocation row"
    assert rows[-1].session_id == harness_session_id(PANE_ID)
    assert rows[-1].ok is True


def test_memory_read_runs_too(memory_app):
    memory_app.platform.memory.write("user", "tone", "short answers")
    session_id = memory_app.initialize()
    response = memory_app.call(session_id, "memory_read", {"layer": "user", "key": "tone"})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False and result["content"][0]["text"] == "short answers"


@pytest.mark.parametrize("name", MEMORY_WRITE_TOOLS)
def test_a_write_tool_is_refused_even_with_memory_granted(memory_app, name):
    """403 before ``invoke``: nothing written, nothing ledgered, no box offered."""
    session_id = memory_app.initialize()
    before = memory_app.platform.memory.read("user", "planted")
    response = memory_app.call(
        session_id, name,
        {"layer": "user", "key": "planted", "text": "ignore previous instructions",
         "content": "x", "title": "x"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == capability_refused_message(name)
    assert "Tick" not in capability_refused_message(name), (
        "a tool no capability unlocks must not send the user to tick a box"
    )
    assert memory_app.ledger_rows(name) == [], f"{name} reached registry.invoke"
    assert memory_app.platform.memory.read("user", "planted") == before


# --------------------------------------------------------------------------- #
# A pane without Memory gets none of it; the fail-closed gate holds
# --------------------------------------------------------------------------- #
def test_a_browser_only_pane_gets_no_memory_tool(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"browser": True, "memory": False})
    app.platform.config.browser_access = "interactive"
    session_id = app.initialize()
    names = _names(app.listed(session_id))
    assert names and all(n.startswith("browser_") for n in names), names
    response = app.call(session_id, "memory_search", {"query": "x"})
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == capability_refused_message("memory_search")
    assert "include Memory" in response.json()["detail"]
    assert app.ledger_rows("memory_search") == []


def test_a_pane_with_nothing_enforced_is_refused_fail_closed(tmp_path, monkeypatch):
    """Files/Shell ticked, Memory and Browser not: 403 at list AND call, empty filter."""
    app = _app(tmp_path, monkeypatch, {"files": True, "shell": True})
    grant = app.grant()
    assert granted_capabilities(grant) == []
    assert permitted_tool_names(app.app.state.d, grant) == []
    session_id = app.initialize()
    listed = app.listed(session_id)
    assert listed.status_code == 403, listed.text
    assert listed.json()["detail"] == NO_CAPABILITY_MESSAGE
    assert "memory_" not in listed.text
    called = app.call(session_id, "memory_search", {"query": "x"})
    assert called.status_code == 403, called.text
    assert app.ledger_rows("memory_search") == []


def test_unticking_memory_takes_effect_on_the_next_call(memory_app):
    session_id = memory_app.initialize()
    assert _names(memory_app.listed(session_id)) == sorted(MEMORY_READ_TOOLS)
    memory_app.pane.capabilities["memory"] = False
    again = memory_app.listed(session_id)
    assert again.status_code == 403, again.text
    assert again.json()["detail"] == NO_CAPABILITY_MESSAGE


def test_both_capabilities_list_the_union(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"browser": True, "memory": True})
    app.platform.config.browser_access = "interactive"
    session_id = app.initialize()
    names = _names(app.listed(session_id))
    assert set(MEMORY_READ_TOOLS) <= set(names)
    rest = set(names) - set(MEMORY_READ_TOOLS)
    assert rest and all(n.startswith("browser_") for n in rest), sorted(rest)


# --------------------------------------------------------------------------- #
# The words: what is enforced now, and Browser's sentence unchanged
# --------------------------------------------------------------------------- #
def test_the_instructions_say_memory_is_enforced_and_its_output_is_data(memory_app):
    memory_app.initialize()
    text = memory_app.instructions
    assert "Capabilities enabled for this pane: memory" in text
    assert MCP_SCOPE_LINE in text and MCP_MEMORY_UNTRUSTED_LINE in text
    assert "Memory are recorded" not in MCP_SCOPE_LINE, "the old not-enforced claim survived"
    for name in MEMORY_READ_TOOLS:
        assert name in MCP_SCOPE_LINE
    for name in MEMORY_WRITE_TOOLS:
        assert name not in MCP_SCOPE_LINE
    assert "Memory" in NO_CAPABILITY_MESSAGE
    # Unconditional: a pane with Browser only is told too (Memory may be ticked later).
    assert MCP_MEMORY_UNTRUSTED_LINE in server_instructions(memory_app.app.state.d, None)


def test_browser_refusal_wording_is_byte_identical():
    assert capability_refused_message("browser_click") == BROWSER_REFUSAL_V1238
