"""Group 20 (third fix plan): what the screen says is what happens, at the
door that gives coding tools Iron Jarvis's tools (``mcpserver/``).

MC1. A box (Memory, Browser, Hand off) turned OFF on a Build › Code session
could be ON again after Iron Jarvis was killed and started again. The tool
keeps running in the code host, and the restart takes the session back from
the state kept beside it (``hosted/adopt.py``) - which took the boxes from
that kept state, not from the session's record; and turning a box off did
not refresh the kept state (no line from the tool, no write, no snapshot).
So the chip (which reads the record) said off while the door served the
tool. Fixed both ways: the restore reads the boxes from the session's record,
and a box change keeps the state at once.

Driven through the real ``hosted`` service, the real code host and the
recorded stand-in for Claude Code (``tests/_code_host_helpers_v14220.py``,
never a real coding tool - ``tests/_real_tool_guard.py``), and the real
``/mcp`` door.

MC2 (below) - the card a coding tool's browser action puts in front of the
user.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.mcpserver.server as server_mod
from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.browser.protocol import DEFAULT_COMMAND_TIMEOUT_S
from iron_jarvis.browser.snapshot import PageSnapshot
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import EventRecord
from iron_jarvis.hosted import adopt as ADOPT
from iron_jarvis.hosted import store
from iron_jarvis.hosted.jarvis_tools import HostedTokens
from iron_jarvis.mcpserver import stdio_shim
from iron_jarvis.mcpserver.server import capability_refused_message
from iron_jarvis.mcpserver.session import SESSION_HEADER
from tests._build_app_helpers_v1409 import client, frames, new_session
from tests._code_host_helpers_v14220 import (
    boot,
    config_home,
    crash,
    fake_rows,
    host_session,
    kept,
    two_turns,
    use_fake,
)

ACCEPT = "application/json, text/event-stream"

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows")


@pytest.fixture
def hosts(monkeypatch):
    yield from host_session(monkeypatch)


# --------------------------------------------------------------------------- #
# MC1 - the boxes after a hard restart
# --------------------------------------------------------------------------- #
async def _session_with_boxes(hosts, tmp_path: Path, monkeypatch):
    """A Build › Code session (the recorded Claude Code) with Memory AND
    Browser on, one reply done, its state kept by the host. Returns
    ``(app, root, sid, token)``; ``token`` is the session's own door token."""
    root = tmp_path / "root"
    log = tmp_path / "fake.log"
    hosts.home(config_home(root))
    hosts.logs.append(log)
    use_fake(monkeypatch, two_turns(tmp_path), log)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(root))
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True, "browser": True}})
        assert r.status_code == 200, r.text
        first = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
    assert first[-1]["type"] == "done", first
    token = next(r["env"]["IRONJARVIS_MCP_TOKEN"] for r in fake_rows(log) if "argv" in r)
    await kept(app, sid)
    return app, root, sid, token


async def _door(c, token: str):
    """Open the door as the coding tool does; returns a ``(method, params)`` caller."""
    headers = {"Accept": ACCEPT, "Authorization": f"Bearer {token}"}
    init = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "clientInfo": {"name": "t", "version": "1"}}}, headers=headers)
    assert init.status_code == 200, init.text
    headers[SESSION_HEADER] = init.headers[SESSION_HEADER]

    async def rpc(method: str, params: dict | None = None):
        return await c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}},
                            headers=headers)

    return rpc


async def _assert_browser_off_after_restart(app2, sid: str, token: str) -> None:
    svc2 = app2.state.platform.hosted
    assert sid in svc2.live and svc2.live[sid].running, "the session was carried on"
    live = svc2.live[sid]
    assert live.caps.get("browser") is False, f"the Browser box came back ON after the restart: {live.caps}"
    assert live.caps.get("memory") is True, live.caps
    async with client(app2) as c:
        chip = (await c.get(f"/build/app/sessions/{sid}")).json()
        shown = (chip.get("session") or chip)["capabilities"]
        assert shown["browser"] is False and shown["memory"] is True, shown
        assert {k: bool(live.caps.get(k)) for k in shown} == shown, "the door reads what the chip shows"
        rpc = await _door(c, token)
        listed = await rpc("tools/list")
        assert listed.status_code == 200, listed.text
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert names and not any(n.startswith("browser_") for n in names), names
        call = await rpc("tools/call", {"name": "browser_get_status", "arguments": {}})
        assert call.status_code == 403, call.text
        assert call.json()["error"]["message"] == capability_refused_message("browser_get_status", f"code:{sid}")


