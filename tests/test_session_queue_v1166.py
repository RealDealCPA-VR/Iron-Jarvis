"""Session queue / lifecycle (v1.166.0 — P5).

``Orchestrator.spawn_managed`` is the concurrency governor: under
``max_concurrent_sessions`` (or with the limit unset) it is EXACTLY the
daemon's historical ``_spawn_bg``; at the limit it parks the UN-started
coroutine FIFO, marks the session QUEUED, publishes SESSION_QUEUED, and starts
the next parked run as each slot frees. Offline (mock provider).
"""

from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import SessionStatus


def _set_limit(platform, n: int) -> None:
    """Set the concurrency limit without tripping pydantic's unknown-field
    guard — P4 lands the ``max_concurrent_sessions`` config field; until then
    the orchestrator reads it via ``getattr(..., 0)``."""
    object.__setattr__(platform.config, "max_concurrent_sessions", n)


def _gated(order: list[str], sid: str, gate: asyncio.Event):
    """A controllable background run: records that it STARTED, then holds its
    slot until the gate opens."""

    async def _run() -> str:
        order.append(sid)
        await gate.wait()
        return sid

    return _run()


async def _drain(orch) -> None:
    """Let done-callbacks and fire-and-forget event publishes settle."""
    for _ in range(3):
        await asyncio.sleep(0)
    if orch._event_tasks:
        await asyncio.gather(*list(orch._event_tasks), return_exceptions=True)
    for _ in range(3):
        await asyncio.sleep(0)


# --- the new enum members exist with the exact wire values --------------------


def test_queued_status_and_event_values():
    assert SessionStatus.QUEUED.value == "queued"
    assert SessionStatus("queued") is SessionStatus.QUEUED
    assert EventType.SESSION_QUEUED == "session.queued"


# --- default (limit unset / 0): byte-identical to today's _spawn_bg ----------


async def test_unlimited_default_runs_everything_immediately(platform, orchestrator):
    # No max_concurrent_sessions set at all — the pre-P4 world.
    order: list[str] = []
    gate = asyncio.Event()
    tasks = [
        orchestrator.spawn_managed(f"s-{i}", _gated(order, f"s-{i}", gate))
        for i in range(3)
    ]
    # Every spawn returns a real, registered task; nothing is parked.
    assert all(isinstance(t, asyncio.Task) for t in tasks)
    assert {f"s-{i}" for i in range(3)} <= set(orchestrator._running)
    assert orchestrator._running["s-0"] is tasks[0]  # the exact task, not a copy
    assert len(orchestrator._queued) == 0
    gate.set()
    results = await asyncio.gather(*tasks)
    assert results == ["s-0", "s-1", "s-2"]  # the coroutines actually ran
    await _drain(orchestrator)
    # Done-callback self-removal: no _running leak after completion.
    assert all(f"s-{i}" not in orchestrator._running for i in range(3))


async def test_explicit_limit_zero_means_unlimited(platform, orchestrator):
    _set_limit(platform, 0)
    order: list[str] = []
    gate = asyncio.Event()
    tasks = [
        orchestrator.spawn_managed(f"z-{i}", _gated(order, f"z-{i}", gate))
        for i in range(4)
    ]
    assert all(t is not None for t in tasks)
    assert len(orchestrator._queued) == 0
    gate.set()
    await asyncio.gather(*tasks)


async def test_crash_is_logged_and_slot_freed(orchestrator, caplog):
    async def _boom() -> None:
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.ERROR):
        task = orchestrator.spawn_managed("boom-1", _boom())
        await asyncio.wait([task])
        await _drain(orchestrator)
    assert "boom-1" not in orchestrator._running
    assert any(
        "background session boom-1 failed" in r.getMessage() for r in caplog.records
    ), "a crashed background run must be surfaced in the log, not swallowed"


# --- at the limit: park FIFO, mark QUEUED, publish, return None --------------


