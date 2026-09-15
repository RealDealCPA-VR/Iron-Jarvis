"""v1.266.0 — one approval per tab, for as long as the tab is open.

THE REPORT: "it seems I need to keep on providing approvals over and over. It
should just take one approval per tab. The approval in that tab lasts as long as
the tab is open."

WHAT WAS TRUE. Every page action in the sidebar is ask-armed (v1.262.0), so the
chat lane raised a card per call; "Allow for this task" widened the grant for the
rest of THAT turn, and every Send is a fresh turn. Ten messages in one tab were
ten cards.

WHAT IS PINNED, against the REAL app (``create_app``, install auth on, a paired
add-on socket, the real panel, the real chat lane; only the model and the tool
registry are doubles):

1. "Allow for this tab" covers the rest of the turn AND the next message in that
   tab — no second card — and the panel's header is told (``tab_allowed``).
2. A grant is for ONE tab: a page action addressed to another tab still cards.
3. The grant ends when the tab closes (the add-on's ``tab_removed`` event).
4. The grant ends when the browser session changes (``browser_session`` in
   ``browser.hello``), and survives a hello that repeats the same session — a
   service-worker reconnect must not put the cards back.
5. Forget ends every grant.
6. THE RISK GATE IS UNTOUCHED: an acting tool on a granted tab still stops at its
   own card for a decision that is the user's — driven through the real
   ``_ActingTool.execute`` with the risk door's own approvals table.
7. The store itself: key normalisation, revoke, clear, and the session rule.

Nothing here asserts a duration; every wait is bounded and fails with a name.
"""

from __future__ import annotations

from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.grants import TabGrants, is_acting_browser_tool, tab_key
from iron_jarvis.browser.tools import BrowserPressKeyTool
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core.approvals import DECISIONS
from iron_jarvis.core.turns import TURNS
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests._fakes.panel_harness import RealApp, _headers, _wait_for
from tests.test_browser_actions_v1237 import (
    ActRuntime,
    FakeApprovals,
    _ctx,
    _form_page,
    _peer,
    _ready,
    _run,
    _sent,
)
from tests.test_browser_agent_v1262 import _NEUTRAL, _tool_names

PINNED = "lgihfomaieifpnemakmpadmggjnoojmm"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


# --------------------------------------------------------------------------- #
# Drivers
# --------------------------------------------------------------------------- #


def _final(text: str, calls: list[ToolCall] | None = None) -> dict:
    return {
        "type": "final",
        "response": LLMResponse(text=text, tool_calls=calls or [], usage={}),
        "provider": "mock",
        "model": "mock",
    }


def _script(app: RealApp, rounds: list) -> dict:
    """Round n: a list of calls, or a STRING (a text answer that ends the turn).

    Unlike the v1262 helper, a turn can be ended ON PURPOSE with words, so the
    next Send starts on the next round rather than on the lane's empty-answer
    nudge. Past the script: "All done."
    """
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        n = state["n"]
        state["n"] += 1
        step = rounds[n] if n < len(rounds) else "All done."
        if isinstance(step, str):
            yield {"type": "text", "text": step}
            yield _final(step)
        else:
            yield _final("", step)

    app.platform.router.stream = fake_stream
    return state


def _click(cid: str, element_id: int, tab_id: int | None = None) -> ToolCall:
    args: dict[str, Any] = {"element_id": element_id}
    if tab_id is not None:
        args["tab_id"] = tab_id
    return ToolCall(id=cid, name="browser_click", arguments=args)


def _look_at(app: RealApp, tab_id: int) -> None:
    """The add-on says which tab the user is looking at (``tab_activated``)."""
    app.ws.send_json(
        P.event_frame(
            f"evt_look_{tab_id}",
            P.EVENT_TAB_ACTIVATED,
            {"tab_id": tab_id, "title": f"Tab {tab_id}", "url": "https://example.test/"},
        )
    )
    backend = app.platform.browser.backend
    _wait_for(
        lambda: (backend.active_tab or {}).get("tab_id") == tab_id,
        f"the daemon never recorded tab {tab_id} as active",
    )