@windows_only
@pytest.mark.timeout(240)
async def test_a_box_turned_off_stays_off_after_a_hard_restart(hosts, tmp_path, monkeypatch):
    """The user's story: Browser on, a reply, Browser OFF, Iron Jarvis killed
    and started again - the box is still off and the door refuses a browser
    tool. Also pins (b): turning the box off sends the kept state at once,
    with the box off in it."""
    app, root, sid, token = await _session_with_boxes(hosts, tmp_path, monkeypatch)
    proc = app.state.platform.hosted.live[sid].proc
    sent: list[dict] = []
    real_send = proc.link.send

    def spy_send(obj, *rest, **kw):  # spies take (*args, **kw)
        if obj.get("t") == "snapshot" and rest:
            sent.append(json.loads(rest[0].decode("utf-8")))
        return real_send(obj, *rest, **kw)

    proc.link.send = spy_send
    async with client(app) as c:
        r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"browser": False}})
        assert r.status_code == 200, r.text
        assert r.json()["applied"]["capabilities"] == "now", "Memory is still on: no new process"
    await kept(app, sid)
    assert sent and sent[-1]["service"]["caps"]["browser"] is False, (
        "turning the Browser box off did not refresh the state kept beside the running tool"
    )
    crash(app)
    hosts.note()
    app2 = await boot(root)
    await _assert_browser_off_after_restart(app2, sid, token)
    await ADOPT.shutdown(app2.state.platform, keep=False)


@windows_only
@pytest.mark.timeout(240)
async def test_the_restore_takes_the_boxes_from_the_sessions_record(hosts, tmp_path, monkeypatch):
    """Pins (a): the kept state still says Browser ON (it was kept before the
    change reached it), the session's record says OFF - the record wins."""
    app, root, sid, token = await _session_with_boxes(hosts, tmp_path, monkeypatch)
    row = await asyncio.to_thread(store.get, app.state.platform.engine, sid)
    off = json.loads(row.capabilities_json or "{}")
    assert off.get("browser") is True, off
    off["browser"] = False
    await asyncio.to_thread(store.update, app.state.platform.engine, sid,
                            capabilities_json=json.dumps(off, sort_keys=True))
    crash(app)
    hosts.note()
    app2 = await boot(root)
    await _assert_browser_off_after_restart(app2, sid, token)
    await ADOPT.shutdown(app2.state.platform, keep=False)


# --------------------------------------------------------------------------- #
# MC2 - the card a coding tool's browser action puts in front of the user
# --------------------------------------------------------------------------- #
#
# The card used to be one bell row, "An agent run wants to …": it did not say
# which terminal or session asked, on which page, or what would be clicked or
# typed; it offered "Allow for this run" and then counted it once; two of the
# card's answers ("tab", "always") were read as "nobody answered"; and the
# timeout told the coding tool's model to have the user set the tool to allow
# in Settings, which Settings refuses for these tools.
#
# Driven through the REAL app (``create_app``: the real ``/mcp`` route, the
# real registry, permission engine and approvals registry, the real pending
# listing the bell reads and the real answering route). The stand-ins: the
# pane (as the pane token store reads it) and the terminal manager's ``get``,
# a Code session's live state, and the browser's tab, all in memory.
INSTALL = "install-bearer-for-this-test"
PANE = "term_door_card"
CODE_SID = "app_door_card"
TYPED = "my private words 42"
PAGE_URL = "https://example.com/account/settings?session=abc123"


class _Pane:
    def __init__(self) -> None:
        self.id = PANE
        self.pane_name = "notes site"
        self.capabilities = {"browser": True}
        self.cwd = ""
        self.alive = True


