"""Fourth north-star fix plan, group 27: the browser sidebar's early Stop
(BS2) and how a stopped sidebar reply is written down (C-BS-2).

BS2: the sidebar stopped its reply with ``TURNS.stop``, which knows only a
turn that has registered. The sidebar's reply registers several awaits after
the Send was answered, so a Stop pressed in that gap was lost: the sidebar
said "Stop requested" and the reply ran to its end. Closing the last sidebar
window right after Send had the same gap, and left a reply running (and paid
for) with nobody watching. Now both use ``TURNS.stop_or_keep(..., by="you")``:
a Stop kept for a turn not yet registered is applied when it registers, so
the reply never starts and the sidebar gets its stopped ``done``. A Stop for a
reply refused before it ran (or one that already ended) answers "nothing
running", as before.

C-BS-2: a reply the person stopped was written into the sidebar's
conversation as what was read, or as "(Iron Jarvis gave no answer.)", so the
next question led the model to redo the stopped request. Now it is written as
stopped, and the next turn's history carries that.

Driven through the REAL app, the REAL pairing socket, the REAL chat lane and
the REAL turn registry (``tests/_fakes/panel_harness.py``); the model is the
only double. The turn is held BEFORE it registers by a gate in front of the
lane's own client-wall step (the original then runs). Waits are bounded
budgets that fail by name; nothing asserts an elapsed time.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.core.turns import STOP_BY_YOU, TURNS
from iron_jarvis.daemon import chat_turn as chat_turn_mod
from iron_jarvis.providers.adapters.base import LLMResponse
from tests._fakes.panel_harness import _FIRST, _SECOND, Gate, RealApp, _wait_for
from tests.test_third_plan_g18_sidebar_chat import _flatten, _messages, _sidebar_chats


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _panel_turns(app: RealApp) -> PanelTurns:
    """The one ``PanelTurns`` the app installed."""
    return app.platform.browser.backend.panel_handler.__self__


def _counting_model(app: RealApp, answers: list[str] | None = None) -> list[list[dict[str, str]]]:
    """A model that answers in one round and KEEPS each call's messages."""
    seen: list[list[dict[str, str]]] = []
    answers = list(answers or [])

    async def fake_stream(*args: Any, **kw: Any):
        seen.append(_flatten(kw.get("messages") or []))
        n = len(seen) - 1
        text = answers[n] if n < len(answers) else "The report has three parts."
        yield {"type": "text", "text": text}
        yield {
            "type": "final",
            "response": LLMResponse(text=text, usage={}),
            "provider": "mock",
            "model": "mock",
        }

    app.platform.router.stream = fake_stream
    return seen


def _hold_before_register(monkeypatch) -> tuple[Gate, threading.Event]:
    """Park the lane at its client-wall step, which runs BEFORE the turn
    registers (``stream_chat_turn``); the original runs once released."""
    gate = Gate()
    reached = threading.Event()
    original = chat_turn_mod.client_wall_for

    async def held(platform: Any, body: Any) -> Any:
        reached.set()
        await gate.wait()
        return await original(platform, body)

    monkeypatch.setattr(chat_turn_mod, "client_wall_for", held)
    return gate, reached


def _spy_register(monkeypatch) -> list[Any]:
    """Keep every handle the REAL registry hands out (the original runs)."""
    handles: list[Any] = []
    original = TURNS.register

    def spy(turn_id: str):
        handle = original(turn_id)
        handles.append(handle)
        return handle

    monkeypatch.setattr(TURNS, "register", spy)
    return handles


def _send_and_hold(app: RealApp, reached: threading.Event, text: str, **params: Any) -> str:
    """Send, and wait until the lane is parked before it registered. Returns
    the panel's turn id."""
    app.panel.send(P.PANEL_ACTION_SEND, text=text, **params)
    try:
        _wait_for(reached.is_set, "the sidebar's reply never reached the lane")
    except AssertionError:
        raise AssertionError(f"the sidebar's reply never reached the lane: {app.panel.events()}")
    turn_id = _panel_turns(app)._turn_id
    assert turn_id and TURNS.handle(turn_id) is None, "the reply registered before the gate"
    return turn_id