def _close_tab(app: RealApp, tab_id: int) -> None:
    app.ws.send_json(P.event_frame(f"evt_gone_{tab_id}", P.EVENT_TAB_REMOVED, {"tab_id": tab_id}))
    grants = app.platform.browser.tab_grants
    _wait_for(lambda: not grants.covers(tab_id), f"tab {tab_id}'s grant never ended")


def _hello(app: RealApp, session: str) -> None:
    app.ws.send_json(
        {
            "type": P.FRAME_HELLO,
            "extension_id": PINNED,
            "extension_version": "1.266.0",
            "host_permission": True,
            "browser_session": session,
        }
    )
    grants = app.platform.browser.tab_grants
    _wait_for(lambda: grants.session == session, "the daemon never recorded the browser session")


def _cards(app: RealApp) -> list[dict]:
    return [p for e, p in app.panel.events() if e == P.PANEL_EVENT_APPROVAL]


def _states(app: RealApp) -> list[dict]:
    return [p for e, p in app.panel.events() if e == P.PANEL_EVENT_STATE]


# --------------------------------------------------------------------------- #
# 1. One approval per tab, across messages
# --------------------------------------------------------------------------- #


def test_allow_for_this_tab_covers_this_message_and_the_next(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        calls = app.stub_tools()
        _script(app, [
            [_click("c1", 1)],
            [_click("c2", 2)],
            "Done with the first message.",
            [_click("c3", 3)],
        ])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        first = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")
        assert first["tool"] == "browser_click"
        app.panel.send(P.PANEL_ACTION_APPROVE, id=first["id"], scope="tab")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the first message never finished")
        assert len(_cards(app)) == 1, "the second click in the same tab raised a card"
        assert _tool_names(calls) == ["browser_click", "browser_click"]
        assert app.platform.browser.tab_grants.tab_ids() == ["7"]
        # The header was told, from a frame emitted AFTER the grant was recorded.
        assert any(s.get("tab_allowed") is True for s in _states(app)), _states(app)

        # THE NEXT MESSAGE. This is the report: it used to ask again here.
        app.panel.send(P.PANEL_ACTION_SEND, text="now press the other one")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the second message never finished", nth=2)
        assert len(_cards(app)) == 1, "a new message in the granted tab raised a card"
        assert _tool_names(calls) == ["browser_click"] * 3
        assert not any(c["kw"].get("deny_reason") for c in calls)


def test_the_decision_word_is_part_of_the_registry_vocabulary():
    assert "tab" in DECISIONS


# --------------------------------------------------------------------------- #
# 2. One tab, not every tab
# --------------------------------------------------------------------------- #


def test_a_grant_is_for_one_tab_only(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        calls = app.stub_tools()
        _script(app, [
            [_click("c1", 1)],
            [_click("c2", 2, tab_id=8)],
        ])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        first = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")
        app.panel.send(P.PANEL_ACTION_APPROVE, id=first["id"], scope="tab")
        second = app.panel.wait_for(
            P.PANEL_EVENT_APPROVAL, "a click in ANOTHER tab raised no card", nth=2
        )
        assert second["id"] != first["id"]
        app.panel.send(P.PANEL_ACTION_DENY, id=second["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        reasons = [c["kw"].get("deny_reason") for c in calls]
        assert reasons[0] in (None, "") and reasons[1], reasons
        assert app.platform.browser.tab_grants.tab_ids() == ["7"], "denying tab 8 must not touch tab 7"


# --------------------------------------------------------------------------- #
# 3. The tab closes
# --------------------------------------------------------------------------- #


def test_closing_the_tab_ends_its_grant(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        calls = app.stub_tools()
        _script(app, [
            [_click("c1", 1)],
            "First message done.",
            [_click("c2", 2, tab_id=7)],
        ])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        first = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")
        app.panel.send(P.PANEL_ACTION_APPROVE, id=first["id"], scope="tab")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the first message never finished")
        assert app.platform.browser.tab_grants.covers(7)

        _close_tab(app, 7)
        assert app.platform.browser.tab_grants.tab_ids() == []
        # The snapshot cache and the active tab let go of it too.
        assert app.platform.browser.backend.active_tab is None

        app.panel.send(P.PANEL_ACTION_SEND, text="press it again")
        again = app.panel.wait_for(
            P.PANEL_EVENT_APPROVAL, "a click in the CLOSED tab's id raised no card", nth=2
        )
        app.panel.send(P.PANEL_ACTION_DENY, id=again["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the second message never finished", nth=2)
        assert _tool_names(calls) == ["browser_click", "browser_click"]


# --------------------------------------------------------------------------- #
# 4. The browser restarts (a new session) — but a reconnect is not a restart
# --------------------------------------------------------------------------- #


def test_a_new_browser_session_ends_every_grant_but_a_repeat_hello_keeps_them(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _hello(app, "session-A")
        _look_at(app, 7)
        app.stub_tools()
        _script(app, [[_click("c1", 1)], "Done."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        first = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the click never raised a card")
        app.panel.send(P.PANEL_ACTION_APPROVE, id=first["id"], scope="tab")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        grants = app.platform.browser.tab_grants
        assert grants.tab_ids() == ["7"]

        # A service-worker reconnect greets again with the SAME session: kept.
        _hello(app, "session-A")
        assert grants.tab_ids() == ["7"], "a reconnect in the same browser session must keep the grant"
        # An older add-on that names no session: also kept (nothing to compare).
        app.ws.send_json(
            {"type": P.FRAME_HELLO, "extension_id": PINNED, "extension_version": "1.265.0", "host_permission": True}
        )
        _wait_for(lambda: app.platform.browser.backend.connection.extension_version == "1.265.0", "hello not seen")
        assert grants.tab_ids() == ["7"]

        # The browser restarted: new session, new tab ids, no grants.
        _hello(app, "session-B")
        assert grants.tab_ids() == [], "a new browser session must end every grant"


# --------------------------------------------------------------------------- #
# 5. Forget
# --------------------------------------------------------------------------- #


def test_forget_ends_every_grant(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        grants = app.platform.browser.tab_grants
        grants.grant(7)
        grants.grant(9)
        r = app.client.post("/browser/forget", headers=_headers())
        assert r.status_code == 200 and r.json()["forgotten"] is True, r.text
        assert grants.tab_ids() == []


# --------------------------------------------------------------------------- #
# 6. The risk gate is untouched
# --------------------------------------------------------------------------- #


def test_a_granted_tab_does_not_skip_the_risk_gate(tmp_path):
    """Enter with no target reaches the risk door and asks (v1.237.0); a tab grant changes nothing there.

    The tab grant answers ONE question — the lane's ordinary "may this call run
    without a card" — and the risk door inside the tool answers a different one.
    Driven through the real ``_ActingTool.execute`` with the door's own approvals
    table, on a runtime whose grant covers the tab: the key still does not reach
    the page, and the door still files its card.
    """
    approvals = FakeApprovals()
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=approvals)
    tab_id = _ready(runtime, tmp_path=tmp_path)
    runtime.tab_grants = TabGrants()
    runtime.tab_grants.grant(tab_id if tab_id is not None else 1)
    assert runtime.tab_grants.tab_ids(), "premise: the tab is granted"

    result = _run(BrowserPressKeyTool(runtime), _ctx(tmp_path), {"key": "Enter"})

    assert not result.ok
    assert "approval required" in (result.error or "")
    assert _sent(runtime, P.METHOD_PRESS_KEY) == [], "a key reached the page on the strength of a tab grant"
    assert len(approvals.rows) == 1


def test_the_risk_door_never_reads_the_grants():
    """Source pin for the boundary the case above proves: neither the tool base nor the risk module knows grants."""
    import inspect

    from iron_jarvis.browser import risk, tools

    assert "tab_grant" not in inspect.getsource(tools), "the acting tools must not consult tab grants"
    assert "tab_grant" not in inspect.getsource(risk) and "TabGrants" not in inspect.getsource(risk)


# --------------------------------------------------------------------------- #
# 7. The store
# --------------------------------------------------------------------------- #


def test_tab_key_normalises_the_spellings_of_one_tab():
    assert tab_key(7) == tab_key("7") == tab_key(7.0) == "7"
    assert tab_key(None) == tab_key("") == ""
    assert tab_key(" abc ") == "abc"


def test_grant_covers_revoke_clear():
    g = TabGrants()
    assert g.grant(None) == "" and g.tab_ids() == []
    assert g.grant(7) == "7"
    assert g.covers("7") and g.covers(7) and not g.covers(8) and not g.covers(None)
    assert g.revoke(8) is False and g.revoke(7) is True and g.revoke(7) is False
    g.grant(1)
    g.grant(2)
    assert g.clear() == 2 and g.tab_ids() == []


def test_the_session_rule():
    g = TabGrants()
    g.grant(7)
    assert g.note_session("") == 0 and g.tab_ids() == ["7"], "an unknown session changes nothing"
    assert g.note_session("A") == 0 and g.tab_ids() == ["7"], "the first session named keeps what was granted"
    assert g.note_session("A") == 0 and g.tab_ids() == ["7"], "the same session again keeps it"
    assert g.note_session("B") == 1 and g.tab_ids() == [], "a different session ends it"
    assert g.session == "B"


def test_only_acting_browser_tools_are_covered():
    assert is_acting_browser_tool("browser_click")
    assert is_acting_browser_tool("browser_type")
    assert is_acting_browser_tool("browser_scroll")
    assert not is_acting_browser_tool("browser_read_page")
    assert not is_acting_browser_tool("browser_get_elements")
    assert not is_acting_browser_tool("shell")
    assert not is_acting_browser_tool("")


# --------------------------------------------------------------------------- #
# 8. The add-on's half, pinned at the source
# --------------------------------------------------------------------------- #


def test_the_addon_reports_closed_tabs_and_its_browser_session():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "extensions" / "chrome" / "src"
    worker = (root / "background" / "index.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    socket = (root / "bridge" / "socket.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    panel_ts = (root / "sidepanel" / "sidepanel.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    panel_html = (root / "sidepanel" / "sidepanel.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    generated = (root / "protocol.ts").read_text(encoding="utf-8").replace("\r\n", "\n")

    import re

    assert re.search(
        r"chrome\.tabs\.onRemoved\.addListener\(\(tabId\) => \{[\s\S]{0,600}EVENT_TAB_REMOVED, \{ tab_id: tabId \}",
        worker,
    ), "the worker does not report a closed tab"
    assert "chrome.storage.session" in socket and "browser_session: this.browserSession" in socket, (
        "the hello does not carry a per-browser-session id"
    )
    assert re.search(r'id="approve-tab"[^>]*>\s*Allow for this tab\s*<', panel_html), "no Allow-for-this-tab button"
    assert re.search(r'approveTab\?\.addEventListener\("click"[\s\S]{0,300}scope: "tab"', panel_ts), (
        "the button does not send scope: tab"
    )
    assert 'payload["tab_allowed"] !== true' in panel_ts, "the header does not read tab_allowed"
    assert "onActivated" in panel_ts and "PANEL_ACTION_OPEN" in panel_ts, "no re-ask on a tab switch"
    assert 'EVENT_TAB_REMOVED = "tab_removed"' in generated and "browser_session?: string" in generated, (
        "protocol.ts was not regenerated"
    )
