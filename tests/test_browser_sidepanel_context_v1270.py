"""v1.270.0 — the sidebar remembers, has ONE switch, and folds its work.

THE REPORT, three sentences: "In the browser extension it is not keeping the
items within the chat in context and doesn't know the reference. Instead of
permissions in the browser extension, there should just be a simple toggle for
all permissions required while using the extension so I don't have to keep
approving. When a request goes through, the specific detail of the process
should go behind a thinking word in the chat window that is expandable."

What was true:

1. ``PanelTurns._run`` built a ``ChatBody`` with ONE message — the sentence just
   typed. "Now the second one" referred to nothing. The daemon now holds the
   conversation (``_history``), every turn carries it, ``open`` replays it and
   ``reset`` forgets it.
2. Every page action carded, and the widest answer covered one tab. The
   ``auto_allow`` action flips ``TabGrants.grant_all`` — the grant the per-tab
   card wrote, widened to every tab — so the chat lane's card predicate finds
   every ordinary call covered. The risk door inside the tools never reads it.
3. Every ``tool`` frame was a separate line in the transcript. The frame now
   carries ``status``/``ok`` and the panel folds a step's start and end into one
   line under a collapsed "Working" summary (the runtime suite drives the built
   bundle; this file pins the frame and the source).

Driven through the REAL app, the REAL pairing socket and the REAL chat lane
(``tests/_fakes/panel_harness.py``), with the model as the only double.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.grants import TabGrants
from iron_jarvis.browser.panel import PANEL_HISTORY_MESSAGES
from iron_jarvis.browser.tools import BrowserPressKeyTool
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core.turns import TURNS
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult
from tests._fakes.panel_harness import _FIRST, _SECOND, Gate, RealApp, _headers, _wait_for
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
from tests.test_browser_tab_grant_v1266 import _cards, _click, _hello, _look_at, _script, _states

_ADDON = Path(__file__).resolve().parents[1] / "extensions" / "chrome" / "src"
PANEL_HTML = _ADDON / "sidepanel" / "sidepanel.html"
PANEL_TS = _ADDON / "sidepanel" / "sidepanel.ts"
PROTOCOL_TS = _ADDON / "protocol.ts"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _panel_turns(app: RealApp):
    """The one ``PanelTurns`` the app installed (``backend.panel_handler`` is its bound ``handle``)."""
    return app.platform.browser.backend.panel_handler.__self__


def _recording_answers(app: RealApp, answers: list[str]) -> list[list[dict[str, str]]]:
    """A model that answers ``answers`` in order and KEEPS the messages it was handed."""
    seen: list[list[dict[str, str]]] = []
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        seen.append(_flatten(kw.get("messages") or []))
        n = state["n"]
        state["n"] += 1
        text = answers[n] if n < len(answers) else "All done."
        yield {"type": "text", "text": text}
        yield {
            "type": "final",
            "response": LLMResponse(text=text, usage={}),
            "provider": "mock",
            "model": "mock",
        }

    app.platform.router.stream = fake_stream
    return seen


def _flatten(messages: list) -> list[dict[str, str]]:
    """``(role, text)`` rows however the lane spelled them (dicts, objects, block lists)."""
    rows: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(
                str(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", b))
                for b in content
            )
        rows.append({"role": str(role or ""), "content": str(content or "")})
    return rows


def _histories(app: RealApp) -> list[dict]:
    return [p for e, p in app.panel.events() if e == P.PANEL_EVENT_HISTORY]


def _open(app: RealApp, **params: Any) -> dict:
    """Post ``open`` and return the ``history`` frame it answers with."""
    n = len(_histories(app))
    app.panel.send(P.PANEL_ACTION_OPEN, **params)
    return app.panel.wait_for(P.PANEL_EVENT_HISTORY, "open never replayed the conversation", nth=n + 1)


def _latest_state(app: RealApp, what: str, *, after: int) -> dict:
    return app.panel.wait_for(P.PANEL_EVENT_STATE, what, nth=after + 1)


# --------------------------------------------------------------------------- #
# 1. It remembers
# --------------------------------------------------------------------------- #


def test_the_second_message_carries_the_first_exchange(tmp_path, monkeypatch):
    """THE REPORT. The model answering message two is handed message one and its answer."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        seen = _recording_answers(app, ["Noted: the number is 41.", "It was 41."])
        app.panel.send(P.PANEL_ACTION_SEND, text="Remember the number 41.")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the first message never finished")
        app.panel.send(P.PANEL_ACTION_SEND, text="What number did I say?")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the second message never finished", nth=2)

        assert len(seen) == 2
        first_turn = [r for r in seen[0] if r["role"] in ("user", "assistant")]
        assert [r["role"] for r in first_turn] == ["user"], first_turn
        second = [r for r in seen[1] if r["role"] in ("user", "assistant")]
        assert [r["role"] for r in second] == ["user", "assistant", "user"], second
        assert "Remember the number 41." in second[0]["content"]
        assert "Noted: the number is 41." in second[1]["content"]
        assert second[-1]["content"].endswith("What number did I say?")