def _frames(app: RealApp, event: str) -> list[dict]:
    return [p for e, p in app.panel.events() if e == event]


# --------------------------------------------------------------------------- #
# BS2: a Stop pressed before the reply registers
# --------------------------------------------------------------------------- #


def test_bs2_a_stop_before_the_reply_registers_stops_it_before_it_starts(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        calls = _counting_model(app)
        gate, reached = _hold_before_register(monkeypatch)
        handles = _spy_register(monkeypatch)

        turn_id = _send_and_hold(app, reached, "summarise the open page")
        app.panel.send(P.PANEL_ACTION_STOP)
        app.panel.barrier("the Stop was never handled")
        # The Stop is KEPT for the turn that has not registered yet.
        assert TURNS.pending_stop(turn_id), "the early Stop was dropped"
        gate.release()

        done = app.panel.wait_for(P.PANEL_EVENT_DONE, "the stopped reply never ended")
        assert done.get("reason") == "stopped", done
        assert calls == [], "the model was called for a reply stopped before it started"
        assert not _frames(app, P.PANEL_EVENT_TOOL) and not _frames(app, P.PANEL_EVENT_APPROVAL)
        assert not _frames(app, P.PANEL_EVENT_DELTA)
        assert len(handles) == 1 and handles[0].stopped
        assert handles[0].stop_author == STOP_BY_YOU
        assert TURNS.pending_count() == 0
        # C-BS-2: written as stopped, in memory and in the saved chat.
        rows = _panel_turns(app)._history
        assert rows[-1] == {"role": "assistant", "content": panel_mod.STOPPED_BY_YOU_EMPTY}
        assert _messages(_sidebar_chats(app)[-1])[-1]["content"] == panel_mod.STOPPED_BY_YOU_EMPTY


def test_bs2_closing_the_last_window_right_after_send_stops_the_reply(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        calls = _counting_model(app)
        app.panel.send(P.PANEL_ACTION_OPEN, window="w1")
        app.panel.wait_for(P.PANEL_EVENT_STATE, "the window never opened")
        gate, reached = _hold_before_register(monkeypatch)
        handles = _spy_register(monkeypatch)

        turn_id = _send_and_hold(app, reached, "summarise the open page", window="w1")
        app.panel.send(P.PANEL_ACTION_CLOSE, window="w1")
        # A frame sent after the close is handled only once the close was.
        app.panel.send(P.PANEL_ACTION_OPEN, window="w2")
        _wait_for(lambda: TURNS.pending_stop(turn_id), "closing the sidebar did not stop the reply")
        gate.release()

        done = app.panel.wait_for(P.PANEL_EVENT_DONE, "the reply closed with the sidebar never ended")
        assert done.get("reason") == "stopped", done
        assert calls == [], "a reply nobody was watching still called the model"
        assert len(handles) == 1 and handles[0].stopped
        assert handles[0].stop_author == STOP_BY_YOU
        assert TURNS.pending_count() == 0
        assert _panel_turns(app)._history[-1]["content"] == panel_mod.STOPPED_ON_CLOSE_EMPTY


def test_bs2_a_stop_for_a_reply_refused_before_it_ran_answers_nothing_running():
    """Through the real panel Stop and the real registry: "refused" and
    "ended" are nothing running (the state frame), never a stop."""

    class _Conn:
        def __init__(self) -> None:
            self.frames: list[tuple[str, dict]] = []

        async def send(self, frame: dict) -> None:
            self.frames.append((str(frame.get("event") or ""), dict(frame.get("payload") or {})))

    async def scenario(note: str) -> None:
        turns = PanelTurns(SimpleNamespace(browser=None))
        conn = _Conn()
        turn_id = f"panel_g27d_{note}"
        turns._turn_id = turn_id
        getattr(TURNS, note)(turn_id)
        before = TURNS.pending_count()
        await turns._stop(conn)
        assert [e for e, _ in conn.frames] == [P.PANEL_EVENT_STATE], (note, conn.frames)
        assert turns._stop_asked is False, note
        assert TURNS.pending_count() == before, f"a Stop was kept for a {note} reply"

    asyncio.run(scenario("note_refused"))
    asyncio.run(scenario("note_ended"))


def test_bs2_a_reply_the_lane_refuses_drops_its_kept_stop_at_once(tmp_path, monkeypatch):
    """A Stop kept for a reply the lane then refuses (the skill went away
    between the sidebar's check and the lane's) is dropped when it is refused,
    not 120 s later, and a Stop for it from then on answers "refused"."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        calls = _counting_model(app)
        # The skill is there for the sidebar's check and the lane's first look,
        # then it is gone: the lane's own folder check finds none and refuses
        # with its 404 (before the turn registers).
        skills = app.platform.skills
        original_get = skills.get
        looks = {"n": 0}

        def get(name: str, *a: Any, **kw: Any):
            if name == "g27d-report-steps":
                looks["n"] += 1
                if looks["n"] <= 2:
                    return SimpleNamespace(name=name, user_invocable=True)
                return None
            return original_get(name, *a, **kw)

        monkeypatch.setattr(skills, "get", get)
        gate, reached = _hold_before_register(monkeypatch)
        held_skill = chat_turn_mod.turn_skill_async

        async def held(platform: Any, body: Any) -> Any:
            reached.set()
            await gate.wait()
            return await held_skill(platform, body)

        monkeypatch.setattr(chat_turn_mod, "turn_skill_async", held)

        turn_id = _send_and_hold(app, reached, "the steps", skill="g27d-report-steps")
        app.panel.send(P.PANEL_ACTION_STOP)
        app.panel.barrier("the Stop was never handled")
        assert TURNS.pending_stop(turn_id)
        gate.release()

        app.panel.wait_for(P.PANEL_EVENT_ERROR, "the lane's refusal never reached the sidebar")
        _wait_for(lambda: _panel_turns(app)._turn_id == "", "the refused reply never ended")
        assert calls == []
        assert not TURNS.pending_stop(turn_id), "the refused reply's Stop was kept"
        assert TURNS.stop_or_keep(turn_id, by=STOP_BY_YOU) == "refused"
        assert TURNS.pending_count() == 0


# --------------------------------------------------------------------------- #
# C-BS-2: a stopped reply is written as stopped, and the next turn reads it
# --------------------------------------------------------------------------- #


def test_c_bs2_a_reply_stopped_after_it_began_is_saved_as_stopped_and_carried(
    tmp_path, monkeypatch
):
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        gate = Gate()
        app.two_rounds(gate)
        app.panel.send(P.PANEL_ACTION_SEND, text="read the open tab and summarise the page")
        app.panel.wait_for(P.PANEL_EVENT_DELTA, "the reply never began")
        app.panel.send(P.PANEL_ACTION_STOP)
        app.panel.barrier("the Stop was never handled")
        gate.release()
        done = app.panel.wait_for(P.PANEL_EVENT_DONE, "the stopped reply never ended")
        assert done.get("reason") == "stopped", done

        stopped = f"{_FIRST}\n\n{panel_mod.STOPPED_BY_YOU}"
        assert _panel_turns(app)._history[-1] == {"role": "assistant", "content": stopped}
        assert _messages(_sidebar_chats(app)[-1])[-1]["content"] == stopped
        assert not any(_SECOND in p.get("text", "") for p in _frames(app, P.PANEL_EVENT_DELTA))

        seen = _counting_model(app, ["Here is what is left."])
        app.panel.send(P.PANEL_ACTION_SEND, text="what else is on the page?")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the next question never finished", nth=2)
        assert seen, "the next question never reached the model"
        assert any(
            r["role"] == "assistant" and r["content"] == stopped for r in seen[0]
        ), seen[0]
        assert not any("gave no answer" in r["content"] for r in seen[0]), seen[0]
