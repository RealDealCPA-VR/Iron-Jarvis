"""v1.507.1 — a stop that lands while a helper's run is being created or
announced settles that run.

Found on GitHub: the reviewer's start took as long as the review's time limit
(``tests/test_team_review_finish_ship_v1401.py`` with its 0.5 s limit), the
stop landed before the hand-off's settle guard, and the reviewer's run read
"active" for ever. Two windows, both closed:

* inside ``Orchestrator.create_session``: the row is saved and the caller
  never gets its id (the cancel propagates);
* in ``delegate`` between the row and the guard: the announcement.

Each test holds the start past the limit ON PURPOSE, in the thread that runs
the event's handlers (the loop stays free, so the limit fires on time). The
assertion holds whichever side of the start the stop lands on; without the
fix it fails whenever the stop lands inside the window, which a 0.7 s hold
against a 0.5 s limit makes every time.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from iron_jarvis.agents import orchestrator as O
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.harness import finish as F
from tests.test_team_review_finish_v1401 import (  # noqa: F401 — `world` is a fixture
    body,
    capture_finish,
    helper_sessions,
    reviewer,
    stream,
    world,
    write_then_end,
)

LIMIT_S = 0.5
HOLD_S = 0.7


async def _never(rec):
    await asyncio.sleep(120)
    return "never"


def _hold(platform, event: str) -> None:
    def slow(ev):
        if ev.type == event:
            time.sleep(HOLD_S)  # in the handlers' worker thread, never on the loop

    platform.event_bus.add_handler(slow)


@pytest.mark.parametrize("event", ["session.created", "delegation.started"])
async def test_a_review_stopped_while_its_run_was_starting_leaves_no_active_run(world, monkeypatch, event):
    monkeypatch.setattr(F, "REVIEW_DEADLINE_S", LIMIT_S)
    reviewer(world.p)
    capture_finish(monkeypatch)
    write_then_end(world)
    world.reviewer = [_never]
    _hold(world.p, event)
    out = await asyncio.wait_for(stream(world.p, body(world.ws)), 60)
    done = out["done"]
    assert done is not None, "the reply ended on the review instead of going on"
    assert done["finish"]["review"]["state"] == "stopped", done["finish"]["review"]
    (kid,) = helper_sessions(world.p)
    assert kid.status is SessionStatus.CANCELLED, kid.status
    assert kid.finished_at is not None


async def test_a_session_whose_creation_is_cancelled_is_settled_with_the_queues_words(world):
    """The orchestrator's own door, with no hand-off around it: a cancel
    during ``create_session`` leaves the saved row CANCELLED, and says it was
    stopped before it started."""
    orch = O.Orchestrator(world.p)  # as ``delegate`` builds its own
    _hold(world.p, "session.created")
    task = asyncio.ensure_future(orch.create_session("look at the folder", AgentType.BUILDER))
    # Wait for the ROW (never a fixed time): its announcement is then held.
    for _ in range(500):
        if any(s.task == "look at the folder" for s in orch.list_sessions()):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the session's row never appeared")
    assert not task.done(), "the announcement was not held"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = [s for s in orch.list_sessions() if s.task == "look at the folder"]
    assert len(rows) == 1
    assert rows[0].status is SessionStatus.CANCELLED
    assert O.STOPPED_BEFORE_START in (rows[0].summary or "")
