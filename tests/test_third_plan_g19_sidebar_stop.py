"""Third north-star fix plan, group 19 (Stop is written down), the browser
sidebar's own Stop: SB2, SB5, SB13.

SB2: Stop (or Esc) did not end a sidebar reply waiting at a RISK card (a
payment or password field, a destructive word, a flagged page).
``PanelTurns.resolve_risk_ask`` waited for the card's answer for up to five
minutes with no look at Stop, the panel stayed "running", and Allow pressed
after Stop still did the action. Now the wait ends the moment the reply is
stopped (it waits on the answer and the turn's stop together), the card's id
is withdrawn, the door refuses with its "stopped" words, the reply ends as
stopped (the panel's ``done`` frame clears the card) with what the user read
kept, and a late Allow, on a risk card or an ordinary one, answers nothing.

SB5: the panel's Stop, Allow, Allow-all and Deny buttons did not check that
the press reached Iron Jarvis. The add-on has no JavaScript test runner, so
this part is pinned by reading ``sidepanel.ts`` (and type-checked with
``pnpm run typecheck``), never run in a browser.

SB13: with the sidebar open in two browser windows (one add-on connection,
two panels), closing one window stopped the reply the other was showing,
which then read as finished. Now each panel names its window; a window
closing stops the reply only when no other window of the sidebar is still
open, and a reply stopped that way is written to the conversation as stopped.

Driven through the REAL ``PanelTurns`` and, for SB2, the REAL browser tool
and risk door (``BrowserClickTool`` over the scripted page of
``test_browser_actions_v1237``); the chat lane is a stand-in at its seam that
keeps the lane's contract (a stopped turn sends no ``done``). Waits are
bounded budgets that fail by name; nothing asserts an elapsed time.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.browser.tools import BrowserClickTool
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core import risk_door
from iron_jarvis.core.turns import TURNS
from iron_jarvis.tools.base import ToolContext
from tests.test_browser_actions_v1237 import ActRuntime, FakeApprovals, _ctx, _form_page, _peer, _ready, _sent

ROOT = Path(__file__).resolve().parents[1]
PANEL_TS = ROOT / "extensions" / "chrome" / "src" / "sidepanel" / "sidepanel.ts"

#: A budget for something that must happen once Stop is pressed. Not a
#: deadline that is measured: the risk wait's own clock is set far beyond it,
#: so only the Stop can end the wait inside it, and a miss fails by name.
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


class _Conn:
    """The add-on's one socket, as the panel sees it: every frame it was sent."""

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

    def index(self, event: str, **match: Any) -> int:
        for n, (e, p) in enumerate(self.frames):
            if e == event and all(p.get(k) == v for k, v in match.items()):
                return n
        raise AssertionError(f"no {event} {match} frame; sent: {[e for e, _ in self.frames]}")


async def _no_models(conn: Any) -> None:
    """The model list rides every open; these tests have no providers to list."""


def _ctx_for_turn(tmp_path: Path, turn_id: str) -> ToolContext:
    base = _ctx(tmp_path, run_id="run-g19")
    return ToolContext(
        workspace=base.workspace, session_id=base.session_id, agent_run_id=base.agent_run_id,
        config=base.config, event_bus=base.event_bus, engine=base.engine, turn_id=turn_id,
    )


# --------------------------------------------------------------------------- #
# SB2: Stop ends a risk card's wait; a late Allow does nothing
# --------------------------------------------------------------------------- #