async def test_limit_parks_fifo_with_queued_status_and_event(platform, orchestrator):
    _set_limit(platform, 1)
    a = await orchestrator.create_session("hold the slot")
    b = await orchestrator.create_session("parked first")
    c = await orchestrator.create_session("parked second")
    order: list[str] = []
    ga, gb, gc = asyncio.Event(), asyncio.Event(), asyncio.Event()

    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, ga))
    tb = orchestrator.spawn_managed(b.id, _gated(order, b.id, gb))
    tc = orchestrator.spawn_managed(c.id, _gated(order, c.id, gc))
    assert isinstance(ta, asyncio.Task)
    assert tb is None and tc is None  # parked, not started
    assert b.id not in orchestrator._running and c.id not in orchestrator._running
    assert [entry[0] for entry in orchestrator._queued] == [b.id, c.id]  # FIFO

    # The parked sessions' rows say QUEUED — the dashboard-visible truth.
    assert orchestrator.get_session(b.id).status is SessionStatus.QUEUED
    assert orchestrator.get_session(c.id).status is SessionStatus.QUEUED
    assert orchestrator.get_session(a.id).status is SessionStatus.ACTIVE

    await _drain(orchestrator)
    queued_events = [
        e for e in platform.event_bus.history if e.type == EventType.SESSION_QUEUED
    ]
    assert [e.session_id for e in queued_events] == [b.id, c.id]
    assert queued_events[0].payload == {"task": "parked first", "position": 1}
    assert queued_events[1].payload == {"task": "parked second", "position": 2}

    # Free the slot: exactly ONE parked run starts per freed slot.
    ga.set()
    await ta
    await _drain(orchestrator)
    assert order == [a.id, b.id]  # b started, c still parked
    assert orchestrator.get_session(b.id).status is SessionStatus.ACTIVE
    assert orchestrator.get_session(c.id).status is SessionStatus.QUEUED
    assert [entry[0] for entry in orchestrator._queued] == [c.id]

    # b finishes -> c starts too.
    tb_started = orchestrator._running[b.id]
    gb.set()
    await tb_started
    await _drain(orchestrator)
    assert order == [a.id, b.id, c.id]
    assert orchestrator.get_session(c.id).status is SessionStatus.ACTIVE
    assert len(orchestrator._queued) == 0
    tc_started = orchestrator._running[c.id]
    gc.set()
    await tc_started
    await _drain(orchestrator)
    assert len(orchestrator._running) == 0


async def test_limit_boundary_exact(platform, orchestrator):
    """running-count < limit runs; running-count == limit parks (kills < -> <=).

    Uses REAL session rows: no-row ids are exempt from the governor (the
    setting is max_concurrent_SESSIONS — see spawn_managed's docstring)."""
    _set_limit(platform, 2)
    s1 = await orchestrator.create_session("boundary one")
    s2 = await orchestrator.create_session("boundary two")
    s3 = await orchestrator.create_session("boundary three")
    order: list[str] = []
    g12, g3 = asyncio.Event(), asyncio.Event()
    t1 = orchestrator.spawn_managed(s1.id, _gated(order, s1.id, g12))
    t2 = orchestrator.spawn_managed(s2.id, _gated(order, s2.id, g12))
    t3 = orchestrator.spawn_managed(s3.id, _gated(order, s3.id, g3))
    assert isinstance(t1, asyncio.Task) and isinstance(t2, asyncio.Task)
    assert t3 is None
    assert [entry[0] for entry in orchestrator._queued] == [s3.id]
    g12.set()
    await asyncio.gather(t1, t2)
    await _drain(orchestrator)
    assert s3.id in order  # promoted once a slot freed
    t3_started = orchestrator._running[s3.id]
    g3.set()
    await t3_started
    await _drain(orchestrator)
    assert s3.id not in orchestrator._running


async def test_no_row_ids_exempt_from_governor(platform, orchestrator):
    """Workflow/comm ids (no Session row) share _spawn_bg but are NEVER parked:
    the setting is max_concurrent_SESSIONS, and a parked workflow that itself
    spawns sessions could deadlock the queue (coordinator decision)."""
    _set_limit(platform, 1)
    s = await orchestrator.create_session("holds the only slot")
    order: list[str] = []
    gate = asyncio.Event()
    ts = orchestrator.spawn_managed(s.id, _gated(order, s.id, gate))
    assert isinstance(ts, asyncio.Task)
    wf = orchestrator.spawn_managed("workflow-run-99", _gated(order, "wf", gate))
    assert isinstance(wf, asyncio.Task)  # started immediately, not parked
    assert len(orchestrator._queued) == 0
    gate.set()
    await asyncio.gather(ts, wf)
    await _drain(orchestrator)
    assert sorted(order) == sorted([s.id, "wf"])


async def test_cancelled_running_task_frees_its_slot(platform, orchestrator):
    _set_limit(platform, 1)
    a = await orchestrator.create_session("running then cancelled")
    b = await orchestrator.create_session("parked behind a")
    order: list[str] = []
    ga, gb = asyncio.Event(), asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, ga))
    assert orchestrator.spawn_managed(b.id, _gated(order, b.id, gb)) is None
    await _drain(orchestrator)  # let a actually start before cancelling it
    assert order == [a.id]
    ta.cancel()
    await asyncio.wait([ta])
    await _drain(orchestrator)
    assert order == [a.id, b.id]  # cancellation freed the slot for b
    tb_started = orchestrator._running[b.id]
    gb.set()
    await tb_started
    await _drain(orchestrator)
    assert b.id not in orchestrator._running