class Door:
    """The real app, a terminal holding Browser, a Code session holding
    Browser, and a browser whose tab in front is example.com."""

    def __init__(self, root: str, monkeypatch) -> None:
        from iron_jarvis.daemon.app import create_app

        self.app = create_app(root)
        p = self.platform = self.app.state.platform
        p.config.browser_access = "interactive"
        pane = _Pane()
        p.pane_tokens.pane_lookup = lambda pid: pane if pid == PANE else None
        monkeypatch.setattr(p.terminals, "get", lambda pid: pane if pid == PANE else None)
        self.token = p.pane_tokens.mint(PANE, {"browser": True})
        live = SimpleNamespace(running=True, caps={"browser": True}, folder="", title="Tidy the report",
                               harness="claude-code")
        svc = SimpleNamespace(live={CODE_SID: live}, platform=p)
        svc.tokens = HostedTokens(svc)
        p.hosted = svc
        self.code_token = svc.tokens.mint(CODE_SID, {"browser": True})
        rt = p.browser
        rt.backend.active_tab = {"tab_id": 7, "url": PAGE_URL, "title": "Account settings"}
        rt.snapshots.put(PageSnapshot(
            snapshot_id="snap_0000aaaa", page_version=1, tab_id=7, title="Account settings", url=PAGE_URL,
            timestamp="2026-11-17T00:00:00Z", mode="interactive", truncated=False, text="",
            elements=({"id": "e12", "role": "button", "name": "Save changes"},
                      {"id": "e13", "role": "textbox", "name": "Password", "type": "password"}),
        ))
        self.client = TestClient(self.app)

    def call(self, name: str, arguments: dict, *, token: str):
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {token}"}
        init = self.client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "t", "version": "1"}}})
        assert init.status_code == 200, init.text
        headers[SESSION_HEADER] = init.headers[SESSION_HEADER]
        return self.client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}})

    def ran(self, name: str) -> list[str]:
        """Record every time the REAL tool's ``execute`` is reached (and call through)."""
        out: list[str] = []
        tool = self.platform.registry.get(name)
        real = tool.execute

        async def spy(*args, **kw):  # spies take (*args, **kw)
            out.append(name)
            return await real(*args, **kw)

        tool.execute = spy
        return out


@pytest.fixture()
def door(tmp_path, monkeypatch) -> Door:
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    return Door(str(tmp_path / "home"), monkeypatch)


def _ask(door: Door, name: str, arguments: dict, decision: str | None, *, token: str):
    """Make the call; while its card waits, read what the BELL reads (the
    pending listing) and what the registry holds, then answer it through the
    real answering route (``decision`` None: nobody answers). From another
    thread, as in life: the call holds the test's thread."""
    seen: dict = {}
    stop = threading.Event()
    other = TestClient(door.app)
    auth = {"Authorization": f"Bearer {INSTALL}"}

    def answer() -> None:
        approvals = door.platform.approvals
        while not stop.wait(0.01):
            ids = approvals.pending_ids()
            if not ids:
                continue
            # Wait for what is asserted: the bell's row, which the listing
            # builds from the card's recorded event, can land a moment after
            # the id (a loaded runner once read an empty listing here, and the
            # test passed only on its retry, v1.539.0's branch run).
            listing = other.get("/chat/approvals/pending", headers=auth).json()
            if not any(str(r.get("session_id", "")).startswith("mcp:")
                       for r in listing.get("approvals") or []):
                continue
            seen["listing"] = listing
            seen["pending"] = [m for sid in (f"mcp:{PANE}", f"mcp:code:{CODE_SID}")
                               for m in approvals.pending_for(sid)]
            if decision is not None:
                seen["answer"] = other.post(f"/chat/approvals/{ids[0]}", json={"decision": decision},
                                            headers=auth)
            return

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        response = door.call(name, arguments, token=token)
    finally:
        stop.set()
        thread.join(5)
    assert "listing" in seen, "the call never put a card in front of the user"
    rows = [r for r in seen["listing"]["approvals"] if str(r.get("session_id", "")).startswith("mcp:")]
    assert len(rows) == 1, seen["listing"]
    return response, rows[0], seen


def _text(response) -> str:
    assert response.status_code == 200, response.text
    return response.json()["result"]["content"][0]["text"]