def test_sb2_stop_ends_a_risk_card_wait_and_a_late_allow_does_not_act(tmp_path, monkeypatch):
    # The risk wait's own clock is far past the budget: only the Stop can end it.
    monkeypatch.setattr(panel_mod, "RISK_ASK_TIMEOUT_S", 1_000_000.0)
    approvals = FakeApprovals()
    turns = PanelTurns(SimpleNamespace(browser=None))
    runtime = ActRuntime(
        _peer(_form_page()), policy=ComputerUsePolicy(), approvals=approvals,
        approval_resolver=turns.resolve_risk_ask,
    )
    _ready(runtime, tmp_path=tmp_path)
    results: list[Any] = []

    async def lane(platform, personas, body, **kw):
        """The chat lane at its seam: a few words, then the page action (the
        REAL tool and risk door), then the lane's contract: a stopped turn
        returns with no ``done``."""
        handle = TURNS.register(body.turn_id)

        async def gen():
            try:
                yield 'event: token\ndata: {"text": "Looking at the page."}\n\n'
                result = await BrowserClickTool(runtime).execute(
                    {"target": {"element_id": "e5"}}, _ctx_for_turn(tmp_path, body.turn_id)
                )
                results.append(result)
                if handle.stopped:
                    return
                yield 'event: done\ndata: {"reply": "Looking at the page. Done."}\n\n'
            finally:
                TURNS.release(body.turn_id, handle)

        return gen()

    import iron_jarvis.daemon.chat_stream as chat_stream

    monkeypatch.setattr(chat_stream, "stream_chat_turn", lane)

    async def drive() -> None:
        conn = _Conn()
        await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "delete my account on this page"})
        task = turns._task
        assert task is not None, "the send started no turn"
        await asyncio.wait_for(conn.card.wait(), BUDGET_S)
        card = conn.of(P.PANEL_EVENT_APPROVAL)[-1]
        assert card.get("floor") is True, card
        assert not task.done()

        await turns.handle(conn, P.PANEL_ACTION_STOP, {})
        done, _ = await asyncio.wait({task}, timeout=BUDGET_S)
        if task not in done:
            task.cancel()
            raise AssertionError(
                "Stop did not end the risk card's wait: the sidebar reply is still"
                " waiting for the card's answer"
            )

        # The door refused with the stopped words, and nothing reached the page.
        assert results and results[0].ok is False, results
        assert risk_door.STOPPED_TAIL in (results[0].error or ""), results[0].error
        assert _sent(runtime, P.METHOD_CLICK) == []
        assert approvals.rows and approvals.rows[-1].status == "denied", "the card's row stayed open"
        # The reply ended as stopped AFTER the card: the panel's `done` clears it.
        stopped_at = conn.index(P.PANEL_EVENT_DONE, reason="stopped")
        assert stopped_at > conn.index(P.PANEL_EVENT_APPROVAL)
        # What the user read is kept.
        assert turns._history == [
            {"role": "user", "content": "delete my account on this page"},
            {"role": "assistant", "content": "Looking at the page."},
        ], turns._history

        # A late Allow for the withdrawn card answers nothing and says so.
        await turns.handle(conn, P.PANEL_ACTION_APPROVE, {"id": card["id"]})
        await asyncio.sleep(0)
        assert _sent(runtime, P.METHOD_CLICK) == []
        errors = [p.get("text") for p in conn.of(P.PANEL_EVENT_ERROR)]
        assert panel_mod.CARD_WITHDRAWN in errors, errors

    asyncio.run(drive())


def test_sb2_an_allow_pressed_after_stop_is_not_passed_to_a_waiting_card():
    """The ORDINARY card's wait (the chat lane's) notices Stop at its next look,
    up to a second later; an Allow pressed in between must not reach it, and
    neither may the allow-everything switch."""
    from iron_jarvis.daemon.routes.chat import _approvals

    async def drive() -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        conn = _Conn()
        turns._conn = conn
        turns._turn_id = "panel_g19"
        handle = TURNS.register("panel_g19")
        hold = asyncio.get_running_loop().create_future()
        turns._task = asyncio.ensure_future(hold)
        try:
            registry = _approvals(SimpleNamespace(platform=turns.platform))
            ap_id, fut = registry.request("browser_click", {"target": {"name": "Next"}})
            await turns._translate(conn, "approval", {"id": ap_id, "tool": "browser_click", "args": {}})
            await turns.handle(conn, P.PANEL_ACTION_STOP, {})
            assert handle.stopped
            await turns.handle(conn, P.PANEL_ACTION_APPROVE, {"id": ap_id})
            assert not fut.done(), "an Allow pressed after Stop was passed to the waiting card"
            # The switch turns on (a stand-in for the browser runtime's flag)...
            turns._set_auto_allow = lambda on: bool(on)
            await turns._auto_allow(conn, {"on": True})
            assert not fut.done(), "the switch answered a card of a stopped reply"
            assert panel_mod.CARD_WITHDRAWN in [p.get("text") for p in conn.of(P.PANEL_EVENT_ERROR)]
            registry.pop(ap_id)
        finally:
            hold.cancel()
            TURNS.release("panel_g19", handle)

    asyncio.run(drive())