# --- cancel / delete of a QUEUED session -------------------------------------


async def test_cancel_queued_finalizes_honestly_and_never_starts(
    platform, orchestrator
):
    _set_limit(platform, 1)
    a = await orchestrator.create_session("hold the slot")
    b = await orchestrator.create_session("parked, then cancelled")
    order: list[str] = []
    gate = asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, gate))
    assert orchestrator.spawn_managed(b.id, _gated(order, b.id, gate)) is None

    got = orchestrator.cancel_session(b.id)
    assert got.status is SessionStatus.CANCELLED
    assert got.summary == "Cancelled while queued (never started)."
    assert got.finished_at is not None
    assert len(orchestrator._queued) == 0  # removed from the queue

    gate.set()
    await ta
    await _drain(orchestrator)
    assert order == [a.id]  # the cancelled run was NEVER started
    assert orchestrator.get_session(b.id).status is SessionStatus.CANCELLED
    assert b.id not in orchestrator._running


async def test_dequeue_skips_stale_entry_and_keeps_popping(platform, orchestrator):
    """An entry whose session left QUEUED behind the queue's back (the
    create->cancel race) is skipped — coro closed — and popping continues."""
    _set_limit(platform, 1)
    a = await orchestrator.create_session("hold the slot")
    b = await orchestrator.create_session("goes stale while parked")
    c = await orchestrator.create_session("runnable behind the stale one")
    order: list[str] = []
    ga, gb, gc = asyncio.Event(), asyncio.Event(), asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, ga))
    assert orchestrator.spawn_managed(b.id, _gated(order, b.id, gb)) is None
    assert orchestrator.spawn_managed(c.id, _gated(order, c.id, gc)) is None

    # Mark b terminal WITHOUT going through cancel_session (so its queue entry
    # is still parked — the exact staleness _dequeue_next must survive).
    row = orchestrator.get_session(b.id)
    row.status = SessionStatus.CANCELLED
    orchestrator._save(row)

    ga.set()
    await ta
    await _drain(orchestrator)
    assert order == [a.id, c.id]  # b skipped, c started in the same sweep
    assert orchestrator.get_session(b.id).status is SessionStatus.CANCELLED
    assert orchestrator.get_session(c.id).status is SessionStatus.ACTIVE
    assert len(orchestrator._queued) == 0
    tc_started = orchestrator._running[c.id]
    gc.set()
    await tc_started
    await _drain(orchestrator)
    assert c.id not in orchestrator._running


async def test_delete_queued_session_drops_its_entry(platform, orchestrator):
    _set_limit(platform, 1)
    a = await orchestrator.create_session("hold the slot")
    b = await orchestrator.create_session("parked, then deleted")
    order: list[str] = []
    gate = asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, gate))
    assert orchestrator.spawn_managed(b.id, _gated(order, b.id, gate)) is None

    orchestrator.delete_session(b.id)
    assert orchestrator.get_session(b.id) is None
    assert len(orchestrator._queued) == 0

    gate.set()
    await ta
    await _drain(orchestrator)
    assert order == [a.id]  # the deleted run never started


# --- real run_session end-to-end under the limit -----------------------------


async def test_real_runs_queue_then_complete(platform, orchestrator):
    _set_limit(platform, 1)
    a = await orchestrator.create_session("first real task")
    b = await orchestrator.create_session("second real task")
    ta = orchestrator.spawn_managed(a.id, orchestrator.run_session(a.id))
    tb = orchestrator.spawn_managed(b.id, orchestrator.run_session(b.id))
    assert isinstance(ta, asyncio.Task) and tb is None
    assert orchestrator.get_session(b.id).status is SessionStatus.QUEUED

    await ta
    for _ in range(500):  # b auto-starts when a's slot frees, then completes
        if orchestrator.get_session(b.id).status is SessionStatus.COMPLETED:
            break
        await asyncio.sleep(0.01)
    sa = orchestrator.get_session(a.id)
    sb = orchestrator.get_session(b.id)
    assert sa.status is SessionStatus.COMPLETED
    assert sb.status is SessionStatus.COMPLETED
    assert sb.summary  # the queued run really executed and reported a result
    assert sb.finished_at is not None
    assert len(orchestrator._queued) == 0
    await _drain(orchestrator)
    assert a.id not in orchestrator._running and b.id not in orchestrator._running


