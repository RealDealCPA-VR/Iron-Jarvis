"""Third north-star fix plan, group 20 (what the screen says is what happens),
the browser sidebar's one switch: SB4 and SB6 (decision G18).

SB4: with the sidebar open in two browser windows, turning "Allow all page
actions" OFF in one was undone at the next tab switch: the other window still
held "on" (it read the stored setting once and never again) and sent it with
its next ``open``, and the daemon took whichever arrived last. Now the switch
has one truth: a PRESS is the only thing that turns it on against a press that
turned it off (an ``open`` may restore the setting after a browser restart, but
never turn the switch back ON over the last press's OFF), every open window is
told the new value, and the panel re-reads the stored setting when it changes
and before each ``open``.

SB6 (G18): a RISK card (a payment or password field, a destructive word, a
flagged page) offered "Allow all page actions": one press allowed the risky
action AND turned the switch on for every tab. Now the panel draws such a card
(``floor``) with "Allow once" and "Deny" only, and the daemon reads an
allow-all answer for it as nothing: the action is not run, the switch does not
change, the card stays, and the panel is told in one sentence. An older
add-on's allow-all (an Allow followed by the switch) does not turn the switch
on either.

Driven through the REAL ``PanelTurns`` and the REAL ``TabGrants`` (the switch
itself); the browser runtime around it is a stand-in that delegates exactly as
``BrowserRuntime.auto_allowed`` / ``set_auto_allow`` do. The panel side is
pinned by reading ``sidepanel.ts`` (and type-checked), never run in a browser.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.grants import TabGrants
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.core.turns import TURNS

ROOT = Path(__file__).resolve().parents[1]
PANEL_TS = ROOT / "extensions" / "chrome" / "src" / "sidepanel" / "sidepanel.ts"
BUDGET_S = 10.0


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


class _Runtime:
    """The browser runtime's switch, as ``BrowserRuntime`` holds it: the real
    ``TabGrants``, delegated to exactly as ``browser/service.py`` does."""

    def __init__(self) -> None:
        self.tab_grants = TabGrants()

    def auto_allowed(self) -> bool:
        return self.tab_grants.all_allowed

    def set_auto_allow(self, on: bool) -> bool:
        return self.tab_grants.grant_all(on)

    def active_tab_allowed(self) -> bool:
        return False


class _Conn:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []
        self.card = asyncio.Event()

    async def send(self, frame: dict) -> None:
        event = str(frame.get("event") or "")
        payload = dict(frame.get("payload") or {})
        self.frames.append((event, payload))
        if event == P.PANEL_EVENT_APPROVAL:
            self.card.set()

    def of(self, event: str) -> list[dict[str, Any]]:
        return [p for e, p in self.frames if e == event]


async def _no_models(conn: Any) -> None:
    """The model list rides every open; these tests have no providers to list."""


def _panel() -> tuple[PanelTurns, _Runtime]:
    runtime = _Runtime()
    turns = PanelTurns(SimpleNamespace(browser=runtime))
    turns._emit_models = _no_models
    return turns, runtime


# --------------------------------------------------------------------------- #
# SB4: one truth for the switch across windows
# --------------------------------------------------------------------------- #


def test_sb4_a_stale_window_cannot_turn_the_switch_back_on():
    async def drive() -> None:
        turns, runtime = _panel()
        conn_a, conn_b = _Conn(), _Conn()
        # Both windows open believing "on" (the stored setting), the switch on.
        await turns.handle(conn_a, P.PANEL_ACTION_OPEN, {"window": "win-a", "auto_allow": True})
        await turns.handle(conn_b, P.PANEL_ACTION_OPEN, {"window": "win-b", "auto_allow": True})
        assert runtime.auto_allowed() is True
        # Window A turns it off.
        await turns.handle(conn_a, P.PANEL_ACTION_AUTO_ALLOW, {"on": False, "window": "win-a"})
        assert runtime.auto_allowed() is False
        # Window B still believes "on" and switches tab: its open re-sends it.
        conn_b.frames.clear()
        await turns.handle(conn_b, P.PANEL_ACTION_OPEN, {"window": "win-b", "auto_allow": True})
        assert runtime.auto_allowed() is False, "a stale window's open turned the switch back on"
        # ...and B is told the switch's real value.
        states = conn_b.of(P.PANEL_EVENT_STATE)
        assert states and states[-1]["auto_allow"] is False, states
        # A PRESS turning it on again still works, from either window.
        await turns.handle(conn_b, P.PANEL_ACTION_AUTO_ALLOW, {"on": True, "window": "win-b"})
        assert runtime.auto_allowed() is True
        # And an open still restores the setting the last press chose (a
        # browser restart clears the daemon's flag; the user's choice stays).
        runtime.tab_grants.clear()
        await turns.handle(conn_a, P.PANEL_ACTION_OPEN, {"window": "win-a", "auto_allow": True})
        assert runtime.auto_allowed() is True
        # Turning a permission OFF always wins, even from an open.
        await turns.handle(conn_a, P.PANEL_ACTION_OPEN, {"window": "win-a", "auto_allow": False})
        assert runtime.auto_allowed() is False

    asyncio.run(drive())


def test_sb4_a_press_in_one_window_reaches_every_other_window():
    async def drive() -> None:
        turns, runtime = _panel()
        conn_a, conn_b = _Conn(), _Conn()
        await turns.handle(conn_a, P.PANEL_ACTION_OPEN, {"window": "win-a"})
        await turns.handle(conn_b, P.PANEL_ACTION_OPEN, {"window": "win-b"})
        conn_b.frames.clear()
        await turns.handle(conn_a, P.PANEL_ACTION_AUTO_ALLOW, {"on": True, "window": "win-a"})
        states = conn_b.of(P.PANEL_EVENT_STATE)
        assert states and states[-1]["auto_allow"] is True, "window B was never told the switch turned on"
        conn_b.frames.clear()
        await turns.handle(conn_a, P.PANEL_ACTION_AUTO_ALLOW, {"on": False, "window": "win-a"})
        states = conn_b.of(P.PANEL_EVENT_STATE)
        assert states and states[-1]["auto_allow"] is False, "window B was never told the switch turned off"
        # A closed window is told nothing more.
        await turns.handle(conn_b, P.PANEL_ACTION_CLOSE, {"window": "win-b"})
        conn_b.frames.clear()
        await turns.handle(conn_a, P.PANEL_ACTION_AUTO_ALLOW, {"on": True, "window": "win-a"})
        assert conn_b.of(P.PANEL_EVENT_STATE) == []

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# SB6 (G18): a risk card is answered once, never with "allow all"
# --------------------------------------------------------------------------- #


async def _risk_card(turns: PanelTurns, conn: _Conn, turn_id: str):
    """Put up a REAL risk card (``resolve_risk_ask``) for a running turn."""
    hold = asyncio.get_running_loop().create_future()
    turns._task = asyncio.ensure_future(hold)
    turns._conn = conn
    turns._turn_id = turn_id
    wait = asyncio.ensure_future(turns.resolve_risk_ask(
        SimpleNamespace(reason="this is a payment field"), SimpleNamespace(turn_id=turn_id),
        name="browser_type", args={"target": {"name": "Card number"}},
    ))
    await asyncio.wait_for(conn.card.wait(), BUDGET_S)
    card = conn.of(P.PANEL_EVENT_APPROVAL)[-1]
    assert card.get("floor") is True, card
    return hold, wait, card


def test_sb6_allow_all_on_a_risk_card_is_read_as_nothing(monkeypatch):
    monkeypatch.setattr(panel_mod, "RISK_ASK_TIMEOUT_S", 1_000_000.0)

    async def drive() -> None:
        turns, runtime = _panel()
        conn = _Conn()
        await turns.handle(conn, P.PANEL_ACTION_OPEN, {"window": "win-a"})
        hold, wait, card = await _risk_card(turns, conn, "panel_g20_risk")
        try:
            await turns.handle(
                conn, P.PANEL_ACTION_APPROVE, {"id": card["id"], "scope": "all", "window": "win-a"}
            )
            # A few turns of the loop (not a clock): enough for an answer that
            # was passed on to wake the card's wait.
            for _ in range(20):
                await asyncio.sleep(0)
            assert not wait.done(), "allow-all on a risk card ran the risky action"
            assert runtime.auto_allowed() is False, "allow-all on a risk card turned the switch on"
            errors = conn.of(P.PANEL_EVENT_ERROR)
            assert errors and errors[-1]["text"] == panel_mod.RISK_CARD_ALL_REFUSED, errors
            assert errors[-1].get("reason") == "risk_card_all", errors
            # The card stays: Allow once still answers it, once.
            await turns.handle(conn, P.PANEL_ACTION_APPROVE, {"id": card["id"], "window": "win-a"})
            verdict = await asyncio.wait_for(wait, BUDGET_S)
            assert getattr(verdict, "granted", verdict) is True
            assert runtime.auto_allowed() is False
        finally:
            hold.cancel()
            if not wait.done():
                wait.cancel()

    asyncio.run(drive())


def test_sb6_an_older_add_ons_allow_all_on_a_risk_card_does_not_turn_the_switch_on(monkeypatch):
    """An older add-on answers "Allow all page actions" with a plain Allow and
    then the switch. The Allow is the same as Allow once (that one action); the
    switch that follows it, from a panel that names no window, is refused."""
    monkeypatch.setattr(panel_mod, "RISK_ASK_TIMEOUT_S", 1_000_000.0)

    async def drive() -> None:
        turns, runtime = _panel()
        conn = _Conn()
        hold, wait, card = await _risk_card(turns, conn, "panel_g20_old")
        try:
            await turns.handle(conn, P.PANEL_ACTION_APPROVE, {"id": card["id"]})
            await asyncio.wait_for(wait, BUDGET_S)
            await turns.handle(conn, P.PANEL_ACTION_AUTO_ALLOW, {"on": True})
            assert runtime.auto_allowed() is False, "an older add-on's allow-all on a risk card turned the switch on"
            errors = conn.of(P.PANEL_EVENT_ERROR)
            assert errors and errors[-1]["text"] == panel_mod.RISK_CARD_ALL_OLDER, errors
            # The next press of the switch is the person's own and works.
            await turns.handle(conn, P.PANEL_ACTION_AUTO_ALLOW, {"on": True})
            assert runtime.auto_allowed() is True
        finally:
            hold.cancel()

    asyncio.run(drive())


def test_sb6_allow_all_on_an_ordinary_card_still_allows_and_turns_the_switch_on():
    from iron_jarvis.daemon.routes.chat import _approvals

    async def drive() -> None:
        turns, runtime = _panel()
        conn_a, conn_b = _Conn(), _Conn()
        await turns.handle(conn_a, P.PANEL_ACTION_OPEN, {"window": "win-a"})
        await turns.handle(conn_b, P.PANEL_ACTION_OPEN, {"window": "win-b"})
        hold = asyncio.get_running_loop().create_future()
        turns._task = asyncio.ensure_future(hold)
        turns._conn = conn_a
        turns._turn_id = "panel_g20_plain"
        registry = _approvals(SimpleNamespace(platform=turns.platform))
        ap_id, fut = registry.request("browser_click", {"target": {"name": "Next"}})
        try:
            await turns._translate(conn_a, "approval", {"id": ap_id, "tool": "browser_click", "args": {}})
            card = conn_a.of(P.PANEL_EVENT_APPROVAL)[-1]
            assert card.get("floor") is not True, card
            conn_b.frames.clear()
            await turns.handle(conn_a, P.PANEL_ACTION_APPROVE, {"id": ap_id, "scope": "all", "window": "win-a"})
            assert fut.done() and fut.result() == "once", "the ordinary card was not allowed"
            assert runtime.auto_allowed() is True, "allow-all did not turn the switch on"
            assert conn_b.of(P.PANEL_EVENT_STATE)[-1]["auto_allow"] is True
            assert conn_a.of(P.PANEL_EVENT_ERROR) == []
        finally:
            hold.cancel()
            registry.pop(ap_id)

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# The panel's side (source pins; the add-on has no JavaScript test runner)
# --------------------------------------------------------------------------- #


def _block(ts: str, anchor: str, end: str) -> str:
    start = ts.index(anchor)
    return ts[start: ts.index(end, start)]


def test_sb6_the_panel_draws_a_risk_card_with_two_buttons():
    ts = _src(PANEL_TS)
    card = _block(ts, "case PANEL_EVENT_APPROVAL: {", "return;\n    }")
    assert 'payload["floor"] === true' in card, card
    show = _block(ts, "function showApproval(", "\n}\n")
    # "Allow all page actions" is hidden on a risk card, shown on an ordinary one.
    assert "el.approveAlways.hidden = floor" in show, show
    # The allow-all answer is ONE judged action, never an Allow plus the switch.
    always = _block(ts, 'el.approveAlways?.addEventListener("click"', "\n});\n")
    assert 'scope: "all"' in always, always
    assert "setAuto(" not in always, always
    # The daemon's refusal leaves the card up and the turn running.
    err = _block(ts, 'if (reason === "risk_card_all")', "return;\n      }")
    assert "clearApproval" not in err and "setTurn" not in err, err


def test_sb4_the_panel_rereads_the_stored_switch():
    ts = _src(PANEL_TS)
    listener = _block(ts, "chrome.storage?.onChanged?.addListener?.(", "\n});\n")
    assert "STORAGE_AUTO_KEY" in listener and "storedAuto =" in listener, listener
    # Every open reads the stored setting first (the first one and each tab switch).
    send_open = _block(ts, "function sendOpen(", "\n}\n")
    assert "await loadAuto()" in send_open and "openParams(storedAuto)" in send_open, send_open
    tail = ts[ts.index("paintVersion();\nvoid refresh();"):]
    assert "post(PANEL_ACTION_OPEN" not in tail, "an open is sent without re-reading the stored switch"
    assert tail.count("sendOpen()") >= 2, tail
    # A press of the switch names its window (a newer add-on's press).
    set_auto = _block(ts, "async function setAuto(", "\n}\n")
    assert "window: PANEL_WINDOW" in set_auto, set_auto
