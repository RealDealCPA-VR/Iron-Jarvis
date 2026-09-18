"""v1.276.0 — /goal wave 3: the risk door's card reaches the sidebar; a named click reads first; a loading page is read twice.

Three items from the sidebar review, each verified against the code:

1. ``BrowserRuntime.approval_resolver`` was None in production, so the four
   floor cases (payment/password field, destructive words, a flagged page, an
   unreadable target) ended in a refusal telling the model to have the user
   approve "in Iron Jarvis" and call again — from the sidebar, a task that
   could not be completed. ``PanelTurns.resolve_risk_ask`` answers its own
   turn's asks through the sidebar's card.
2. ``prepare_action`` refused STALE_SNAPSHOT for a role/name or css target on a
   tab nobody had read; it reads first now (an element_id still refuses).
3. ``read_page_snapshot`` handed PAGE_NOT_READY straight back; it retries once.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.panel import RISK_ASK_TIMEOUT_S, PanelTurns
from iron_jarvis.browser.service import PAGE_NOT_READY_RETRY_S
from iron_jarvis.browser.tools import BrowserClickTool, BrowserReadPageTool, BrowserTypeTool
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core.turns import TURNS
from iron_jarvis.tools.base import ToolContext
from tests._fakes.panel_harness import RealApp
from tests.test_browser_actions_v1237 import ActRuntime, FakeApprovals, _ctx, _form_page, _peer, _ready, _run, _sent

ROOT = Path(__file__).resolve().parents[1]


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
# 1a. The door consults a resolver of any shape, with the context and the words
# --------------------------------------------------------------------------- #


def _ctx_for_turn(tmp_path: Path, turn_id: str) -> ToolContext:
    base = _ctx(tmp_path, run_id="run-risk")
    return ToolContext(
        workspace=base.workspace, session_id=base.session_id, agent_run_id=base.agent_run_id,
        config=base.config, event_bus=base.event_bus, engine=base.engine, turn_id=turn_id,
    )


def test_an_async_resolver_is_awaited_with_the_context_and_the_words(tmp_path):
    asked: list[dict[str, Any]] = []

    async def resolver(req, ctx, *, name, args):
        asked.append({"reason": req.reason, "turn_id": ctx.turn_id, "name": name, "args": args})
        return True

    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals(), approval_resolver=resolver)
    _ready(runtime, tmp_path=tmp_path)
    result = _run(BrowserClickTool(runtime), _ctx_for_turn(tmp_path, "panel_abc"), {"target": {"element_id": "e5"}})
    assert result.ok, result.error
    assert len(_sent(runtime, P.METHOD_CLICK)) == 1
    assert asked and asked[0]["name"] == "browser_click" and asked[0]["turn_id"] == "panel_abc"
    assert "delete" in asked[0]["reason"].lower()
    assert asked[0]["args"]["target"] == {"element_id": "e5"}


def test_a_refusing_async_resolver_keeps_the_key_off_the_page(tmp_path):
    async def resolver(req, ctx, *, name, args):
        return False

    approvals = FakeApprovals()
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=approvals, approval_resolver=resolver)
    _ready(runtime, tmp_path=tmp_path)
    result = _run(BrowserClickTool(runtime), _ctx_for_turn(tmp_path, "panel_abc"), {"target": {"element_id": "e5"}})
    assert not result.ok and "approval required" in (result.error or "")
    assert _sent(runtime, P.METHOD_CLICK) == []
    assert approvals.rows and approvals.rows[-1].status == "denied", "a refusal must be a denied row, not a pending one"


def test_a_one_argument_sync_resolver_still_works(tmp_path):
    seen: list[Any] = []
    runtime = ActRuntime(
        _peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals(),
        approval_resolver=lambda req: seen.append(req) or True,
    )
    _ready(runtime, tmp_path=tmp_path)
    assert _run(BrowserClickTool(runtime), _ctx(tmp_path), {"target": {"element_id": "e5"}}).ok
    assert len(seen) == 1


def test_a_resolver_that_raises_is_a_refusal_not_an_exception(tmp_path):
    async def resolver(req, ctx, *, name, args):
        raise RuntimeError("the panel went away")

    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals(), approval_resolver=resolver)
    _ready(runtime, tmp_path=tmp_path)
    result = _run(BrowserClickTool(runtime), _ctx_for_turn(tmp_path, "panel_abc"), {"target": {"element_id": "e5"}})
    assert not result.ok and "approval required" in (result.error or "")
    assert _sent(runtime, P.METHOD_CLICK) == []


# --------------------------------------------------------------------------- #
# 1b. The panel's resolver: a card in the sidebar, for its own turn only
# --------------------------------------------------------------------------- #


class _Conn:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.frames.append(frame)

    def cards(self) -> list[dict]:
        return [f["payload"] for f in self.frames if f.get("event") == P.PANEL_EVENT_APPROVAL]


def _turns_with_running_turn(turn_id: str = "panel_t1") -> tuple[PanelTurns, _Conn, asyncio.Task]:
    turns = PanelTurns(SimpleNamespace(browser=None))
    conn = _Conn()
    turns._conn = conn
    turns._turn_id = turn_id
    task = asyncio.ensure_future(asyncio.sleep(30))
    turns._task = task
    return turns, conn, task


def _req(reason: str) -> SimpleNamespace:
    return SimpleNamespace(id="appr_1", reason=reason, run_id="chat", status="pending")


def test_the_panel_asks_its_own_turn_through_a_card_and_allow_grants_once():
    async def drive():
        turns, conn, task = _turns_with_running_turn()
        try:
            ask = asyncio.ensure_future(turns.resolve_risk_ask(
                _req("destructive/transactional action ('delete')"),
                SimpleNamespace(turn_id="panel_t1"),
                name="browser_click", args={"target": {"role": "button", "name": "Delete account"}},
            ))
            for _ in range(200):
                if conn.cards():
                    break
                await asyncio.sleep(0.01)
            cards = conn.cards()
            assert cards, "no card reached the sidebar"
            card = cards[0]
            assert card["floor"] is True and card["tool"] == "browser_click"
            assert "click 'Delete account'" in card["text"] and "delete" in card["text"].lower()
            assert card["id"] in turns._offered, "the panel would refuse its own card as not-this-panel"
            await turns._decide(conn, P.PANEL_ACTION_APPROVE, card["id"])
            assert await ask is True
        finally:
            task.cancel()

    asyncio.run(drive())


def test_deny_refuses_and_a_foreign_turn_or_an_idle_panel_gets_no_card():
    async def drive():
        turns, conn, task = _turns_with_running_turn()
        try:
            ask = asyncio.ensure_future(turns.resolve_risk_ask(
                _req("the page marks this field as sensitive"), SimpleNamespace(turn_id="panel_t1"),
                name="browser_type", args={"target": {"name": "Card number"}, "text": "***REDACTED***"},
            ))
            for _ in range(200):
                if conn.cards():
                    break
                await asyncio.sleep(0.01)
            assert conn.cards()
            await turns._decide(conn, P.PANEL_ACTION_DENY, conn.cards()[0]["id"])
            assert await ask is False
            # Another turn's ask: no card, not granted.
            before = len(conn.frames)
            assert await turns.resolve_risk_ask(_req("x"), SimpleNamespace(turn_id="panel_other"), name="browser_click", args={}) is False
            assert len(conn.frames) == before
        finally:
            task.cancel()
        # An idle panel (no running turn) grants nothing and sends nothing.
        idle = PanelTurns(SimpleNamespace(browser=None))
        idle._conn = conn
        assert await idle.resolve_risk_ask(_req("x"), SimpleNamespace(turn_id=""), name="browser_click", args={}) is False

    asyncio.run(drive())


def test_the_wait_is_bounded_and_a_timeout_is_a_refusal(monkeypatch):
    import iron_jarvis.browser.panel as panel_mod

    monkeypatch.setattr(panel_mod, "RISK_ASK_TIMEOUT_S", 0.05)

    async def drive():
        turns, conn, task = _turns_with_running_turn()
        try:
            assert await turns.resolve_risk_ask(_req("x"), SimpleNamespace(turn_id="panel_t1"), name="browser_click", args={}) is False
            assert conn.cards(), "the card was never shown"
        finally:
            task.cancel()

    asyncio.run(drive())
    assert RISK_ASK_TIMEOUT_S == 300.0


def test_install_wires_the_panels_resolver_and_both_lanes_pass_the_turn_id(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        resolver = app.platform.browser.approval_resolver
        assert resolver is not None and getattr(resolver, "__name__", "") == "resolve_risk_ask"
    for rel in ("daemon/routes/chat.py", "daemon/chat_turn.py"):
        src = _src(ROOT / "src" / "iron_jarvis" / rel)
        assert 'turn_id=str(getattr(body, "turn_id", "") or "")' in src, rel
    assert "turn_id: str = \"\"" in _src(ROOT / "src" / "iron_jarvis" / "tools" / "base.py")


# --------------------------------------------------------------------------- #
# 2. A named click reads first
# --------------------------------------------------------------------------- #


def test_a_role_name_click_on_an_unread_tab_reads_first_and_runs(tmp_path):
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals())
    # NO _ready(): nobody has read the tab.
    result = _run(BrowserClickTool(runtime), _ctx(tmp_path), {"target": {"role": "button", "name": "Sign in"}})
    assert result.ok, result.error
    methods = [name for name, _ in runtime.calls]
    assert P.METHOD_READ_PAGE in methods and methods.index(P.METHOD_READ_PAGE) < methods.index(P.METHOD_CLICK)


def test_an_element_id_click_on_an_unread_tab_still_refuses(tmp_path):
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals())
    result = _run(BrowserClickTool(runtime), _ctx(tmp_path), {"target": {"element_id": "e3"}})
    assert not result.ok and result.data["code"] == BrowserErrorCode.STALE_SNAPSHOT.value
    assert _sent(runtime, P.METHOD_CLICK) == [] and _sent(runtime, P.METHOD_READ_PAGE) == []


def test_the_auto_read_feeds_the_risk_door_the_real_control(tmp_path):
    """A password field addressed by role and name on an unread tab still escalates."""
    approvals = FakeApprovals()
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=approvals)
    result = _run(BrowserTypeTool(runtime), _ctx(tmp_path), {"target": {"role": "textbox", "name": "Password"}, "text": "hunter2"})
    assert not result.ok and "approval required" in (result.error or "")
    assert _sent(runtime, P.METHOD_TYPE_TEXT) == []
    assert len(approvals.rows) == 1


# --------------------------------------------------------------------------- #
# 3. A loading page is read twice
# --------------------------------------------------------------------------- #


def test_page_not_ready_is_retried_once(tmp_path):
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals())
    real = runtime.command
    calls = {"n": 0}

    async def flaky(method, params):
        if method == P.METHOD_READ_PAGE:
            calls["n"] += 1
            if calls["n"] == 1:
                raise BrowserError(BrowserErrorCode.PAGE_NOT_READY, tab_id=42)
        return await real(method, params)

    runtime.command = flaky  # type: ignore[method-assign]
    result = _run(BrowserReadPageTool(runtime), _ctx(tmp_path), {})
    assert result.ok, result.error
    assert calls["n"] == 2
    assert PAGE_NOT_READY_RETRY_S < 1.0


def test_a_page_still_loading_after_the_retry_keeps_its_own_words(tmp_path):
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals())
    calls = {"n": 0}

    async def never_ready(method, params):
        calls["n"] += 1
        raise BrowserError(BrowserErrorCode.PAGE_NOT_READY, tab_id=42)

    runtime.command = never_ready  # type: ignore[method-assign]
    result = _run(BrowserReadPageTool(runtime), _ctx(tmp_path), {})
    assert not result.ok and result.data["code"] == BrowserErrorCode.PAGE_NOT_READY.value
    assert calls["n"] == 2, "exactly one retry"