def test_open_replays_the_conversation_and_reset_forgets_it(tmp_path, monkeypatch):
    """A reopened panel is shown what was said; a new conversation starts empty — for the model too."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        seen = _recording_answers(app, ["Noted.", "Fresh."])
        assert _open(app)["turns"] == [], "a fresh sidebar already had a conversation"
        app.panel.send(P.PANEL_ACTION_SEND, text="Remember 41.")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the first message never finished")

        replay = _open(app)["turns"]
        assert replay == [
            {"role": "user", "text": "Remember 41."},
            {"role": "assistant", "text": "Noted."},
        ], replay

        # Count BEFORE sending: a reset is answered in microseconds now that
        # `open` no longer waits on the model probe, and a count taken after
        # the send could already include the answer (then wait for one more).
        seen_histories = len(_histories(app))
        app.panel.send(P.PANEL_ACTION_RESET)
        cleared = app.panel.wait_for(
            P.PANEL_EVENT_HISTORY, "reset never answered", nth=seen_histories + 1
        )
        assert cleared["turns"] == []
        assert _panel_turns(app)._history == []

        app.panel.send(P.PANEL_ACTION_SEND, text="What number?")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the message after the reset never finished", nth=2)
        rows = [r for r in seen[1] if r["role"] in ("user", "assistant")]
        assert [r["role"] for r in rows] == ["user"], f"the reset did not reach the model: {rows}"
        assert "41" not in " ".join(r["content"] for r in rows)


def test_open_replays_the_conversation_before_the_model_probe(tmp_path, monkeypatch):
    """The replay must not wait behind the model list: that list probes providers and
    shells out to CLIs, and on a busy machine that is seconds. A reopened panel shows
    the conversation first and fills the picker when the probe returns. Order, not
    duration, is asserted; the probe is made deliberately slow so the order is visible."""
    import time as _time

    from iron_jarvis.daemon.routes import connections

    def slow_catalog(d):
        _time.sleep(0.4)
        return [{"provider": "mock", "model": "mock", "available": True}]

    monkeypatch.setattr(connections, "selectable_models", slow_catalog)
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _recording_answers(app, ["Noted."])
        app.panel.send(P.PANEL_ACTION_SEND, text="Remember 41.")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the message never finished")
        app.panel.send(P.PANEL_ACTION_OPEN)
        app.panel.wait_for(P.PANEL_EVENT_MODELS, "open never sent the model list")
        names = [e for e, _ in app.panel.events()]
        assert P.PANEL_EVENT_HISTORY in names and P.PANEL_EVENT_MODELS in names
        assert names.index(P.PANEL_EVENT_HISTORY) < names.index(P.PANEL_EVENT_MODELS), names
        # And the state frame still leads, as v1.267.0 promised the header.
        assert names.index(P.PANEL_EVENT_STATE) < names.index(P.PANEL_EVENT_HISTORY), names
        replay = [p for e, p in app.panel.events() if e == P.PANEL_EVENT_HISTORY][-1]["turns"]
        assert replay == [{"role": "user", "text": "Remember 41."}, {"role": "assistant", "text": "Noted."}]


def test_reset_while_a_turn_runs_is_refused_and_the_conversation_is_kept(tmp_path, monkeypatch):
    gate = Gate()
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        app.one_round("Half an answer", gate)
        app.panel.send(P.PANEL_ACTION_SEND, text="Take your time.")
        app.panel.wait_for(P.PANEL_EVENT_DELTA, "the turn never started")

        app.panel.send(P.PANEL_ACTION_RESET)
        refusal = app.panel.wait_for(P.PANEL_EVENT_ERROR, "a reset mid-turn was not refused")
        assert refusal.get("reason") == "still_running"
        assert "Stop" in refusal["text"]

        gate.release()
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        assert _open(app)["turns"] == [
            {"role": "user", "text": "Take your time."},
            {"role": "assistant", "text": "Half an answer"},
        ]


def test_a_stopped_turn_keeps_the_half_answer_the_user_read(tmp_path, monkeypatch):
    """What the conversation remembers is what was on screen — including a stop's half."""
    gate = Gate()
    with RealApp(tmp_path, monkeypatch) as app:
        app.pair()
        app.stub_tools()
        app.billed_then_parks(gate)
        app.panel.send(P.PANEL_ACTION_SEND, text="read the open tab and summarise the page")
        first = app.panel.wait_for(P.PANEL_EVENT_DELTA, "the turn never started generating")
        assert first["text"] == _FIRST
        app.panel.send(P.PANEL_ACTION_STOP)
        app.panel.barrier("the stop frame was never processed by the daemon")
        gate.release()
        app.panel.wait_for(P.PANEL_EVENT_DONE, "a stopped turn left the panel with no terminal frame")

        turns = _open(app)["turns"]
        assert [t["role"] for t in turns] == ["user", "assistant"], turns
        assert turns[1]["text"] == _FIRST, turns
        assert _SECOND not in turns[1]["text"]