def test_sb2_a_stop_before_the_risk_card_puts_up_no_card():
    async def drive() -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        conn = _Conn()
        turns._conn = conn
        turns._turn_id = "panel_g19b"
        handle = TURNS.register("panel_g19b")
        hold = asyncio.get_running_loop().create_future()
        turns._task = asyncio.ensure_future(hold)
        try:
            handle.stop()
            verdict = await asyncio.wait_for(
                turns.resolve_risk_ask(
                    SimpleNamespace(reason="a payment field"), SimpleNamespace(turn_id="panel_g19b"),
                    name="browser_type", args={},
                ),
                BUDGET_S,
            )
            assert getattr(verdict, "granted", verdict) is False
            assert getattr(verdict, "tail", "") == risk_door.STOPPED_TAIL
            assert conn.of(P.PANEL_EVENT_APPROVAL) == []
        finally:
            hold.cancel()
            TURNS.release("panel_g19b", handle)

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# SB5: a press that did not reach Iron Jarvis says so (source pins)
# --------------------------------------------------------------------------- #


def _handler(ts: str, anchor: str) -> str:
    start = ts.index(anchor)
    return ts[start: ts.index("\n});\n", start)]


@pytest.mark.parametrize(
    "anchor, action",
    [
        ('el.stop?.addEventListener("click"', "PANEL_ACTION_STOP"),
        ('el.approve?.addEventListener("click"', "PANEL_ACTION_APPROVE"),
        ('el.approveAlways?.addEventListener("click"', "PANEL_ACTION_APPROVE"),
        ('el.deny?.addEventListener("click"', "PANEL_ACTION_DENY"),
    ],
)
def test_sb5_each_press_reads_whether_it_was_sent(anchor: str, action: str):
    ts = _src(PANEL_TS)
    body = _handler(ts, anchor)
    if action == "PANEL_ACTION_STOP":
        assert "post(PANEL_ACTION_STOP)" in body
        call = body
    else:
        assert "answerCard(" in body, body
        helper = ts[ts.index("function answerCard("):]
        call = helper[: helper.index("\n}\n")]
        assert f"{action}" in body or f"{action}" in call
    # The press reads post()'s answer, and has a branch for "not sent".
    assert re.search(r"\.then\(\(sent\) =>", call), call
    assert "if (!sent)" in call, call
    assert "say(\"error\", NOT_CONNECTED)" in call, call
    # Nothing is hidden or claimed before the answer.
    before = body[: body.index("post(")] if "post(" in body else body
    assert "clearApproval()" not in before and "Stop requested" not in before, body


def test_sb5_the_card_stays_up_when_the_answer_did_not_go_out():
    ts = _src(PANEL_TS)
    helper = ts[ts.index("function answerCard("):]
    helper = helper[: helper.index("\n}\n")]
    not_sent = helper[helper.index("if (!sent)"):]
    not_sent = not_sent[: not_sent.index("return false;")]
    assert "clearApproval" not in not_sent, helper
    # Hidden only once the press went out, and only if it is still the same card.
    sent_part = helper[helper.index("return false;"):]
    assert "pendingApprovalId === id" in sent_part and "clearApproval()" in sent_part, helper
    # The stop line is said only after the press went out.
    stop = _handler(ts, 'el.stop?.addEventListener("click"')
    assert stop.index("if (!sent)") < stop.index("Stop requested"), stop
    # One sentence for "not connected", the one Send already says.
    assert 'const NOT_CONNECTED = "That did not reach Iron Jarvis — this browser is not connected.";' in ts


