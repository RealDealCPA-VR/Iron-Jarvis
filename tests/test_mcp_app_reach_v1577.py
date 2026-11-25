"""v1.577.0 — large apps work well: each app's own settings, read live, and
its tools reach the reply by use, not by a 24-tool cap.

Pinned here, through the REAL registry gate (``registry.invoke`` ->
``PermissionEngine.authorize`` -> the platform's resolver), the real chat
lanes (only the model is scripted) and the real routes:

* ``approval``: "server" runs an app's tool with no Iron Jarvis card;
  "writes" runs its readers and holds its changers for a card about THAT
  tool; "ask" holds every tool; "" (every existing row) is unchanged. Read
  LIVE from the saved row: no restart.
* A held-back tool and a LOCKED app's tool still ask, whatever the mode.
* ``trust_all`` releases tools a later version of the app added (never one
  the scan flagged).
* ``pinned_tools`` join every reply the app is part of, past the cap.
* ``app_tools_find`` finds an app's other tools and they join the reply at
  the tier the app already has; a tool used in a reply joins the chat's next.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import policy as P
from iron_jarvis.mcp import reach
from iron_jarvis.mcp.client import FakeTransport, MCPClient
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import asks_each_use, runs_unasked

APP = "books"


def _client() -> MCPClient:
    return MCPClient(
        FakeTransport({
            "tools/call": lambda p: {
                "content": [{"type": "text", "text": f"{p.get('name')}:ok"}], "isError": False,
            },
        }),
        name=APP,
    )


#: An accounting-sized app: 60 tools, NO annotations on the writers (the
#: common real-world case), readers declared read-only.
WRITERS = [f"create_thing_{i}" for i in range(30)] + ["record_payment", "apply_credit", "enter_bill"]
READERS = [f"list_thing_{i}" for i in range(24)] + ["get_invoice", "find_customer", "wait_for_decision"]


def _connect(platform, row_extra: dict | None = None, *, quarantined=(), locked=False):
    row = {"name": APP, "command": "books-mcp", **(row_extra or {})}
    platform.config.mcp_servers.append(row)
    client = _client()
    for n in WRITERS + READERS:
        reads = n in READERS
        desc = {
            "record_payment": "Record a customer payment against an invoice.",
            "apply_credit": "Apply a credit note to a customer balance.",
            "enter_bill": "Enter a vendor bill.",
        }.get(n, f"{n.replace('_', ' ')} in the books.")
        tool = MCPRemoteTool(client, APP, n, desc, None, {"readOnlyHint": True} if reads else {})
        tool.quarantined = n in quarantined
        tool.locked = locked
        platform.registry.register(tool, mcp=True)
    return row


def _ctx(platform, tmp_path: Path) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )


def _invoke(platform, tmp_path, name, **kw):
    return asyncio.run(
        platform.registry.invoke(name, {}, _ctx(platform, tmp_path), platform.permissions, **kw)
    )


@pytest.fixture()
def platform(tmp_path):
    app = create_app(str(tmp_path / "root"))
    return app.state.platform


# --------------------------------------------------------------------------- #
# the setting itself
# --------------------------------------------------------------------------- #
def test_a_row_without_settings_reads_as_before():
    p = P.policy_of({"name": "x"})
    assert (p.approval, p.trust_all, p.pinned) == ("", False, ())
    assert p.resource_timeout_s == 15.0 and p.resource_chars == 20_000
    assert P.policy_of({"name": "x", "approval": "SERVER"}).approval == "server"
    assert P.policy_of({"name": "x", "approval": "whatever"}).approval == ""
    assert P.normalise_pins("a, b,a\nmcp__x__c") == ["a", "b", "c"]
    # Bounds: a typo never makes a read wait an hour or flood the context.
    q = P.policy_of({"name": "x", "resource_timeout_s": 99999, "resource_chars": 10})
    assert q.resource_timeout_s == P.RESOURCE_TIMEOUT_MAX_S and q.resource_chars == 1000


# --------------------------------------------------------------------------- #
# approval, through the real gate
# --------------------------------------------------------------------------- #
def test_default_mode_is_unchanged_an_unarmed_app_call_is_refused_headless(platform, tmp_path):
    _connect(platform)
    tool = platform.registry.get(f"mcp__{APP}__enter_bill")
    assert not asks_each_use(tool) and not runs_unasked(tool)
    r = _invoke(platform, tmp_path, f"mcp__{APP}__enter_bill")
    assert r.ok is False and "permission" in (r.error or "").lower(), r


def test_server_mode_runs_every_tool_without_a_card_and_applies_live(platform, tmp_path):
    row = _connect(platform)
    name = f"mcp__{APP}__enter_bill"
    assert _invoke(platform, tmp_path, name).ok is False  # control: as before
    row["approval"] = "server"  # the saved row changes: no restart
    r = _invoke(platform, tmp_path, name)
    assert r.ok is True and r.output.startswith("enter_bill:"), r
    row["approval"] = ""
    assert _invoke(platform, tmp_path, name).ok is False


def test_writes_mode_runs_readers_and_holds_writers_for_a_card_about_that_tool(platform, tmp_path):
    _connect(platform, {"approval": "writes"})
    read, write = f"mcp__{APP}__get_invoice", f"mcp__{APP}__record_payment"
    assert _invoke(platform, tmp_path, read).ok is True
    assert asks_each_use(platform.registry.get(write))
    # An armed name (the app switched on for the chat) does not lift it ...
    held = _invoke(platform, tmp_path, write, session_allow={write, "mcp_call"}, card_allow=set())
    assert held.ok is False
    # ... only the user's answer to a card about THIS tool does.
    ok = _invoke(platform, tmp_path, write, session_allow={write}, card_allow={write})
    assert ok.ok is True, ok


def test_ask_mode_holds_even_a_reader(platform, tmp_path):
    _connect(platform, {"approval": "ask"})
    read = f"mcp__{APP}__get_invoice"
    assert asks_each_use(platform.registry.get(read))
    assert _invoke(platform, tmp_path, read, session_allow={read}, card_allow=set()).ok is False


def test_a_held_back_or_locked_tool_still_asks_under_server_mode(platform, tmp_path):
    _connect(platform, {"approval": "server"}, quarantined={"apply_credit"})
    held = platform.registry.get(f"mcp__{APP}__apply_credit")
    assert asks_each_use(held) and not runs_unasked(held)
    assert _invoke(platform, tmp_path, f"mcp__{APP}__apply_credit").ok is False
    held.quarantined = False
    held.locked = True
    assert not runs_unasked(held)


def test_the_route_saves_validates_and_refuses_a_locked_app(platform, tmp_path):
    client = TestClient(create_app(str(tmp_path / "r2")))
    p2 = client.app.state.platform
    _connect(p2)
    r = client.put(f"/mcp/servers/{APP}/policy", json={"approval": "server", "pinned_tools": ["enter_bill", "nope"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy"]["approval"] == "server" and body["unknown_pins"] == ["nope"]
    saved = next(s for s in p2.config.mcp_servers if s["name"] == APP)
    assert saved["approval"] == "server" and saved["pinned_tools"] == ["enter_bill", "nope"]
    row = next(s for s in client.get("/mcp/servers").json()["servers"] if s["name"] == APP)
    assert row["policy"]["approval_words"] == P.APPROVAL_WORDS["server"]
    assert client.put(f"/mcp/servers/{APP}/policy", json={"approval": "maybe"}).status_code == 422
    assert client.put("/mcp/servers/none/policy", json={"approval": ""}).status_code == 404
    # Omitted keeps; "" clears.
    client.put(f"/mcp/servers/{APP}/policy", json={"trust_all": True})
    saved = next(s for s in p2.config.mcp_servers if s["name"] == APP)
    assert saved["approval"] == "server" and saved["trust_all"] is True
    client.put(f"/mcp/servers/{APP}/policy", json={"approval": ""})
    saved = next(s for s in p2.config.mcp_servers if s["name"] == APP)
    assert "approval" not in saved
    saved["locked"] = "microsoft_365"
    assert client.put(f"/mcp/servers/{APP}/policy", json={"approval": "server"}).status_code == 409


def test_trust_whole_app_releases_a_new_writer_but_never_a_flagged_one(tmp_path):
    from iron_jarvis.mcp.tools import apply_quarantine

    home = tmp_path / "home"
    client = _client()

    def tools(names, flagged=()):
        out = []
        for n in names:
            t = MCPRemoteTool(client, APP, n, n, None, {})
            t.description_flagged = n in flagged
            out.append(t)
        return out

    P.bind(None)
    P.sync([{"name": APP}])
    assert apply_quarantine(APP, tools(["a"]), home) == []  # first load trusts all
    assert apply_quarantine(APP, tools(["a", "b"]), home) == ["b"]  # control: held
    P.sync([{"name": APP, "trust_all": True}])
    try:
        assert apply_quarantine(APP, tools(["a", "b", "c", "d"], flagged={"d"}), home) == ["d"]
    finally:
        P.sync([])


# --------------------------------------------------------------------------- #
# reach: pins, the finder, continuity — through the real lanes
# --------------------------------------------------------------------------- #
def _scripted(platform, monkeypatch, script):
    """A model for BOTH lanes that follows *script* (a list of tool calls
    per round; then words), recording the tools it was shown each round."""
    seen: dict = {"tools": []}
    rounds = iter(script)

    def _next():
        calls = next(rounds, None)
        if calls:
            return LLMResponse(text="", tool_calls=[ToolCall(id=f"c{i}", name=n, arguments=a)
                                                     for i, (n, a) in enumerate(calls)])
        return LLMResponse(text="Done.", tool_calls=[])

    def _names(tools):
        return [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []]

    async def fake_complete(*, system, messages, tools, **kw):
        seen["tools"].append(_names(tools))
        return RouteResult(_next(), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        seen["tools"].append(_names(tools))
        resp = _next()
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return seen


def _send(client, lane, text, **extra):
    body = {"messages": [{"role": "user", "content": text}], "auto_tools": True, **extra}
    r = client.post("/chat" if lane == "post" else "/chat/stream", json=body)
    assert r.status_code == 200, r.text
    return r


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_pinned_tool_joins_past_the_cap_and_the_finder_is_offered(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path / "root")))
    platform = client.app.state.platform
    # A tool the words of the message would never pick.
    _connect(platform, {"pinned_tools": ["wait_for_decision"]})
    seen = _scripted(platform, monkeypatch, [])
    _send(client, lane, "list the things", connectors=[APP])
    shown = seen["tools"][0]
    mine = [t for t in shown if t.startswith(f"mcp__{APP}__")]
    assert f"mcp__{APP}__wait_for_decision" in mine
    assert len(mine) == chat_turn._MAX_CONNECTOR_TOOLS + 1
    assert reach.FINDER in shown


def test_without_a_pin_the_capped_pick_is_unchanged(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path / "root")))
    platform = client.app.state.platform
    _connect(platform)
    seen = _scripted(platform, monkeypatch, [])
    _send(client, "post", "list the things", connectors=[APP])
    mine = [t for t in seen["tools"][0] if t.startswith(f"mcp__{APP}__")]
    assert f"mcp__{APP}__wait_for_decision" not in mine
    assert len(mine) == chat_turn._MAX_CONNECTOR_TOOLS


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_the_finder_brings_a_tool_the_cap_left_out_and_it_runs(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path / "root")))
    platform = client.app.state.platform
    _connect(platform)
    pay = f"mcp__{APP}__record_payment"
    seen = _scripted(platform, monkeypatch, [
        [(reach.FINDER, {"query": "record a customer payment"})],
        [(pay, {"invoice": "INV-1"})],
    ])
    # The chat page names every turn (`turn_id`); what a search found is kept
    # for THAT turn only.
    r = _send(client, lane, "list the things", connectors=[APP], thread_id="t-find-" + lane,
              turn_id="turn-1-" + lane)
    assert pay not in seen["tools"][0], "control: the capped pick left it out"
    assert pay in seen["tools"][1], "the finder brought it into the next round"
    text = r.text
    assert "record_payment:ok" in text or pay in str(r.json() if lane == "post" else text)
    # CONTINUITY: the chat's next reply holds it from the start.
    seen2 = _scripted(platform, monkeypatch, [])
    _send(client, lane, "and the next one", connectors=[APP], thread_id="t-find-" + lane,
          turn_id="turn-2-" + lane)
    assert pay in seen2["tools"][0]


def test_the_finder_searches_only_the_apps_already_in_the_reply(platform):
    _connect(platform)
    found = reach.search(platform.registry, [APP], "apply a credit to a customer")
    assert found[0] == f"mcp__{APP}__apply_credit"
    assert reach.search(platform.registry, ["other"], "apply a credit") == []
    assert reach.search(platform.registry, [APP], "zzz qqq") == []


def test_widen_never_brings_an_app_that_is_not_in_the_reply_and_nothing_at_low_trust(platform):
    _connect(platform, {"pinned_tools": ["enter_bill"]})
    armed, auto, ask = reach.widen(SimpleD(platform), None, ["read_file"], [], [], can_ask=True)
    assert armed == ["read_file"] and ask == []
    armed, auto, ask = reach.widen(
        SimpleD(platform), None, [f"mcp__{APP}__get_invoice"], [], [], can_ask=True, low_trust=True,
    )
    assert armed == [f"mcp__{APP}__get_invoice"]


def test_post_lane_widen_drops_a_tool_that_would_need_a_card(platform):
    _connect(platform, {"pinned_tools": ["enter_bill"]})
    reach.RECENT.note("t1", [f"mcp__{APP}__record_payment"])
    # The app is only ASKED in this reply (named, not switched on): the POST
    # lane cannot card, so neither the pin nor the continued tool joins.
    armed, _a, ask = reach.widen(SimpleD(platform), _Body("t1"), [], [], [f"mcp__{APP}__get_invoice"],
                                 can_ask=False)
    assert f"mcp__{APP}__enter_bill" not in armed and f"mcp__{APP}__record_payment" not in armed
    # The stream lane asks for them instead.
    armed, _a, ask = reach.widen(SimpleD(platform), _Body("t1"), [], [], [f"mcp__{APP}__get_invoice"],
                                 can_ask=True)
    assert {f"mcp__{APP}__enter_bill", f"mcp__{APP}__record_payment"} <= set(ask)


class SimpleD:
    def __init__(self, platform):
        self.platform = platform


class _Body:
    def __init__(self, thread_id):
        self.thread_id = thread_id


# --------------------------------------------------------------------------- #
# a program for another kind of computer is named as such (item 8)
# --------------------------------------------------------------------------- #
def test_a_windows_program_on_a_linux_host_is_named_before_any_other_reason():
    from iron_jarvis.mcp.tools import classify_load_failure, runs_elsewhere

    row = {"name": APP, "command": r"C:\Apps\Books\Books.exe", "args": [r"resources\mcp\server.mjs"]}
    said = runs_elsewhere(row, os_name="posix")
    assert "Windows program" in said["reason"] and "web address" in said["fix"]
    assert runs_elsewhere({"name": APP, "command": "Books.exe"}, os_name="posix")
    assert runs_elsewhere(row, os_name="nt") == {}
    assert runs_elsewhere({"name": APP, "command": "npx"}, os_name="posix") == {}
    assert runs_elsewhere({"name": APP, "command": "/opt/books/server"}, os_name="nt")
    assert runs_elsewhere({"name": APP, "transport": "http", "command": "x.exe"}, os_name="posix") == {}
    if __import__("os").name != "nt":
        assert classify_load_failure(row, "FileNotFoundError: x")["reason"] == said["reason"]