class _RecordingConn:
    """A socket stand-in that records, with every frame, what the conversation held AT THAT MOMENT."""

    def __init__(self, turns) -> None:
        self.turns = turns
        self.frames: list[tuple[dict, list[dict[str, str]]]] = []

    async def send(self, frame: dict) -> None:
        self.frames.append((frame, [dict(r) for r in self.turns._history]))

    def at(self, event: str) -> list[dict[str, str]]:
        for frame, history in self.frames:
            if frame.get("event") == event:
                return history
        raise AssertionError(f"no {event} frame was sent; sent: {[f.get('event') for f, _ in self.frames]}")


@pytest.mark.parametrize("exit_path", ["done", "stopped", "failed"])
def test_the_exchange_is_written_before_the_terminal_frame_leaves(exit_path: str):
    """A FACT, RECORDED BEFORE IT IS SAID. The parallel suite caught the opposite order:
    `open` racing the turn's own `finally`, and replaying a question with no answer.
    Three exits, one rule: when the terminal frame crosses the socket, the conversation
    already holds the question and what the user read."""
    import asyncio
    from types import SimpleNamespace

    from iron_jarvis.browser import panel as panel_mod
    from iron_jarvis.browser.panel import PanelTurns

    turns = PanelTurns(SimpleNamespace(browser=None))
    conn = _RecordingConn(turns)

    if exit_path == "done":
        # The lane's own frames, through the real translator: two tokens, then done.
        async def drive():
            turns._turn_text, turns._remembered, turns._answer = "q?", False, []
            await turns._translate(conn, "token", {"text": "half "})
            await turns._translate(conn, "token", {"text": "answer"})
            await turns._translate(conn, "done", {"text": "half answer"})

        asyncio.run(drive())
        assert conn.at("done") == [{"role": "user", "content": "q?"}, {"role": "assistant", "content": "half answer"}]
        return

    # The other two exits go through `_run` with the chat lane stood in for at its seam.
    async def stopped_lane(*a, **kw):
        async def gen():
            yield "event: token\ndata: {\"text\": \"half\"}\n\n"
            # A stopped turn returns WITHOUT a done frame (the lane's contract).

        return gen()

    async def failing_lane(*a, **kw):
        async def gen():
            yield "event: token\ndata: {\"text\": \"half\"}\n\n"
            raise RuntimeError("provider fell over")

        return gen()

    import iron_jarvis.daemon.chat_stream as chat_stream

    original = chat_stream.stream_chat_turn
    chat_stream.stream_chat_turn = stopped_lane if exit_path == "stopped" else failing_lane
    try:
        async def drive():
            turns._conn = conn
            turns._turn_text, turns._remembered, turns._answer = "q?", False, []
            await turns._run(conn, "q?", "panel_test")

        asyncio.run(drive())
    finally:
        chat_stream.stream_chat_turn = original

    terminal = "done" if exit_path == "stopped" else "error"
    assert conn.at(terminal) == [{"role": "user", "content": "q?"}, {"role": "assistant", "content": "half"}]
    assert panel_mod.PANEL_HISTORY_MESSAGES >= 2


