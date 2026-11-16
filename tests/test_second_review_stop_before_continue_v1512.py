"""v1.512.0 (second review, group 12 part D) -- a follow-up that is stopped in
the instant it is being continued is settled, never left "active" for ever.

v1.507.1 closed the window in ``create_session``: the row was saved, a stop
landed before the caller had its id, and nothing could ever settle the run.
The follow-up path had the same window, in three places:

* ``Orchestrator.continue_session`` saves the follow-up's row (off the loop)
  and then announces it; a stop on either await left the row ACTIVE;
* ``POST /sessions/{id}/continue`` writes the mission link and clears the
  parent's restart tag (two hops off the loop) before the run is started;
* ``POST /missions/{id}/retry-failed`` (a rerun of the mission) does the same
  after its new row exists.

And one under every door: a run whose task is cancelled before its FIRST
step (a stop on the loop in the same turn the run was started or promoted
from the queue) never runs ``run_session`` at all, so its cancel handler
never ran (``Orchestrator._start_managed`` / ``_settle_never_ran``).

A row left ACTIVE in a reused folder also refused every later Continue there
("A follow-up is already running or queued in this folder").

Each test holds the step in a WORKER thread on a ``threading.Event`` (the loop
stays free), stops the caller while it is held, then lets the step finish.
The parent run is never touched: the follow-up is its own row, and the
parent keeps its status and its result.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlmodel import select

from iron_jarvis.agents import orchestrator as O
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import sessions as S
from iron_jarvis.daemon.schemas import ContinueBody
from iron_jarvis.platform import build_platform

PARENT_TASK = "tidy the folder"
PARENT_RESULT = "Moved 3 notes into the archive folder."
MISSION = "job:mission"
HOLD_LIMIT_S = 30  # a ceiling on the held step, never the thing waited for


class _Hold:
    """Holds ONE call in its worker thread until released."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self.used = False

    def take(self) -> bool:
        if self.used:
            return False
        self.used = True
        return True

    def wait_here(self) -> None:
        self.reached.set()
        self.release.wait(HOLD_LIMIT_S)

    async def until_reached(self) -> None:
        assert await asyncio.to_thread(self.reached.wait, HOLD_LIMIT_S), "the step was never reached"


def _parent(engine, folder, *, origin: str | None = None) -> str:
    with session_scope(engine) as db:
        row = Session(
            task=PARENT_TASK, agent_type=AgentType.BUILDER,
            status=SessionStatus.COMPLETED, workspace_path=str(folder),
            provider="mock", model="mock-1", summary=PARENT_RESULT, origin=origin,
        )
        db.add(row)
        db.commit()
        return row.id


def _rows(engine) -> list[Session]:
    with session_scope(engine) as db:
        return list(db.exec(select(Session)).all())


def _settled_follow_up(engine, parent_id: str) -> Session:
    others = [r for r in _rows(engine) if r.id != parent_id]
    assert len(others) == 1, [(r.id, r.status) for r in others]
    (row,) = others
    assert row.status is SessionStatus.CANCELLED, (row.status, row.summary)
    assert row.finished_at is not None
    assert O.STOPPED_BEFORE_START in (row.summary or ""), row.summary
    return row


def _parent_untouched(engine, parent_id: str) -> None:
    with session_scope(engine) as db:
        parent = db.get(Session, parent_id)
    assert parent.status is SessionStatus.COMPLETED
    assert parent.summary == PARENT_RESULT


async def _stop_while_held(coro, hold: _Hold) -> None:
    task = asyncio.ensure_future(coro)
    try:
        await hold.until_reached()
        assert not task.done(), "the step was not held"
        task.cancel()
    finally:
        hold.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------- #
# Orchestrator.continue_session
# --------------------------------------------------------------------------- #


async def test_a_follow_up_stopped_while_it_is_announced_is_settled(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = O.Orchestrator(p)
    folder = tmp_path / "folder"
    folder.mkdir()
    pid = _parent(p.engine, folder)
    hold = _Hold()

    def slow(ev):
        if ev.type == "session.created" and hold.take():
            hold.wait_here()  # in the handlers' worker thread, never on the loop

    p.event_bus.add_handler(slow)
    await _stop_while_held(orch.continue_session(pid, "and archive the old ones too"), hold)

    row = _settled_follow_up(p.engine, pid)
    assert row.workspace_path == str(folder)
    _parent_untouched(p.engine, pid)
    # The folder is free again: the next Continue is not refused as busy.
    again = await orch.continue_session(pid, "now archive the old ones")
    assert again.status is SessionStatus.ACTIVE


async def test_a_follow_up_stopped_while_its_row_is_saved_is_settled(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = O.Orchestrator(p)
    folder = tmp_path / "folder"
    folder.mkdir()
    pid = _parent(p.engine, folder)
    hold = _Hold()
    real_save = orch._save

    def slow_save(session):
        if hold.take():
            hold.wait_here()  # off_loop_settled runs the save on a worker thread
        real_save(session)

    orch._save = slow_save
    await _stop_while_held(orch.continue_session(pid, "and archive the old ones too"), hold)

    _settled_follow_up(p.engine, pid)
    _parent_untouched(p.engine, pid)


# --------------------------------------------------------------------------- #
# a started (or promoted) run whose task is cancelled before its first step
# --------------------------------------------------------------------------- #


async def _settles(orch) -> None:
    """Wait for the background settles the orchestrator started (the thing
    asserted next), bounded."""
    pending = list(orch._event_tasks)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), HOLD_LIMIT_S)