def test_the_card_names_the_terminal_the_page_and_what_is_clicked(door):
    ran = door.ran("browser_click")
    response, row, seen = _ask(door, "browser_click", {"target": "e12"}, "deny", token=door.token)
    why = row.get("why") or ""
    assert "Build terminal “notes site”" in why, why
    assert "click 'Save changes'" in why, why
    assert "example.com" in why and "“Account settings”" in why, why
    assert "session=abc123" not in why and "/account" not in why, "the host only, never the address"
    assert row.get("once_only") is True, "Allow once and Deny only"
    assert row.get("timeout_s") == int(server_mod.MCP_APPROVAL_TIMEOUT_S), row
    assert seen["pending"] and seen["pending"][0].get("why") == why
    assert seen["pending"][0].get("once_only") is True
    assert server_mod.MCP_ASK_DENIED_MESSAGE in _text(response) and not ran


def test_a_code_sessions_card_names_the_session_and_its_tool(door):
    response, row, _ = _ask(door, "browser_press_key", {"key": "Enter"}, "deny", token=door.code_token)
    why = row.get("why") or ""
    assert why.startswith("The coding session “Tidy the report” (Claude Code) wants to press Enter"), why
    assert "example.com" in why, why
    assert server_mod.MCP_ASK_DENIED_MESSAGE in _text(response)


def test_typed_text_is_never_on_the_card(door):
    ran = door.ran("browser_type")
    response, row, seen = _ask(door, "browser_type", {"target": "e13", "text": TYPED}, "once", token=door.token)
    why = row.get("why") or ""
    assert "type some text into 'Password'" in why, why
    assert TYPED not in json.dumps(seen["listing"]) and TYPED not in json.dumps(seen["pending"], default=str)
    with session_scope(door.platform.engine) as db:
        rows = [r.payload_json for r in db.exec(select(EventRecord)).all()
                if r.type == EventType.APPROVAL_REQUESTED]
    assert rows and not any(TYPED in (r or "") for r in rows), "the typed text reached the event log"
    assert ran == ["browser_type"]
    _text(response)


@pytest.mark.parametrize("decision", ["always", "conversation", "tab"])
def test_a_wider_answer_runs_this_one_call_and_never_reads_as_nobody_answered(door, decision):
    ran = door.ran("browser_click")
    response, _, seen = _ask(door, "browser_click", {"target": "e12"}, decision, token=door.token)
    assert seen["answer"].status_code == 200, seen["answer"].text
    text = _text(response)
    assert server_mod.MCP_ASK_TIMEOUT_MESSAGE not in text, f"the user answered {decision!r}: {text}"
    assert ran == ["browser_click"], "an allow answer runs the call"
    # ... and only this one: the next call asks again.
    again, _, _ = _ask(door, "browser_click", {"target": "e12"}, "deny", token=door.token)
    assert server_mod.MCP_ASK_DENIED_MESSAGE in _text(again) and ran == ["browser_click"]


def test_nobody_answering_returns_the_plain_sentence_and_nothing_runs(door, monkeypatch):
    monkeypatch.setattr(server_mod, "MCP_APPROVAL_TIMEOUT_S", 0.2)
    ran = door.ran("browser_click")
    response, _, _ = _ask(door, "browser_click", {"target": "e12"}, None, token=door.token)
    text = _text(response)
    assert server_mod.MCP_ASK_TIMEOUT_MESSAGE in text, text
    assert "Settings" not in text and "set this tool" not in text, text
    assert not ran, "an unanswered card ran the tool"


def test_the_wait_is_the_longest_the_coding_tool_can_still_receive():
    """45 s: the bridge's 60 s round trip less the 15 s an approved click may
    then take in the browser. Longer and the answer never reaches the tool;
    the timeout sentence says the number it really waited."""
    assert server_mod.MCP_APPROVAL_TIMEOUT_S + DEFAULT_COMMAND_TIMEOUT_S == stdio_shim.DEFAULT_TIMEOUT_S
    assert f"within {int(server_mod.MCP_APPROVAL_TIMEOUT_S)} seconds" in server_mod.MCP_ASK_TIMEOUT_MESSAGE
