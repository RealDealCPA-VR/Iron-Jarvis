"""v1.274.0 — four sidebar items from the module review, each verified before it was built.

1. ``open`` used to await the model catalog INSIDE the socket's frame loop; the
   catalog probes providers and shells out to CLIs, and the panel posts ``open``
   on every tab switch — so a tab switch could stall every command in flight.
   Now the ``models`` frame is sent from its own task and ``open`` returns at once.
2. Approval cards read "click on the page" because ``describe_browser_call`` read
   flat keys while the tools nest the target. It reads the nested target now.
3. The prompt's roster of browser tools is rendered from what is armed, so a
   panel turn whose ceiling dropped the new-tab tool is not told it has one.
4. The sidebar offers Grant site access itself (the worker already had the path).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import describe_browser_call
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn
from tests._fakes.panel_harness import RealApp
from tests.test_browser_agent_v1262 import _NEUTRAL

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome" / "src"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# 1. open returns before the model probe; the socket keeps reading
# --------------------------------------------------------------------------- #


def test_open_does_not_hold_the_socket_while_the_catalog_probes(tmp_path, monkeypatch):
    """A `send` posted right after `open` is processed while the catalog is still blocked."""
    from iron_jarvis.daemon.routes import connections

    release = __import__("threading").Event()

    def slow_catalog(d):
        release.wait(10)  # off the loop (to_thread); the read loop must not wait on it
        return [{"provider": "mock", "model": "mock", "available": True}]

    monkeypatch.setattr(connections, "selectable_models", slow_catalog)
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        app.one_round("answered while the catalog was still probing")
        app.panel.send(P.PANEL_ACTION_OPEN)
        app.panel.wait_for(P.PANEL_EVENT_HISTORY, "open never answered its cheap frames")
        # The catalog is still blocked. A send must be read and started anyway.
        app.panel.send(P.PANEL_ACTION_SEND, text="hello")
        try:
            app.panel.wait_for(P.PANEL_EVENT_DONE, "the send was stuck behind the model probe")
            names = [e for e, _ in app.panel.events()]
            assert P.PANEL_EVENT_MODELS not in names, "the models frame arrived before the catalog was released"
        finally:
            release.set()
        app.panel.wait_for(P.PANEL_EVENT_MODELS, "the models frame never arrived once the catalog returned")


def test_open_sends_the_models_frame_from_its_own_task():
    src = _src(ROOT / "src" / "iron_jarvis" / "browser" / "panel.py")
    branch = src[src.index("if act == P.PANEL_ACTION_OPEN:"): src.index("if act == P.PANEL_ACTION_RESET:")]
    assert "asyncio.ensure_future(self._emit_models(conn))" in branch
    assert "await self.models()" not in branch, "the probe is awaited inline again"
    emit = src[src.index("async def _emit_models("):]
    emit = emit[: emit.index("\n    # ---")]
    assert "except Exception" in emit, "a failed probe would become a socket error"


# --------------------------------------------------------------------------- #
# 2. Cards name the control
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("browser_click", {"target": {"role": "button", "name": "Delete account"}}, "click 'Delete account'"),
        ("browser_click", {"target": {"element_id": "e17"}, "tab_id": 3, "snapshot_id": "s9"}, "click 'element e17'"),
        ("browser_click", {"target": {"css": "button.buy"}}, "click 'button.buy'"),
        ("browser_type", {"target": {"role": "textbox", "name": "Email"}, "text": "***REDACTED***"}, "type some text into 'Email'"),
        ("browser_type", {"target": {"name": "Search"}, "text": "flights", "press_enter": True}, "type 'flights' into 'Search' and press Enter"),
        ("browser_press_key", {"key": "Enter", "target": {"name": "Card number"}}, "press Enter"),
    ],
)
def test_the_card_reads_the_nested_target_the_tools_really_send(name, args, expected):
    assert describe_browser_call(name, args) == expected


def test_the_redaction_marker_is_never_quoted_on_a_card():
    assert "REDACTED" not in describe_browser_call("browser_type", {"text": "***REDACTED***"})


# --------------------------------------------------------------------------- #
# 3. The roster is the armed set
# --------------------------------------------------------------------------- #


def test_the_block_names_only_the_tools_it_is_given():
    block = chat_turn.browser_agent_block({"browser_click", "browser_navigate", "browser_read_page", "shell"})
    roster = block[block.index("yourself:"): block.index("are yours to call")]
    assert "browser_click and browser_navigate" in roster
    # The ROSTER names acting tools only (read_page is named later in the prose, as advice).
    assert "browser_create_tab" not in roster and "shell" not in roster and "browser_read_page" not in roster
    assert "{tools}" not in block
    assert "WORK IN THE TAB THE USER IS LOOKING AT" in block
    one = chat_turn.browser_agent_block({"browser_click"})
    assert "browser_click are yours to call" in one
    assert "the browser tools are yours to call" in chat_turn.browser_agent_block(set())


def test_a_panel_turn_is_not_told_about_a_new_tab_tool_it_cannot_call(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        system = str(seen[0].get("system") or "")
        offered = {str(t.get("name") or "") for t in (seen[0].get("tools") or [])}
        assert "browser_create_tab" not in offered
        roster = system[system.index("You can act in this browser yourself:"): system.index("are yours to call")]
        assert "browser_create_tab" not in roster, roster
        assert "browser_navigate" in roster and "browser_click" in roster


def test_a_panel_turn_that_asks_for_a_new_tab_is_told_it_has_one(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text="open a new tab and book the first available slot")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        system = str(seen[0].get("system") or "")
        roster = system[system.index("You can act in this browser yourself:"): system.index("are yours to call")]
        assert "browser_create_tab" in roster, roster


def test_both_seams_render_the_roster():
    turn = _src(ROOT / "src" / "iron_jarvis" / "daemon" / "chat_turn.py")
    lane = _src(ROOT / "src" / "iron_jarvis" / "daemon" / "routes" / "chat.py")
    assert 'system += "\\n\\n" + browser_agent_block(armed)' in turn
    assert 'system += "\\n\\n" + browser_agent_block({*armed, *ask_armed})' in lane
    assert '"\\n\\n" + BROWSER_AGENT_BLOCK' not in turn and '"\\n\\n" + BROWSER_AGENT_BLOCK' not in lane


# --------------------------------------------------------------------------- #
# 4. Grant site access, from the sidebar
# --------------------------------------------------------------------------- #


def test_the_sidebar_offers_the_grant_itself():
    html = _src(ADDON / "sidepanel" / "sidepanel.html")
    assert re.search(r'<button class="primary" id="grant"[^>]*hidden[^>]*>Grant site access</button>', html)
    ts = _src(ADDON / "sidepanel" / "sidepanel.ts")
    assert 'el.grant.hidden = !(status.state === "connected" && !status.hostPermission);' in ts
    assert re.search(r'el\.grant\?\.addEventListener\("click"[\s\S]{0,200}kind: "request_host_permission"', ts)
    assert "Open Jarvis and press Grant site access" not in ts, "the panel still sends the user to another app"
    # The worker's path it presses is the one that already exists.
    worker = _src(ADDON / "background" / "index.ts")
    assert 'case "request_host_permission":' in worker