async def test_a_run_stopped_before_its_first_step_is_settled(tmp_path):
    """A stop on the loop in the same turn the run was started: its task never
    runs ``run_session`` at all, so ``run_session``'s own cancel handler never
    ran and the row stayed ACTIVE."""
    p = build_platform(str(tmp_path / "home"))
    orch = O.Orchestrator(p)
    session = await orch.create_session("look at the folder", AgentType.BUILDER)
    task = orch.spawn_managed(session.id, orch.run_session(session.id))
    assert task is not None
    orch.cancel_session(session.id)  # same turn: before the task's first step
    with pytest.raises(asyncio.CancelledError):
        await task
    await _settles(orch)
    row = orch.get_session(session.id)
    assert row.status is SessionStatus.CANCELLED, (row.status, row.summary)
    assert row.finished_at is not None
    assert O.STOPPED_BEFORE_START in (row.summary or ""), row.summary


async def test_control_a_run_that_had_started_is_never_called_unstarted(tmp_path):
    """Anti-vacuity: a run whose task HAD started settles itself (here a
    stand-in that does not), and is never stamped "stopped before it
    started"."""
    p = build_platform(str(tmp_path / "home"))
    orch = O.Orchestrator(p)
    session = await orch.create_session("look at the folder", AgentType.BUILDER)
    started = asyncio.Event()

    async def stand_in():
        started.set()
        await asyncio.Event().wait()

    task = orch.spawn_managed(session.id, stand_in())
    await asyncio.wait_for(started.wait(), HOLD_LIMIT_S)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _settles(orch)
    row = orch.get_session(session.id)
    assert row.status is SessionStatus.ACTIVE
    assert O.STOPPED_BEFORE_START not in (row.summary or "")


# --------------------------------------------------------------------------- #
# the doors: POST /sessions/{id}/continue and POST /missions/{id}/retry-failed
# --------------------------------------------------------------------------- #


def _endpoint(app, path: str):
    (route,) = [r for r in app.routes if getattr(r, "path", "") == path and "POST" in r.methods]
    return route.endpoint


def _hold_helper(monkeypatch, name: str) -> _Hold:
    hold = _Hold()
    real = getattr(S, name)

    def slow(*a, **kw):
        if hold.take():
            hold.wait_here()
        return real(*a, **kw)

    monkeypatch.setattr(S, name, slow)
    return hold


@pytest.mark.parametrize("step", ["_stamp_continuation", "_clear_interrupted"])
async def test_the_continue_door_settles_a_follow_up_stopped_before_its_run_starts(tmp_path, monkeypatch, step):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    folder = tmp_path / "folder"
    folder.mkdir()
    pid = _parent(engine, folder)
    hold = _hold_helper(monkeypatch, step)
    endpoint = _endpoint(app, "/sessions/{session_id}/continue")

    await _stop_while_held(endpoint(pid, ContinueBody(message="and archive the old ones", wait=False)), hold)

    _settled_follow_up(engine, pid)
    _parent_untouched(engine, pid)


@pytest.mark.parametrize("step", ["_stamp_continuation", "_clear_interrupted"])
async def test_the_retry_door_settles_a_retry_stopped_before_its_run_starts(tmp_path, monkeypatch, step):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    folder = tmp_path / "folder"
    folder.mkdir()
    pid = _parent(platform.engine, folder, origin=MISSION)
    store = platform.worklist
    board = store.root_session_for(pid)
    store.add(board, [("a.txt", "a.txt"), ("b.txt", "b.txt")])
    store.finish(board, "a.txt", status="done")
    store.finish(board, "b.txt", status="failed")
    hold = _hold_helper(monkeypatch, step)
    endpoint = _endpoint(app, "/missions/{session_id}/retry-failed")

    await _stop_while_held(endpoint(pid), hold)

    row = _settled_follow_up(platform.engine, pid)
    assert row.task == PARENT_TASK, "a retry is a rerun: same task"
    _parent_untouched(platform.engine, pid)