# --- create->cancel race: a terminal row is never re-queued -------------------


async def test_spawn_never_requeues_a_cancelled_row(platform, orchestrator):
    """cancel_session lands between create_session and spawn_managed at the
    limit: the row must STAY CANCELLED and the coroutine must never run —
    stamping QUEUED over it used to resurrect the run, execute work the user
    was told was cancelled, and finish it COMPLETED."""
    _set_limit(platform, 1)
    a = await orchestrator.create_session("hold the slot")
    b = await orchestrator.create_session("cancelled before spawn")
    order: list[str] = []
    ga, gb = asyncio.Event(), asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, ga))

    # The race: cancel wins first (no live task, no queue entry -> the
    # else-branch marks the row CANCELLED directly, without publishing).
    assert orchestrator.cancel_session(b.id).status is SessionStatus.CANCELLED

    coro = _gated(order, b.id, gb)
    assert orchestrator.spawn_managed(b.id, coro) is None
    assert inspect.getcoroutinestate(coro) == "CORO_CLOSED"  # discarded un-started
    assert len(orchestrator._queued) == 0  # never parked
    assert orchestrator.get_session(b.id).status is SessionStatus.CANCELLED

    ga.set()
    await ta
    await _drain(orchestrator)
    assert order == [a.id]  # b NEVER ran, even after a slot freed
    assert orchestrator.get_session(b.id).status is SessionStatus.CANCELLED
    assert b.id not in orchestrator._running
    # And no SESSION_QUEUED lie was published for the cancelled session.
    assert not [
        e
        for e in platform.event_bus.history
        if e.type == EventType.SESSION_QUEUED and e.session_id == b.id
    ]


async def test_real_run_cancelled_before_spawn_stays_cancelled(
    platform, orchestrator
):
    """Same race end-to-end with the REAL run_session coroutine: the session
    finishes CANCELLED, never COMPLETED."""
    _set_limit(platform, 1)
    a = await orchestrator.create_session("first real task")
    b = await orchestrator.create_session("cancelled real task")
    ta = orchestrator.spawn_managed(a.id, orchestrator.run_session(a.id))
    orchestrator.cancel_session(b.id)
    assert orchestrator.spawn_managed(b.id, orchestrator.run_session(b.id)) is None

    await ta
    await _drain(orchestrator)
    for _ in range(20):  # give any (wrong) promotion every chance to run
        await asyncio.sleep(0.01)
    assert orchestrator.get_session(a.id).status is SessionStatus.COMPLETED
    row = orchestrator.get_session(b.id)
    assert row.status is SessionStatus.CANCELLED  # not COMPLETED
    assert len(orchestrator._queued) == 0


# --- continue guard covers QUEUED continuations -------------------------------


async def test_second_continue_blocked_while_first_is_queued(platform, orchestrator):
    """A continuation parked QUEUED by the governor still owns the shared
    workspace: a second continue of the same parent must refuse exactly as it
    does for an ACTIVE one — two sessions writing one workspace is the race
    _continue_lock exists to prevent."""
    _set_limit(platform, 1)
    parent = await orchestrator.create_session("original work")
    done = orchestrator.get_session(parent.id)
    done.status = SessionStatus.COMPLETED  # a finished parent, ripe for continue
    orchestrator._save(done)

    # Unrelated work occupies the only slot so the continuation parks.
    order: list[str] = []
    gate = asyncio.Event()
    holder = orchestrator.spawn_managed("holder", _gated(order, "holder", gate))

    first = await orchestrator.continue_session(parent.id, "carry on")
    assert first.workspace_path == parent.workspace_path  # shared on purpose
    assert orchestrator.spawn_managed(first.id, _gated(order, first.id, gate)) is None
    assert orchestrator.get_session(first.id).status is SessionStatus.QUEUED

    with pytest.raises(ValueError, match="running or queued"):
        await orchestrator.continue_session(parent.id, "carry on again")

    # Cancelling the queued continuation frees the workspace again.
    orchestrator.cancel_session(first.id)
    second = await orchestrator.continue_session(parent.id, "carry on again")
    assert second.workspace_path == parent.workspace_path

    gate.set()
    await holder
    await _drain(orchestrator)


# --- shutdown drain -----------------------------------------------------------