def test_the_conversation_is_bounded_oldest_first_out(tmp_path, monkeypatch):
    assert PANEL_HISTORY_MESSAGES == 40
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        _panel_turns(app).history_limit = 4
        _recording_answers(app, ["a1", "a2", "a3"])
        for n, text in enumerate(["q1", "q2", "q3"], start=1):
            app.panel.send(P.PANEL_ACTION_SEND, text=text)
            app.panel.wait_for(P.PANEL_EVENT_DONE, f"message {n} never finished", nth=n)
        turns = _open(app)["turns"]
        assert [t["text"] for t in turns] == ["q2", "a2", "q3", "a3"], turns


# --------------------------------------------------------------------------- #
# 2. One switch
# --------------------------------------------------------------------------- #


def test_the_switch_skips_the_ordinary_card_for_every_tab_across_messages(tmp_path, monkeypatch):
    """THE REPORT: no cards while the switch is on; the cards come back when it is off."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        calls = app.stub_tools()
        _script(app, [
            [_click("c1", 1)],
            [_click("c2", 2, tab_id=9)],  # a DIFFERENT tab: covered too
            "Done with the first message.",
            [_click("c3", 3)],
            "Done with the second.",
            [_click("c4", 4)],
        ])
        states_before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_AUTO_ALLOW, on=True)
        state = _latest_state(app, "the switch never answered", after=states_before)
        assert state["auto_allow"] is True
        assert app.platform.browser.auto_allowed() is True

        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the first message never finished")
        app.panel.send(P.PANEL_ACTION_SEND, text="and the next one")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the second message never finished", nth=2)
        assert _cards(app) == [], "a card appeared with the switch on"
        assert _tool_names(calls) == ["browser_click"] * 3
        assert not any(c["kw"].get("deny_reason") for c in calls)

        # OFF: the very next page action asks again.
        states_before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_AUTO_ALLOW, on=False)
        state = _latest_state(app, "the switch never answered off", after=states_before)
        assert state["auto_allow"] is False
        app.panel.send(P.PANEL_ACTION_SEND, text="one more")
        card = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the card did not come back with the switch off")
        assert card["tool"] == "browser_click"
        app.panel.send(P.PANEL_ACTION_DENY, id=card["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the third message never finished", nth=3)


def test_turning_the_switch_on_answers_the_card_that_is_up(tmp_path, monkeypatch):
    """The user presses "always" while looking at a card: that call runs, the turn goes on."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        calls = app.stub_tools()
        _script(app, [[_click("c1", 1)], [_click("c2", 2)], "Done."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        card = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")

        app.panel.send(P.PANEL_ACTION_AUTO_ALLOW, on=True)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished after the switch went on")
        assert len(_cards(app)) == 1, "a second card appeared after the switch went on"
        assert _tool_names(calls) == ["browser_click", "browser_click"]
        assert not any(c["kw"].get("deny_reason") for c in calls), "the carded call was refused, not run"


def test_the_open_carries_the_panels_remembered_setting(tmp_path, monkeypatch):
    """The daemon's flag is a mirror of the user's setting: a bool on open sets it; anything else is ignored."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        runtime = app.platform.browser
        before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_OPEN, auto_allow=True)
        assert _latest_state(app, "open never answered", after=before)["auto_allow"] is True
        before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_OPEN)
        assert _latest_state(app, "a plain open never answered", after=before)["auto_allow"] is True, (
            "an open with no setting turned the switch off"
        )
        # A non-bool is ignored in BOTH directions: a falsy one while the switch
        # is on, a truthy one while it is off. (A mutation that reads `bool(x)`
        # is invisible to a non-bool that happens to agree with the state.)
        before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_OPEN, auto_allow=0)
        assert _latest_state(app, "open never answered", after=before)["auto_allow"] is True, (
            "a non-bool (0) was read as a setting"
        )
        before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_OPEN, auto_allow=False)
        assert _latest_state(app, "open never answered", after=before)["auto_allow"] is False
        assert runtime.auto_allowed() is False
        before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_OPEN, auto_allow="yes")
        assert _latest_state(app, "open never answered", after=before)["auto_allow"] is False, (
            "a non-bool ('yes') was read as a setting"
        )


def test_a_new_browser_session_and_forget_end_the_switch(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        runtime = app.platform.browser
        _hello(app, "session-one")
        runtime.set_auto_allow(True)
        assert runtime.auto_allowed() is True
        # The same session again (a worker restart): kept — that is the report.
        _hello(app, "session-one")
        assert runtime.auto_allowed() is True
        _hello(app, "session-two")
        _wait_for(lambda: not runtime.auto_allowed(), "a new browser session kept the switch on")

        runtime.set_auto_allow(True)
        r = app.client.post("/browser/forget", headers=_headers())
        assert r.status_code == 200 and r.json()["forgotten"] is True, r.text
        assert runtime.auto_allowed() is False


def test_the_switch_does_not_skip_the_risk_gate(tmp_path):
    """Enter with no readable target still stops at the risk door with the switch on (v1.237.0 floor)."""
    approvals = FakeApprovals()
    runtime = ActRuntime(_peer(_form_page()), policy=ComputerUsePolicy(), approvals=approvals)
    _ready(runtime, tmp_path=tmp_path)
    runtime.tab_grants = TabGrants()
    runtime.tab_grants.grant_all(True)
    assert runtime.tab_grants.covers(None) and runtime.tab_grants.covers(12345), "premise: everything is covered"

    result = _run(BrowserPressKeyTool(runtime), _ctx(tmp_path), {"key": "Enter"})

    assert not result.ok
    assert "approval required" in (result.error or "")
    assert _sent(runtime, P.METHOD_PRESS_KEY) == [], "a key reached the page on the strength of the switch"
    assert len(approvals.rows) == 1


def test_grant_all_is_a_set_operation_that_clear_and_a_new_session_reset():
    g = TabGrants()
    assert g.all_allowed is False and g.covers(7) is False and g.covers("") is False
    assert g.grant_all(True) is True
    assert g.covers(7) and g.covers("") and g.covers(None), "the switch must cover a call whose tab is unknown"
    assert g.grant_all(False) is False and not g.covers(7)
    g.grant_all(True)
    g.note_session("a")
    assert g.all_allowed, "the FIRST hello has nothing to compare with and must keep the switch"
    g.note_session("a")
    assert g.all_allowed, "a repeat hello (a worker restart) must keep the switch"
    g.note_session("b")
    assert not g.all_allowed
    g.grant_all(True)
    g.grant(3)
    assert g.clear() == 1 and not g.all_allowed and not g.covers(3)


def test_the_status_route_reports_the_switch(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        assert app.client.get("/browser/status", headers=_headers()).json()["auto_allow"] is False
        app.platform.browser.set_auto_allow(True)
        assert app.client.get("/browser/status", headers=_headers()).json()["auto_allow"] is True


# --------------------------------------------------------------------------- #
# 2b. The grant REACHES the registry (v1.270.1) — the real `invoke`, not a stub
# --------------------------------------------------------------------------- #
#
# THE LIVE REPORT (2026-09-18, the user, switch on, a real tab): "I couldn't open
# a browser page — tab creation needs your approval in Settings." The ledger:
# `tool.denied browser_create_tab mode=ask: needs approval and nothing here could
# ask`. The lane skipped the card for a covered call and never put the grant a
# card's 'once' answer supplies into `session_allow`, so the registry's own gate
# refused. Every case above stubs `registry.invoke` — intent, not outcome (the
# repository's "Arming is granting" lesson, again). These cases keep the REAL
# `invoke` (its permission gate, its arming gate, its ledger row) and stub only
# the tool's `execute`, so a refusal inside the registry is visible.


def _real_click(cid: str, n: int, tab_id: int | None = None) -> ToolCall:
    """A click in the shape the REAL tool requires (`target`, not a bare `element_id`).

    The v1266 helper's `{"element_id": 1}` was never checked by anything: with
    `registry.invoke` stubbed, the argument-shape gate (v1.228.0) never ran. Against
    the real registry it refuses `missing required: target` — which is a second
    thing the stub hid.
    """
    args: dict[str, Any] = {"target": {"element_id": f"e{n}"}}
    if tab_id is not None:
        args["tab_id"] = tab_id
    return ToolCall(id=cid, name="browser_click", arguments=args)


def _stub_execute(app: RealApp, name: str, *, fail: str = "") -> list[dict]:
    """Stub ONE tool's `execute` (the page is not here); everything before it is real."""
    tool = app.platform.registry.get(name)
    assert tool is not None, f"no tool {name}"
    calls: list[dict] = []

    async def fake_execute(args, ctx=None, *a, **kw):
        calls.append(dict(args))
        if fail:
            return ToolResult(ok=False, output="", error=fail)
        return ToolResult(ok=True, output="clicked")

    tool.execute = fake_execute  # type: ignore[method-assign]
    return calls


def _tool_frames(app: RealApp, name: str) -> list[dict]:
    return [p for e, p in app.panel.events() if e == P.PANEL_EVENT_TOOL and p.get("name") == name]



def test_with_no_grant_the_real_registry_still_asks_and_deny_still_refuses(tmp_path, monkeypatch):
    """The control: the stub below does not bypass the gate — with nothing granted, the card
    comes and a Deny keeps `execute` from running (the refusal is the registry's)."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        ran = _stub_execute(app, "browser_click")
        _script(app, [[_real_click("c1", 1)], "Done."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        card = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "no grant, yet no card")
        app.panel.send(P.PANEL_ACTION_DENY, id=card["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        assert ran == [], "a denied call reached execute"
        finished = [f for f in _tool_frames(app, "browser_click") if f["status"] == "finished"]
        assert finished and finished[0]["ok"] is False


def test_the_switch_carries_the_grant_into_the_real_registry(tmp_path, monkeypatch):
    """THE LIVE REPORT, fixed: switch on → no card AND the call actually runs."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        ran = _stub_execute(app, "browser_click")
        app.platform.browser.set_auto_allow(True)
        _script(app, [[_real_click("c1", 1)], [_real_click("c2", 2, tab_id=9)], "Done."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        assert _cards(app) == [], "the switch did not skip the card"
        assert [c["target"]["element_id"] for c in ran] == ["e1", "e2"], (
            f"the covered calls never reached execute: {ran}; frames: {_tool_frames(app, 'browser_click')}"
        )
        finished = [f for f in _tool_frames(app, "browser_click") if f["status"] == "finished"]
        assert [f["ok"] for f in finished] == [True, True], finished
        assert not any("needs approval" in f["text"] for f in _tool_frames(app, "browser_click"))


def test_the_per_tab_grant_carries_the_grant_into_the_real_registry(tmp_path, monkeypatch):
    """v1.266.0's grant had the same hole. One card answered 'tab', then the next click in
    that tab runs for real — through the registry, not a stub of it."""
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        ran = _stub_execute(app, "browser_click")
        _script(app, [[_real_click("c1", 1)], [_real_click("c2", 2)], "Done."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        card = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the first click never raised a card")
        app.panel.send(P.PANEL_ACTION_APPROVE, id=card["id"], scope="tab")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        assert len(_cards(app)) == 1, "the second click in the granted tab carded"
        assert [c["target"]["element_id"] for c in ran] == ["e1", "e2"], (
            f"a covered call never reached execute: {ran}; frames: {_tool_frames(app, 'browser_click')}"
        )


def test_a_failed_step_names_its_reason_in_the_folded_line(tmp_path, monkeypatch):
    """The live report reached the user only as the model's paraphrase; the step line said
    "Could not click on the page." and nothing more. Now the tool's own error rides the
    finished frame's text, capped to a sentence."""
    from iron_jarvis.browser.panel import STEP_REASON_CHARS, _short_reason

    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        _stub_execute(app, "browser_click", fail="the page refused: that element is gone")
        app.platform.browser.set_auto_allow(True)
        _script(app, [[_real_click("c1", 1)], "Done."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        finished = [f for f in _tool_frames(app, "browser_click") if f["status"] == "finished"]
        assert finished and finished[0]["ok"] is False
        assert finished[0]["text"].startswith("Could not click"), finished
        assert "the page refused: that element is gone" in finished[0]["text"], finished
    # The cap: one line, no dump.
    long = "x" * 500 + "\n" + "y" * 10
    short = _short_reason(long)
    assert len(short) <= STEP_REASON_CHARS and short.endswith("\u2026") and "\n" not in short
    assert _short_reason("  spaced   out \n words ") == "spaced out words"
    assert _short_reason(None) == ""


# --------------------------------------------------------------------------- #
# 3. The work, folded: the frame says start/finish
# --------------------------------------------------------------------------- #


def test_tool_frames_carry_status_and_ok(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _look_at(app, 7)
        app.stub_tools()
        app.platform.browser.set_auto_allow(True)
        _script(app, [[_click("c1", 1)], "Pressed it."])
        app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the message never finished")
        tools = [p for e, p in app.panel.events() if e == P.PANEL_EVENT_TOOL]
        assert [(t["name"], t["status"], t["ok"]) for t in tools] == [
            ("browser_click", "started", None),
            ("browser_click", "finished", True),
        ], tools
        assert tools[0]["text"].endswith("…") and "done" in tools[1]["text"]


# --------------------------------------------------------------------------- #
# 4. The vocabulary, the generated protocol, the page and the script
# --------------------------------------------------------------------------- #


def test_the_vocabulary_and_the_generated_protocol():
    assert P.PANEL_ACTION_RESET in P.ALL_PANEL_ACTIONS and P.PANEL_ACTION_AUTO_ALLOW in P.ALL_PANEL_ACTIONS
    assert P.PANEL_EVENT_HISTORY in P.ALL_PANEL_EVENTS
    assert len(P.ALL_PANEL_ACTIONS) == 10 and len(P.ALL_PANEL_EVENTS) == 10
    generated = _src(PROTOCOL_TS)
    for name, value in (
        ("PANEL_ACTION_RESET", "reset"),
        ("PANEL_ACTION_AUTO_ALLOW", "auto_allow"),
        ("PANEL_EVENT_HISTORY", "history"),
    ):
        assert f'export const {name} = "{value}";' in generated, f"protocol.ts was not regenerated ({name})"


def test_the_page_has_the_switch_the_new_conversation_and_a_three_button_card():
    html = _src(PANEL_HTML)
    header = re.search(r"<header>(.*?)</header>", html, flags=re.S)
    assert header, "no header"
    assert re.search(r'<button class="switch" id="auto"[^>]*role="switch"[^>]*aria-checked="false"', header.group(1)), (
        "the switch is not in the header, or not a switch"
    )
    assert 'id="reset"' in header.group(1), "no new-conversation control in the header"
    card = re.search(r'<div id="approval" hidden>(.*?)</div>\s*</div>', html, flags=re.S)
    assert card, "no approval card"
    ids = re.findall(r'<button[^>]*id="([a-z-]+)"', card.group(1))
    assert ids == ["approve", "approve-always", "deny"], ids
    # The floor is named where the user decides: on the switch and on the wider button.
    for needle in ("Payments, passwords",):
        assert needle in re.search(r'id="auto"[^>]*title="([^"]*)"', html).group(1)
        assert needle in re.search(r'id="approve-always"[^>]*title="([^"]*)"', html).group(1)
    # The switch is drawn only where there is something to allow, and hidden
    # while a turn runs is the reset — the daemon refuses it then.
    css = re.search(r"<style>(.*?)</style>", html, flags=re.S).group(1)
    assert 'body:not([data-access="interactive"]) #auto' in css
    assert 'body[data-turn="running"] #reset' in css
    assert ".work summary" in css and ".work .step" in css, "no styles for the folded work"


def test_the_script_folds_steps_keeps_the_route_visible_and_replays_history():
    ts = _src(PANEL_TS)
    assert 'document.createElement("details")' in ts and 'block.className = "work"' in ts
    assert 'if (name === "route")' in ts, "the route notice would fold away with the steps"
    assert "case PANEL_EVENT_HISTORY:" in ts and "function paintHistory" in ts
    assert 'querySelector(".turn, .work")' in ts, "history would paint over a transcript that already holds it"
    assert "PANEL_ACTION_RESET" in ts and "PANEL_ACTION_AUTO_ALLOW" in ts
    assert "openParams(storedAuto)" in ts, "the remembered setting does not ride the open"
    assert ts.count("openParams(storedAuto)") == 2, "one of the two opens (mount, tab switch) forgot the setting"
    # The header paints the switch from the daemon's frame, never from the press.
    assert 'paintAuto(payload["auto_allow"] === true)' in ts
    press = re.search(r'el\.auto\?\.addEventListener\("click", \(\) => \{(.*?)\}\);', ts, flags=re.S)
    assert press and "paintAuto" not in press.group(1) and "setAuto" in press.group(1)


def test_the_idle_panel_still_holds_the_word_budget():
    from tests.test_browser_sidepanel_minimal_v1267 import IDLE_WORD_BUDGET, _visible_words

    words = _visible_words(_src(PANEL_HTML))
    assert "Auto-allow" in words, "the switch has no label"
    assert len(words) <= IDLE_WORD_BUDGET, words
