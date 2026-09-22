"""v1.287.0 — a steer note the turn never read comes BACK, it is not thrown away.

THE SILENT FAILURE THIS FILE GUARDS: the chat page said "Steer sent — Jarvis
reads it at its next step", cleared the box, and the note vanished. A turn
reads steer notes only at a ROUND BOUNDARY; a plain question ends after round
0 and the last round of any tool job has no boundary after it. The steer route
still answered 200 ``queued`` while the turn was registered, nothing drained
the queue after the loop, and ``TURNS.release`` dropped the handle with the
note inside.

Now the stream lane closes the queue once its loop is over and returns what is
left as ``done.unread_steers``; a note arriving after that gets the route's
404. The sidebar's own socket-backed source is NOT read at that point — it
narrates consumption ("steered") and flushes its own pending notes on ``done``
(``browser/panel.py`` STEER_NOT_TAKEN), so reading it there would announce a
note as landed that never did.

Driven through the real app factory and the real ``/chat/stream`` route; the
steer rides a second, independent in-process connection while the answer is
genuinely mid-stream (the v1.241.0 stop harness).
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core.turns import TURNS, TurnRegistry
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_chat_turn_stop_v1241 import _asgi_post, _body, _drive_stream


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _held_plain_answer(gate: asyncio.Event):
    """A plain question: one round, NO tool calls. The answer pauses after its
    first word until `gate` opens — the window in which the user steers."""

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        yield {"type": "text", "text": "Here "}
        await gate.wait()
        yield {"type": "text", "text": "is the answer."}
        yield {
            "type": "final",
            "response": LLMResponse(text="Here is the answer.", tool_calls=[], usage={}),
            "provider": "mock", "model": "mock",
        }

    return fake_stream


def _plain_body(**over) -> dict:
    return _body(tools=[], **over)


# --------------------------------------------------------------------------- #
# 1. The registry: closing hands the leftovers over once and refuses the rest
# --------------------------------------------------------------------------- #


def test_closing_a_turns_queue_returns_its_notes_and_refuses_later_ones():
    reg = TurnRegistry()
    handle = reg.register("t1")
    assert reg.steer("t1", "shorter") is True
    assert reg.steer("t1", "bullets") is True
    assert handle.close_steers() == ["shorter", "bullets"]
    # Still registered (the runner has not released yet) — and still refused:
    # nothing would ever read or return a note accepted now.
    assert reg.is_running("t1")
    assert reg.steer("t1", "too late") is False
    assert handle.close_steers() == []
    assert handle.take_steers() == []


# --------------------------------------------------------------------------- #
# 2. End to end: a note typed during the final answer comes back on `done`
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_note_sent_during_the_final_answer_comes_back_as_unread(tmp_path):
    app = create_app(str(tmp_path))
    gate = asyncio.Event()
    app.state.platform.router.stream = _held_plain_answer(gate)
    posted: dict = {}

    async def steer_mid_answer(ev: str, data: dict) -> None:
        if ev == "token" and "status" not in posted:
            status, reply = await _asgi_post(
                app, "/chat/turns/turn-U/steer", {"text": "make it shorter"}
            )
            posted["status"], posted["reply"] = status, reply
            gate.set()

    frames = await _drive_stream(app, _plain_body(turn_id="turn-U"), steer_mid_answer)

    assert posted.get("status") == 200, posted  # the route DID accept it
    done = [d for ev, d in frames if ev == "done"]
    assert len(done) == 1, frames
    # The turn never read it (no round boundary came round) ...
    assert all("steer" not in d for ev, d in frames if ev == "round"), frames
    # ... so it is handed back, verbatim, instead of vanishing.
    assert done[0].get("unread_steers") == ["make it shorter"], done[0]
    assert done[0]["reply"] == "Here is the answer."
    assert not TURNS.is_running("turn-U")
    status, _ = await _asgi_post(app, "/chat/turns/turn-U/steer", {"text": "later"})
    assert status == 404


@pytest.mark.asyncio
async def test_a_turn_with_no_leftover_note_carries_no_unread_key(tmp_path):
    """The key is conditional (like ``workflow_run``): absent, never []."""
    app = create_app(str(tmp_path))
    gate = asyncio.Event()
    gate.set()
    app.state.platform.router.stream = _held_plain_answer(gate)
    frames = await _drive_stream(app, _plain_body(turn_id="turn-N"))
    done = [d for ev, d in frames if ev == "done"]
    assert len(done) == 1 and "unread_steers" not in done[0], done


# --------------------------------------------------------------------------- #
# 3. The sidebar's own source is not read at the end (no double report)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_callers_own_steer_source_is_not_consulted_after_the_loop(tmp_path):
    """The panel passes ``steer_source=self.take_steer``, which emits a
    ``steered`` frame for every note it hands over. Called after the loop, it
    would tell the sidebar a note landed that no model ever saw — while the
    panel's own ``done`` handling already reports it as not taken."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody

    app = create_app(str(tmp_path))
    gate = asyncio.Event()
    gate.set()
    platform = app.state.platform
    platform.router.stream = _held_plain_answer(gate)
    calls: list[int] = []

    async def panel_source() -> str:
        calls.append(1)
        return ""

    gen = await stream_chat_turn(
        platform, {}, ChatBody(**_plain_body(turn_id="turn-P")),
        steer_source=panel_source,
    )
    raw = "".join([chunk async for chunk in gen])
    assert "event: done" in raw
    assert "unread_steers" not in raw
    assert calls == [1], "read once, at round 0's boundary — never after the loop"