# --------------------------------------------------------------------------- #
# SB13: closing one of two windows does not stop the other's reply
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sockets", ["one socket", "two sockets"])
def test_sb13_closing_one_window_leaves_the_others_reply_running(sockets: str):
    async def drive() -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        conn_a = _Conn()
        conn_b = conn_a if sockets == "one socket" else _Conn()
        turns._emit_models = _no_models
        await turns.handle(conn_a, P.PANEL_ACTION_OPEN, {"window": "win-a"})
        await turns.handle(conn_b, P.PANEL_ACTION_OPEN, {"window": "win-b"})
        turns._conn = conn_a
        turns._turn_id = "panel_g19c"
        handle = TURNS.register("panel_g19c")
        hold = asyncio.get_running_loop().create_future()
        turns._task = asyncio.ensure_future(hold)
        try:
            await turns.handle(conn_b, P.PANEL_ACTION_CLOSE, {"window": "win-b"})
            assert not handle.stopped, "closing window B stopped the reply window A is showing"
            # The last window closing still stops a reply narrating to nobody.
            await turns.handle(conn_a, P.PANEL_ACTION_CLOSE, {"window": "win-a"})
            assert handle.stopped, "closing the last window left the reply running"
        finally:
            hold.cancel()
            TURNS.release("panel_g19c", handle)

    asyncio.run(drive())


def test_sb13_a_close_that_names_no_window_still_stops_as_before():
    async def drive() -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        turns._turn_id = "panel_g19d"
        handle = TURNS.register("panel_g19d")
        try:
            await turns.handle(_Conn(), P.PANEL_ACTION_CLOSE, {})
            assert handle.stopped
        finally:
            TURNS.release("panel_g19d", handle)

    asyncio.run(drive())


def test_sb13_a_reply_stopped_by_its_last_window_closing_is_written_as_stopped(monkeypatch):
    async def lane(platform, personas, body, **kw):
        handle = TURNS.register(body.turn_id)

        async def gen():
            try:
                yield 'event: token\ndata: {"text": "Half of it"}\n\n'
                await handle.wait_stopped()
                # The lane's contract: a stopped turn sends no `done`.
            finally:
                TURNS.release(body.turn_id, handle)

        return gen()

    import iron_jarvis.daemon.chat_stream as chat_stream

    monkeypatch.setattr(chat_stream, "stream_chat_turn", lane)

    async def drive() -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        conn = _Conn()
        turns._emit_models = _no_models
        await turns.handle(conn, P.PANEL_ACTION_OPEN, {"window": "win-a"})
        await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "summarise the page", "window": "win-a"})
        task = turns._task
        assert task is not None
        for _ in range(200):
            if conn.of(P.PANEL_EVENT_DELTA) and TURNS.handle(turns._turn_id) is not None:
                break
            await asyncio.sleep(0)
        await turns.handle(conn, P.PANEL_ACTION_CLOSE, {"window": "win-a"})
        done, _ = await asyncio.wait({task}, timeout=BUDGET_S)
        assert task in done, "closing the last window did not end the reply"
        assert conn.of(P.PANEL_EVENT_DONE)[-1].get("reason") == "stopped"
        assert turns._history[-1] == {
            "role": "assistant", "content": f"Half of it\n\n{panel_mod.STOPPED_ON_CLOSE}",
        }, turns._history

    asyncio.run(drive())


def test_sb13_the_panel_names_its_window_and_shows_a_stop_it_did_not_press():
    ts = _src(PANEL_TS)
    assert "const PANEL_WINDOW =" in ts
    open_params = ts[ts.index("export function openParams"):]
    open_params = open_params[: open_params.index("\n}\n")]
    assert 'window: PANEL_WINDOW' in open_params or 'params["window"] = PANEL_WINDOW' in open_params
    send = ts[ts.index("function sendNow"):]
    send = send[: send.index("\n}\n")]
    assert "window: PANEL_WINDOW" in send
    close = ts[ts.index('window.addEventListener("pagehide"'):]
    close = close[: close.index("\n});\n")]
    assert "post(PANEL_ACTION_CLOSE, { window: PANEL_WINDOW })" in close
    done = ts[ts.index("case PANEL_EVENT_DONE: {"):]
    done = done[: done.index("return;\n    }")]
    assert 'payload["reason"] === "stopped"' in done and 'say("tool", "Stopped.")' in done
