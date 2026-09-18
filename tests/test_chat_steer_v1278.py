"""v1.278.0 — a running chat turn takes steer notes BY NAME, from any connection.

The sidebar has been able to say "shorter" to a turn already working since
v1.242.0 (its socket hands ``stream_chat_turn`` a ``steer_source``). The chat
page could not: its only mid-turn control was Stop, and a correction meant
Stop, retype, resend. Now a turn that named itself (``ChatBody.turn_id``, the
v1.241.0 stop contract) reads notes queued through
``POST /chat/turns/{turn_id}/steer`` at its next ROUND BOUNDARY, exactly where
the sidebar's notes land, and the ``round`` frame that follows carries the note
back (``steer``) so the page knows it was read.

Harness: the v1.241.0 stop test's in-process ASGI driver — the steer must
arrive down a connection the streaming turn knows nothing about.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core.turns import TURNS, TurnRegistry
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult
from tests.test_chat_turn_stop_v1241 import _asgi_post, _body, _drive_stream

@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


# --------------------------------------------------------------------------- #
# 1. The registry: a queue per running turn, taken once, gone with the turn
# --------------------------------------------------------------------------- #


def test_registry_queues_for_a_running_turn_only_and_hands_notes_over_once():
    reg = TurnRegistry()
    assert reg.steer("ghost", "shorter") is False, "an unknown turn takes no note"
    handle = reg.register("t1")
    assert reg.steer("t1", "   ") is False, "an empty note is not a note"
    assert reg.steer("t1", "shorter") is True
    assert reg.steer("t1", "and in French") is True
    assert reg.take_steers("t1") == ["shorter", "and in French"]
    assert reg.take_steers("t1") == [], "taken once"
    assert handle.take_steers() == []
    reg.steer("t1", "late")
    reg.release("t1", handle)
    assert reg.take_steers("t1") == [], "a released turn reads nothing"
    assert reg.steer("t1", "later still") is False


# --------------------------------------------------------------------------- #
# 2. The route: 404 for a turn nobody is running, and for an empty note
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_route_refuses_an_unknown_turn_and_an_empty_note(tmp_path):
    app = create_app(str(tmp_path))
    status, data = await _asgi_post(app, "/chat/turns/nobody/steer", {"text": "shorter"})
    assert status == 404, data
    assert "no such running turn" in str(data.get("detail", ""))
    TURNS.register("t-empty")
    status, data = await _asgi_post(app, "/chat/turns/t-empty/steer", {"text": "  "})
    assert status == 404, data
    assert "needs a note" in str(data.get("detail", ""))
    status, data = await _asgi_post(app, "/chat/turns/t-empty/steer", {"text": "shorter"})
    assert status == 200, data
    assert data == {"ok": True, "queued": True}


# --------------------------------------------------------------------------- #
# 3. End to end: the note joins round 2's messages and rides its round frame
# --------------------------------------------------------------------------- #


def _gated_tool(app, gate: asyncio.Event, started: asyncio.Event) -> None:
    """Round 1's tool call WAITS on `gate` — the window in which the steer lands."""

    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None, **kw):
        started.set()
        await gate.wait()
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = fake_invoke


def _two_round_stream(seen: list[list]):
    """Round 1 = one tool call; round 2 = a final answer. Records each round's
    messages so the test can see what the model was handed."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        seen.append([(m.role, m.content) for m in messages])
        if rounds["n"] == 0:
            rounds["n"] += 1
            yield {
                "type": "final",
                "response": LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="read_file",
                                         arguments={"path": "notes.txt"})],
                    usage={"input_tokens": 7, "output_tokens": 3},
                ),
                "provider": "mock", "model": "mock",
            }
        else:
            yield {"type": "text", "text": "shorter answer"}
            yield {
                "type": "final",
                "response": LLMResponse(text="shorter answer", usage={}),
                "provider": "mock", "model": "mock",
            }

    return fake_stream


@pytest.mark.asyncio
async def test_a_note_posted_mid_turn_joins_the_next_round_and_its_round_frame(tmp_path):
    app = create_app(str(tmp_path))
    gate, started = asyncio.Event(), asyncio.Event()
    _gated_tool(app, gate, started)
    seen: list[list] = []
    app.state.platform.router.stream = _two_round_stream(seen)

    async def steer_while_the_tool_runs():
        await asyncio.wait_for(started.wait(), 10)
        assert TURNS.is_running("turn-S"), "the turn never became addressable"
        status, data = await _asgi_post(app, "/chat/turns/turn-S/steer", {"text": "make it shorter"})
        assert status == 200, data
        gate.set()

    poster = asyncio.create_task(steer_while_the_tool_runs())
    frames = await _drive_stream(app, _body(turn_id="turn-S"))
    await poster

    # Round 2 was handed the note as the user's own next message, after the tool result.
    assert len(seen) == 2, [len(r) for r in seen]
    roles = [r for r, _ in seen[1]]
    assert ("user", "make it shorter") in seen[1], seen[1]
    assert roles.index("tool") < len(roles) - 1 and seen[1][-1] == ("user", "make it shorter")
    # The round frame that opened round 2 carries it; round 1's carries nothing.
    rounds = [d for ev, d in frames if ev == "round"]
    assert [r.get("steer") for r in rounds] == [None, "make it shorter"], rounds
    # And the answer still arrived.
    assert any(ev == "done" for ev, _ in frames)
    assert not TURNS.is_running("turn-S")


@pytest.mark.asyncio
async def test_a_note_the_turn_never_reached_is_dropped_not_replayed(tmp_path):
    """A note queued after the last round boundary has nobody to read it: the
    turn ends, the registry forgets it, and the next turn does not inherit it."""
    app = create_app(str(tmp_path))
    gate, started = asyncio.Event(), asyncio.Event()
    _gated_tool(app, gate, started)
    seen: list[list] = []
    app.state.platform.router.stream = _two_round_stream(seen)
    gate.set()  # nothing holds the tool; the turn runs straight through
    frames = await _drive_stream(app, _body(turn_id="turn-Q"))
    assert any(ev == "done" for ev, _ in frames)
    status, _ = await _asgi_post(app, "/chat/turns/turn-Q/steer", {"text": "too late"})
    assert status == 404
    assert TURNS.take_steers("turn-Q") == []
    assert all("steer" not in d for ev, d in frames if ev == "round")


@pytest.mark.asyncio
async def test_a_turn_without_a_name_is_untouched(tmp_path):
    """No turn_id: nothing registers, no source is attached, the frames are as before."""
    app = create_app(str(tmp_path))
    gate, started = asyncio.Event(), asyncio.Event()
    _gated_tool(app, gate, started)
    seen: list[list] = []
    app.state.platform.router.stream = _two_round_stream(seen)
    gate.set()
    frames = await _drive_stream(app, _body())
    # Rounds are counted from 0 on the wire, exactly as before this release.
    assert [d for ev, d in frames if ev == "round"] == [{"round": 0}, {"round": 1}]
    assert TURNS.running_ids() == []
