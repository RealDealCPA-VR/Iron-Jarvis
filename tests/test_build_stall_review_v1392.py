"""v1.392.0 review (the long-jobs plan's Phase 3, never stalls silently): the
defects the reviewer found in the doer's cut, each pinned red first.

1. The turn's progress mark (``core/progress``) rode into EVERY event
   handler's context copy, like the hold and the account pin did before
   v1.372.0's review: work a handler started from a coding reply's event
   (a reflex rule's run, the skill-distill sweep) reset that reply's stall
   clock with its own output. ``core/events._drop_build_turn_scope`` drops
   it now.
2. A model that said nothing for ten minutes was never NAMED: the watch read
   the provider off a ``meta`` frame no code emits, so every such notice said
   "the model's answer". The watch reads the turn's held model (and every
   model call the reply makes) now.

Every interval is read through ``harness.stall.CLOCK`` (moved by the test).
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core import progress
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import stall
from tests.test_build_sessions_v1367 import (
    Script,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(stall, "CLOCK", c)
    monkeypatch.setattr(stall, "TICK_S", 0.02)
    return c


def _stalls(app, turn: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn) if e == "stall"]


# --------------------------------------------------------------------------- #
# 1. the progress mark never rides into an event handler's work
# --------------------------------------------------------------------------- #


async def test_an_event_handler_never_carries_the_turns_progress_mark():
    """A handler runs in a COPY of the publisher's context (``to_thread``);
    the turn's hold, pin and command rule are dropped there (v1.372.0 /
    v1.381.0) — and now its progress mark: a reflex run or a sweep a handler
    starts is not the reply, and its output must never move the reply's
    stall clock."""
    bus = EventBus()
    seen: list[object] = []
    bus.add_handler(lambda event: seen.append(progress.current()))
    marks: list[int] = []
    with progress.scope(lambda: marks.append(1)):
        await bus.publish(EventType.SESSION_COMPLETED, {"x": 1}, session_id="s1")
        # The turn itself keeps its mark.
        assert progress.current() is not None
    assert await until(lambda: seen, 10)
    assert seen == [None], "an event handler ran with the coding reply's progress mark"


async def test_a_single_dispatched_handler_never_carries_it_either():
    bus = EventBus()
    seen: list[object] = []

    def handler(event):
        seen.append(progress.current())

    with progress.scope(lambda: None):
        await EventBus._dispatch(handler, object())
    assert seen == [None]


# --------------------------------------------------------------------------- #
# 2. a silent model is named
# --------------------------------------------------------------------------- #


async def test_a_model_that_says_nothing_for_ten_minutes_is_named_by_the_sessions_model(app, work, clock):
    """The session runs on Anthropic (the test's CLAUDE pair): ten minutes of
    silence before its first word names it — no ``meta`` frame exists."""
    gate = asyncio.Event()
    sid = new_session(app, work, approval_mode="yolo")["id"]

    async def hold(_script):
        await gate.wait()
        return "Finally."

    wire(app, Script([hold]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "think", "turn_id": "bt-named"}, drop=drop))
    try:
        assert await until(lambda: any(e == "round" for e, _ in turn_frames(app, "bt-named")), 30)
        clock.advance(601)
        got = await until(lambda: _stalls(app, "bt-named"), 30)
        assert got, "no stall notice"
        assert got[0]["words"] == "No progress for 10 minutes — waiting on Anthropic's answer"
        gate.set()
        lg = turn_log(app, "bt-named")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        gate.set()
        drop.set()
        await task


def test_the_watch_names_the_model_that_answered_the_reply_so_far(clock):
    """A ``model_call`` frame (the Timeline's) names who answered; the next
    silence waits on THAT model."""
    import json

    w = stall.StallWatch(provider="codex-cli")
    clock.advance(601)
    assert w.check()["words"].endswith("waiting on Codex's answer")
    w.saw("event: model_call\ndata: " + json.dumps({"provider": "claude-cli", "model": "opus"}) + "\n\n")
    assert w.ended() == {"over": True}
    clock.advance(601)
    assert w.check()["words"].endswith("waiting on Claude Code's answer")
