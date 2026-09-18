"""v1.262.0 — the sidebar becomes a browser agent.

THE REPORT: "It doesn't navigate and control the browser and simply acts as a
chat bot next to the window. It should have full agentic capability with the
browser." The daemon already had every acting tool (navigate, click, type,
press key, scroll, tabs — Ship 3) behind ``browser_access = interactive`` and
the approval card. The sidebar could not reach them:

  * a panel turn was armed BY THE SENTENCE — the autoselect pass arms
    ``browser_click`` for "click the 'Buy' button" and nothing for "book the
    first available slot" — so most requests ran with no acting tool at all;
  * six tool rounds, then an escalation the sidebar cannot follow ("ask again
    in the Iron Jarvis window") — a form is five rounds of read → act;
  * the prompt never said it could act, so the model advised the user which
    buttons to press; Read only stripped every acting tool in silence; every
    action was its own card with no way to allow the rest of the task.

WHAT THIS FILE PINS, each against the REAL app, pairing socket and chat lane
(harness lifted into ``tests/_fakes/panel_harness.py``):
  1. ARMED BY SURFACE: an interactive panel turn on a sentence no rule matches
     is offered every read tool AND every acting tool; a read-only turn is
     offered the read tier only. Read tools run with no card; an acting tool
     raises the card (VISIBLE, NEVER GRANTED).
  2. ALLOW FOR THIS TASK: one press covers the rest of the turn; a plain Allow
     covers one call.
  3. THE BRIEF: the system prompt carries the agent's brief only when acting
     tools are armed, and the look-only line at Read only.
  4. ROUNDS: a browser-agent turn gets 24 rounds and ends IN CHAT with the
     browser wording, never on the escalation dead end.
  5. WORDS: tool and approval frames are worded for a person.
  6. The add-on's card offers the button and the panel explains its mode.
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
from iron_jarvis.browser.panel import PanelTurns, describe_browser_call
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

from tests._fakes.panel_harness import Gate, RealApp  # noqa: F401 — Gate kept for parity

_ROOT = Path(__file__).resolve().parents[1]
_ADDON = _ROOT / "extensions" / "chrome" / "src" / "sidepanel"

#: A request NO autoselect rule matches — no "click", "type", "page", "tab",
#: "browser" in it. Before v1.262.0 this armed nothing at all.
_NEUTRAL = "book the first available slot for tomorrow morning"

_READ_TIER = frozenset(
    {
        "browser_get_status",
        "browser_list_tabs",
        "browser_get_active_tab",
        "browser_read_page",
        "browser_get_elements",
        "browser_screenshot",
    }
)
_ACT_TIER = chat_turn._BROWSER_ACTING_TOOLS


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _offered_names(kw: dict) -> set:
    return {str(spec.get("name") or "") for spec in (kw.get("tools") or [])}


def _final(text: str, calls: list[ToolCall] | None = None) -> dict:
    return {
        "type": "final",
        "response": LLMResponse(text=text, tool_calls=calls or [], usage={}),
        "provider": "mock",
        "model": "mock",
    }


def _scripted(app: RealApp, rounds: list[list[ToolCall]], seen: list | None = None):
    """Round n calls ``rounds[n]``; past the script, a plain text answer."""
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        if seen is not None:
            seen.append(dict(kw))
        n = state["n"]
        state["n"] += 1
        if n < len(rounds):
            yield _final("", rounds[n])
        else:
            yield {"type": "text", "text": "All done."}
            yield _final("All done.")

    app.platform.router.stream = fake_stream
    return state


def _tool_names(calls: list[dict]) -> list[str]:
    return [str(c["args"][0]) for c in calls]


# ==========================================================================
# 1. armed by surface, not by sentence
# ==========================================================================


def test_an_interactive_sidebar_turn_is_offered_the_whole_browser_family(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the panel turn never finished")
        offered = _offered_names(seen[0])
        assert _READ_TIER <= offered, f"read tier missing: {sorted(_READ_TIER - offered)}"
        # v1.271.0: the new-tab tool is offered only to a sentence that asks for a
        # new tab (tests/test_browser_sidepanel_tab_v1271.py); the rest of the
        # acting tier is offered by surface exactly as before.
        _act = _ACT_TIER - {"browser_create_tab"}
        assert _act <= offered, f"acting tier missing: {sorted(_act - offered)}"
        assert "browser_create_tab" not in offered, "a plain sentence was offered the new-tab tool"
        # Still scoped to the browser: nothing outside the family.
        assert all(n.startswith("browser_") for n in offered), sorted(offered)


def test_a_read_only_sidebar_turn_is_offered_the_read_tier_and_nothing_that_acts(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the panel turn never finished")
        offered = _offered_names(seen[0])
        assert offered == _READ_TIER, sorted(offered)


def test_a_read_tool_runs_with_no_card_and_an_acting_tool_raises_one(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        calls = app.stub_tools()
        _scripted(app, [
            [ToolCall(id="c1", name="browser_read_page", arguments={})],
            [ToolCall(id="c2", name="browser_click", arguments={"element_id": 7})],
        ])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        card = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the click never raised a card")
        assert card["tool"] == "browser_click"
        assert "click" in card["text"] and "browser_click" not in card["text"], card["text"]
        # The read ran BEFORE any card, without one.
        assert _tool_names(calls) == ["browser_read_page"]
        app.panel.send(P.PANEL_ACTION_APPROVE, id=card["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished after the approval")
        assert _tool_names(calls) == ["browser_read_page", "browser_click"]
        assert not any(c["kw"].get("deny_reason") for c in calls)


# ==========================================================================
# 2. allow for this task
# ==========================================================================


def test_allow_for_this_task_covers_the_rest_of_the_turn(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        calls = app.stub_tools()
        _scripted(app, [
            [ToolCall(id="c1", name="browser_click", arguments={"element_id": 1})],
            [ToolCall(id="c2", name="browser_click", arguments={"element_id": 2})],
            [ToolCall(id="c3", name="browser_click", arguments={"element_id": 3})],
        ])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        first = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")
        app.panel.send(P.PANEL_ACTION_APPROVE, id=first["id"], scope="task")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        cards = [p for e, p in app.panel.events() if e == P.PANEL_EVENT_APPROVAL]
        assert len(cards) == 1, f"a task-wide allow still raised {len(cards)} cards"
        assert _tool_names(calls) == ["browser_click"] * 3
        assert not any(c["kw"].get("deny_reason") for c in calls)


def test_a_plain_allow_covers_one_call_only(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        calls = app.stub_tools()
        _scripted(app, [
            [ToolCall(id="c1", name="browser_click", arguments={"element_id": 1})],
            [ToolCall(id="c2", name="browser_click", arguments={"element_id": 2})],
        ])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        first = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")
        app.panel.send(P.PANEL_ACTION_APPROVE, id=first["id"])
        second = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the second click raised no card", nth=2)
        assert second["id"] != first["id"]
        app.panel.send(P.PANEL_ACTION_DENY, id=second["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        reasons = [c["kw"].get("deny_reason") for c in calls]
        assert reasons[0] in (None, "") and reasons[1], reasons


@pytest.mark.parametrize(
    "action,scope,expected",
    [
        (P.PANEL_ACTION_APPROVE, "", "once"),
        (P.PANEL_ACTION_APPROVE, "task", "conversation"),
        (P.PANEL_ACTION_APPROVE, "tab", "tab"),  # v1.266.0
        (P.PANEL_ACTION_APPROVE, "anything-else", "once"),
        (P.PANEL_ACTION_DENY, "task", "deny"),
    ],
)
def test_the_panel_maps_scope_to_the_registry_decision(monkeypatch, action, scope, expected):
    decided: list[tuple[str, str]] = []

    class _Reg:
        def resolve(self, approval_id, decision):
            decided.append((approval_id, decision))
            return True

    from iron_jarvis.daemon.routes import chat as chat_routes

    monkeypatch.setattr(chat_routes, "_approvals", lambda d: _Reg())
    turns = PanelTurns(SimpleNamespace(registry=None))
    turns._offered.add("apr_1")
    emitted: list = []

    async def emit(conn, event, payload):
        emitted.append((event, payload))

    turns.emit = emit  # type: ignore[assignment]
    asyncio.run(turns._decide(object(), action, "apr_1", scope))
    assert decided == [("apr_1", expected)]
    assert emitted == [], emitted


# ==========================================================================
# 3. the brief
# ==========================================================================


def test_the_agent_brief_rides_only_with_acting_tools(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the panel turn never finished")
        system = str(seen[0].get("system") or "")
        assert "Work the task step by step" in system
        assert chat_turn.BROWSER_LOOK_ONLY_LINE not in system
    with RealApp(tmp_path / "ro", monkeypatch, access="read_only") as app:
        app.pair()
        seen = app.records_the_offer()
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the panel turn never finished")
        system = str(seen[0].get("system") or "")
        assert chat_turn.BROWSER_LOOK_ONLY_LINE in system
        assert "Work the task step by step" not in system, "a brief that says 'you can click' beside no click tool"


# ==========================================================================
# 4. rounds
# ==========================================================================


def test_the_round_budget_and_the_cut_wording_follow_the_armed_set():
    assert chat_turn._round_budget({"browser_click"}) == chat_turn._BROWSER_TOOL_ROUNDS == 24
    assert chat_turn._round_budget({"browser_read_page"}) == chat_turn._MAX_TOOL_ROUNDS
    assert chat_turn._round_budget({"docx_edit"}) == chat_turn._DOC_TOOL_ROUNDS
    assert chat_turn._round_budget({"browser_type", "docx_edit"}) == chat_turn._BROWSER_TOOL_ROUNDS
    assert chat_turn._stays_in_chat({"browser_navigate"}) and chat_turn._stays_in_chat({"docx_edit"})
    assert not chat_turn._stays_in_chat({"browser_read_page", "read_file"})
    assert chat_turn._out_of_rounds_instruction({"browser_click"}) == chat_turn.OUT_OF_STEPS_BROWSER_INSTRUCTION
    assert chat_turn._out_of_rounds_instruction({"docx_edit"}) == chat_turn.OUT_OF_ROUNDS_INSTRUCTION


def test_the_acting_set_is_exactly_what_the_registry_declares_interactive(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch) as app:
        declared = {
            name
            for name in app.platform.registry.names()
            if name.startswith("browser_")
            and getattr(app.platform.registry.get(name), "min_access", "") == "interactive"
        }
        assert declared == set(_ACT_TIER), sorted(declared ^ set(_ACT_TIER))


def test_a_browser_agent_turn_runs_past_six_rounds_and_ends_in_chat(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        calls = app.stub_tools()
        # A model that ALWAYS reads the page: no card, every round consumed.
        _scripted(app, [[ToolCall(id=f"c{i}", name="browser_read_page", arguments={})] for i in range(40)])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        reads = _tool_names(calls).count("browser_read_page")
        assert reads > chat_turn._MAX_TOOL_ROUNDS, f"only {reads} rounds ran"
        assert reads == chat_turn._BROWSER_TOOL_ROUNDS - 1, f"{reads} reads for a {chat_turn._BROWSER_TOOL_ROUNDS}-round budget"
        words = " ".join(p.get("text", "") for e, p in app.panel.events() if e == P.PANEL_EVENT_DELTA)
        assert "needs the full Iron Jarvis agent" not in words, words
        assert words.strip(), "the cut turn said nothing"


# ==========================================================================
# 5. words
# ==========================================================================


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("browser_click", {"element_id": 12, "text_hint": "Search"}, "click 'Search'"),
        ("browser_click", {"element_id": 12}, "click 'element 12'"),
        ("browser_click", {}, "click on the page"),
        # v1.274.0: the tools' REAL shapes nest the target — these rows are what
        # a card actually receives; the flat rows above are older shapes.
        ("browser_click", {"target": {"role": "button", "name": "Sign in"}, "tab_id": 7}, "click 'Sign in'"),
        ("browser_click", {"target": {"element_id": "e17"}, "snapshot_id": "s1"}, "click 'element e17'"),
        ("browser_click", {"target": {"css": "#submit"}}, "click '#submit'"),
        ("browser_type", {"target": {"role": "textbox", "name": "Search"}, "text": "***REDACTED***", "press_enter": True},
         "type some text into 'Search' and press Enter"),
        ("browser_type", {"target": {"element_id": "e2"}, "text": "hello"}, "type 'hello' into 'element e2'"),
        ("browser_type", {"text": "flights to denver", "text_hint": "Search", "press_enter": True},
         "type 'flights to denver' into 'Search' and press Enter"),
        ("browser_type", {"text": "x" * 100}, "type '" + "x" * 59 + "…'"),
        ("browser_press_key", {"key": "Enter"}, "press Enter"),
        ("browser_navigate", {"url": "https://example.com/a"}, "open https://example.com/a"),
        ("browser_create_tab", {}, "open a new tab"),
        ("browser_scroll", {"direction": "down"}, "scroll down"),
        ("browser_close_tab", {"tab_id": 3}, "close a tab"),
        ("browser_activate_tab", {"tab_id": 3}, "switch to another tab"),
        ("browser_read_page", {}, "read the page"),
        ("browser_screenshot", {}, "take a screenshot of the page"),
        ("something_new", {}, "run something_new"),
        ("", None, "run a step"),
    ],
)
def test_describe_browser_call_speaks_for_a_person(name, args, expected):
    assert describe_browser_call(name, args) == expected


def test_tool_frames_reach_the_panel_in_words(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        app.stub_tools()
        _scripted(app, [[ToolCall(id="c1", name="browser_read_page", arguments={})]])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        tool_lines = [p["text"] for e, p in app.panel.events() if e == P.PANEL_EVENT_TOOL]
        assert tool_lines[:2] == ["Read the page…", "Read the page — done."], tool_lines


# ==========================================================================
# 6. the add-on's own surface (source pins)
# ==========================================================================


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_card_is_three_answers_and_the_panel_explains_its_mode():
    """v1.270.0: the per-task button gave way to the one switch.

    The daemon still answers ``scope: "task"`` as the chat lane's
    ``conversation`` grant (the parametrised case above proves it, and the chat
    page's card uses it); the SIDEBAR's card now offers Allow · Always allow ·
    Deny — the user's own words were "instead of permissions in the browser
    extension, there should just be a simple toggle".
    """
    html = _src(_ADDON / "sidepanel.html")
    assert re.search(r'id="approve"[^>]*>\s*Allow\s*<', html), "no Allow button"
    assert re.search(r'id="approve-always"[^>]*>\s*Always allow\s*<', html), "no Always-allow button"
    assert 'id="approve-task"' not in html and 'id="approve-tab"' not in html, (
        "the sidebar's card grew back a per-task or per-tab button beside the switch"
    )
    # v1.267.0: the explanation moved from a paragraph (`#mode-hint`) to the
    # access pill's tooltip — the minimal panel prints no instruction paragraphs.
    assert 'id="access"' in html
    ts = _src(_ADDON / "sidepanel.ts")
    assert re.search(r'approveAlways\?\.addEventListener\("click"[\s\S]{0,300}setAuto\(true\)', ts), (
        "Always allow does not turn the switch on"
    )
    assert 'case "read_only":' in ts and "choose Interactive" in ts, "read-only mode is not explained"
    assert 'case "interactive":' in ts and "Auto-allow" in ts
    assert "el.access.title = modeHint(status.access)" in ts, "the mode explanation has no home"


def test_the_panel_module_no_longer_claims_to_arm_nothing():
    src = _src(_ROOT / "src" / "iron_jarvis" / "browser" / "panel.py")
    assert "arm_family=ceiling" in src
    assert "ARMED BY SURFACE, NOT BY SENTENCE" in src
    assert "picks nothing explicitly" not in src