async def test_shutdown_queue_discards_parked_runs_and_blocks_promotion(
    platform, orchestrator
):
    """shutdown_queue closes every parked coroutine, leaves the rows QUEUED
    for reconcile to tell the truth at next boot, and makes the slot-free hook
    inert — a task cancelled by the lifespan teardown must not create_task a
    brand-new agent run mid-shutdown."""
    _set_limit(platform, 1)
    a = await orchestrator.create_session("hold the slot")
    b = await orchestrator.create_session("parked at shutdown")
    c = await orchestrator.create_session("also parked")
    order: list[str] = []
    gate = asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, gate))
    await _drain(orchestrator)  # let a actually start
    cb = _gated(order, b.id, gate)
    cc = _gated(order, c.id, gate)
    assert orchestrator.spawn_managed(b.id, cb) is None
    assert orchestrator.spawn_managed(c.id, cc) is None

    assert orchestrator.shutdown_queue() == 2
    assert len(orchestrator._queued) == 0
    assert inspect.getcoroutinestate(cb) == "CORO_CLOSED"
    assert inspect.getcoroutinestate(cc) == "CORO_CLOSED"
    # Rows stay QUEUED on purpose — this process never ran them; the next
    # boot's reconcile marks them FAILED honestly.
    assert orchestrator.get_session(b.id).status is SessionStatus.QUEUED
    assert orchestrator.get_session(c.id).status is SessionStatus.QUEUED

    # Lifespan teardown cancels the running task; its done-callback fires
    # _release -> _dequeue_next, which must start NOTHING while draining.
    ta.cancel()
    await asyncio.wait([ta])
    await _drain(orchestrator)
    assert order == [a.id]
    assert len(orchestrator._running) == 0

    # Next boot: stranded QUEUED (and a's stranded ACTIVE) -> FAILED.
    assert orchestrator.reconcile_interrupted_sessions() == 3
    assert orchestrator.get_session(b.id).status is SessionStatus.FAILED
    assert orchestrator.get_session(c.id).status is SessionStatus.FAILED
    assert orchestrator.get_session(b.id).summary == "interrupted by a daemon restart"


async def test_dequeue_next_is_inert_while_draining(platform, orchestrator):
    """Mutation pin for _dequeue_next's early-return: after shutdown_queue the
    deque is already empty, so only a still-parked entry can prove the flag is
    honored — the teardown window where the flag is set and a done-callback
    fires must not promote it."""
    _set_limit(platform, 1)
    a = await orchestrator.create_session("running at teardown")
    b = await orchestrator.create_session("parked at teardown")
    order: list[str] = []
    gate = asyncio.Event()
    ta = orchestrator.spawn_managed(a.id, _gated(order, a.id, gate))
    cb = _gated(order, b.id, gate)
    assert orchestrator.spawn_managed(b.id, cb) is None  # parked behind a
    await _drain(orchestrator)

    orchestrator._draining = True  # flag set; the entry is still parked
    ta.cancel()
    await asyncio.wait([ta])
    await _drain(orchestrator)
    assert order == [a.id]  # b was NOT promoted
    assert [e[0] for e in orchestrator._queued] == [b.id]  # untouched, still parked
    assert b.id not in orchestrator._running
    assert orchestrator.shutdown_queue() == 1  # tidy: close the parked coro
    assert inspect.getcoroutinestate(cb) == "CORO_CLOSED"


async def test_spawn_refused_while_draining(platform, orchestrator):
    """New work arriving after shutdown_queue is refused outright — parking it
    would leak a coroutine into a queue that was already discarded, and
    starting it would race the teardown."""
    assert orchestrator.shutdown_queue() == 0  # idempotent on an empty queue
    order: list[str] = []
    gate = asyncio.Event()
    coro = _gated(order, "late", gate)
    assert orchestrator.spawn_managed("late", coro) is None
    assert inspect.getcoroutinestate(coro) == "CORO_CLOSED"
    assert len(orchestrator._queued) == 0
    assert "late" not in orchestrator._running
    assert order == []


# --- restart reconciliation ---------------------------------------------------


def test_reconcile_marks_stranded_queued_failed(platform, orchestrator):
    s = asyncio.run(orchestrator.create_session("stranded in the queue"))
    s.status = SessionStatus.QUEUED
    orchestrator._save(s)

    marked = orchestrator.reconcile_interrupted_sessions()

    assert marked == 1
    row = orchestrator.get_session(s.id)
    assert row.status is SessionStatus.FAILED
    assert row.summary == "interrupted by a daemon restart"
    assert row.finished_at is not None


def test_reconcile_still_marks_stranded_active(platform, orchestrator):
    s = asyncio.run(orchestrator.create_session("stranded active"))
    assert s.status is SessionStatus.ACTIVE
    marked = orchestrator.reconcile_interrupted_sessions()
    assert marked == 1
    assert orchestrator.get_session(s.id).status is SessionStatus.FAILED
