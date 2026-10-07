"""Wave 4 (RECOVERY), v1.312.0 — the implementer's own pins for the half of
W4-1 the tester's file cannot see.

``tests/test_wave4_chat_recovery_v1312.py`` releases the held hop BEFORE it
asserts, so a Stop checked BETWEEN stages passes it. These pin the stronger
promise ``chat_stream._prep_step`` makes: a NAMED turn's Stop ABANDONS the slow
stage it lands in (the turn ends while the hop is still held), for grounding
and for the automatic summary's model call. And one pins ``_honest_failures``:
a fault during preparation, which now runs inside an open stream, ends as one
plain ``error`` frame — never a cut connection.

Mutation-checked: make ``_prep_step`` await without racing and the first test
goes red (the stream only ends after release); drop the race in the compaction
loop and the second goes red; remove ``_honest_failures`` and the third does.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from tests.test_wave4_chat_recovery_v1312 import (
    _WHILE_HELD_S,
    _LiveStream,
    _asgi,
    _long_history,
    _sse_frames,
    _until,
    _wire,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


@pytest.mark.asyncio
async def test_a_stop_abandons_a_held_grounding_hop(tmp_path):
    """The turn ENDS while the hop is still held — the Stop did not wait for a
    wedged memory base to answer."""
    app = create_app(str(tmp_path))
    held, router, _ = _wire(app, compaction=False)
    live = _LiveStream(app, {
        "messages": [{"role": "user", "content": "hello there"}],
        "turn_id": "w4-race-ground",
    })
    live.start()
    try:
        await _until(held.entered.is_set, "the grounding hop never started",
                     _WHILE_HELD_S * 2)
        stop = await _asgi(app, "POST", "/chat/turns/w4-race-ground/stop")
        assert stop == (200, {"ok": True, "stopped": True}), stop
        await _until(lambda: live.ended,
                     "the stopped turn waited for the held hop to finish",
                     _WHILE_HELD_S)
        assert not held.release.is_set()
        assert not TURNS.is_running("w4-race-ground")
    finally:
        held.release.set()
        await live.finish()
    assert router.calls == 0
    assert live.phases() == ["recalling"], live.phases()
    for k in ("round", "token", "done", "error"):
        assert k not in live.kinds(), live.kinds()


@pytest.mark.asyncio
async def test_a_stop_cancels_the_automatic_summary_while_it_runs(tmp_path):
    """``summarizing`` is said WHILE the summary's model call runs, and a Stop
    then cancels that call: the turn ends with the call still unanswered."""
    app = create_app(str(tmp_path))
    status, body = await _asgi(app, "PUT", "/settings",
                               {"values": {"model_context_windows": {"mock": 2000}}})
    assert status == 200, body
    held, router, _ = _wire(app, compaction=False)
    held.release.set()  # grounding answers at once here
    entered, cancelled = asyncio.Event(), asyncio.Event()

    def factory(*a, **k):
        async def complete(system, user):
            entered.set()
            try:
                await asyncio.sleep(30)  # the summary that never answers
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "GOAL:\n- x\n", "acme", "acme-1"

        return complete

    app.state.platform._compaction_complete = factory
    live = _LiveStream(app, {"messages": _long_history(), "turn_id": "w4-race-summ"})
    live.start()
    try:
        await asyncio.wait_for(entered.wait(), _WHILE_HELD_S * 2)
        await _until(lambda: "summarizing" in live.phases(),
                     f"no `phase: summarizing` while the summary ran: {live.frames}",
                     _WHILE_HELD_S)
        stop = await _asgi(app, "POST", "/chat/turns/w4-race-summ/stop")
        assert stop[0] == 200, stop
        await asyncio.wait_for(cancelled.wait(), _WHILE_HELD_S)
        await _until(lambda: live.ended, "the stopped turn did not end", _WHILE_HELD_S)
    finally:
        await live.finish()
    assert router.calls == 0
    assert "choosing_tools" not in live.phases(), live.phases()
    for k in ("round", "token", "done", "error"):
        assert k not in live.kinds(), live.kinds()
    assert not TURNS.is_running("w4-race-summ")


def test_a_preparation_fault_is_one_plain_error_frame(tmp_path, monkeypatch):
    """Prep runs inside the open stream now; a fault there is said, not a cut
    connection, and it says the answer never started."""
    async def _broken(*a, **k):
        raise RuntimeError("the memory base did not answer")

    monkeypatch.setattr(chat_routes, "_gather_grounding", _broken)
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "hello there"}],
    })
    assert r.status_code == 200, r.text
    frames = _sse_frames(r.text)
    kinds = [ev for ev, _ in frames]
    assert kinds == ["phase", "error"], kinds
    detail = frames[-1][1]["detail"]
    assert "the memory base did not answer" in detail
    assert "could not get ready to answer" in detail
    assert "Send your message again" in detail
