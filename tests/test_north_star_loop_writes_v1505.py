"""v1.505.0 (north-star 5.4): the writes on a session start, a rule fire and
the other automation doors leave the event loop.

When Compact (VACUUM), a backup or a big import holds the database file, the
next write waits for SQLite's busy timeout. A write made ON the event loop
parks the whole daemon for that wait (``/health`` stops answering); a write
made on a worker thread waits alone while the loop keeps serving.

How each test proves it, with no sleep and no wall clock:

* the write under test is ARMED by a spy that wraps the real writer (it runs
  wherever the writer runs, so it changes nothing about on-loop or off-loop);
* at the first write statement of that call, a hook on the test's own engine
  takes a REAL ``BEGIN IMMEDIATE`` on the same database file from a second
  connection, so the write has to wait;
* a ticker coroutine on the loop counts the turns it gets while the lock is
  held and RELEASES the lock itself after a few. Off the loop the ticker runs,
  the lock goes, the write lands. On the loop the ticker never runs, nothing
  releases the lock, and the write fails with "database is locked" after the
  (shortened) busy timeout: the test goes red.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

from sqlalchemy import event

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, Session, SessionStatus
from iron_jarvis.platform import build_platform

#: The loop turns the ticker must get while the lock is held before it lets
#: go. Any number above zero proves the loop ran; a few makes it unmistakable.
_TURNS = 5
#: How long a write on the LOOP waits before it fails (the red case). Short so
#: a regression fails in seconds, not the daemon's 30.
_BUSY_MS = 1500


class _WriterLock:
    """A real write lock on the test's own database, taken at the first write
    statement of the ARMED call and released by the loop's own ticker."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.db_file = engine.url.database
        self.armed_thread: int | None = None
        self.taken = threading.Event()
        self.taken_on: int | None = None
        self.released = False
        self._con: sqlite3.Connection | None = None
        event.listen(engine, "before_cursor_execute", self._before)

    def arm(self) -> None:
        if self.armed_thread is None:  # one-shot: only the first call is locked
            self.armed_thread = threading.get_ident()

    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if self.armed_thread is None or self.taken.is_set():
            return
        if threading.get_ident() != self.armed_thread:
            return
        if not statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            return
        cursor.connection.execute(f"PRAGMA busy_timeout={_BUSY_MS}")
        con = sqlite3.connect(
            self.db_file, timeout=5, isolation_level=None, check_same_thread=False
        )
        con.execute("BEGIN IMMEDIATE")
        self._con = con
        self.taken_on = threading.get_ident()
        self.taken.set()

    def release(self) -> None:
        if self._con is not None and not self.released:
            self.released = True
            self._con.execute("COMMIT")
            self._con.close()

    def detach(self) -> None:
        self.release()
        event.remove(self.engine, "before_cursor_execute", self._before)


def _arm_before(lock: _WriterLock, monkeypatch, target, attr: str, order: list | None = None):
    """Wrap ``target.attr`` so the call arms the lock on the thread it runs on
    (and, with ``order``, notes when it returned)."""
    real = getattr(target, attr)

    def spy(*a, **kw):
        lock.arm()
        out = real(*a, **kw)
        if order is not None:
            order.append(attr)
        return out

    monkeypatch.setattr(target, attr, spy)


async def _under_lock(lock: _WriterLock, awaitable):
    """Await ``awaitable`` while a ticker counts its loop turns under the lock.
    Returns ``(result, turns_while_locked)``; an exception propagates."""
    state = {"turns": 0, "done": False}

    async def ticker() -> None:
        while not state["done"]:
            await asyncio.sleep(0)  # a yield, not a timed wait
            if lock.taken.is_set() and not lock.released:
                state["turns"] += 1
                if state["turns"] >= _TURNS:
                    lock.release()

    t = asyncio.ensure_future(ticker())
    try:
        result = await awaitable
    finally:
        state["done"] = True
        await t
        lock.detach()
    return result, state["turns"]


def _row(engine, sid: str) -> Session | None:
    with session_scope(engine) as db:
        return db.get(Session, sid)


# --- the orchestrator ---------------------------------------------------------


async def test_a_session_start_waits_for_a_writer_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    lock = _WriterLock(p.engine)
    order: list[str] = []
    _arm_before(lock, monkeypatch, orch, "_save", order)
    real_publish = p.event_bus.publish

    async def publish(type_, payload, **kw):
        if type_ == EventType.SESSION_CREATED:
            order.append("session.created")
        return await real_publish(type_, payload, **kw)

    monkeypatch.setattr(p.event_bus, "publish", publish)

    session, turns = await _under_lock(lock, orch.create_session("hello", provider="mock"))

    assert lock.taken.is_set(), "the session row's write never ran"
    assert lock.taken_on != threading.main_thread().ident, "the session row was written ON the loop"
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert _row(p.engine, session.id) is not None
    # The row lands BEFORE anyone is told the session exists.
    assert order == ["_save", "session.created"], order


async def test_continue_waits_for_a_writer_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    first = await orch.create_session("first ask", provider="mock")
    row = _row(p.engine, first.id)
    row.status = SessionStatus.COMPLETED
    orch._save(row)
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, orch, "_save")

    follow, turns = await _under_lock(lock, orch.continue_session(first.id, "and then"))

    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert _row(p.engine, follow.id) is not None


async def test_a_capped_run_parks_queued_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    p.config.max_concurrent_sessions = 1
    orch._governed.add("someone-else")  # the one slot is busy
    s = await orch.create_session("nightly", provider="mock", origin="schedule:nightly")
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, orch, "_save")
    task = asyncio.ensure_future(orch.run_session(s.id))

    async def parked() -> bool:
        while not task.done():
            if any(entry[0] == s.id for entry in orch._queued):
                return True
            await asyncio.sleep(0)
        return False

    try:
        is_parked, turns = await _under_lock(lock, parked())
        assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
        assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
        assert is_parked, "the run never reached the queue"
        assert _row(p.engine, s.id).status is SessionStatus.QUEUED
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def test_a_capped_run_admitted_during_its_queued_write_runs_active(tmp_path, monkeypatch):
    """The slot frees while the QUEUED write is in flight: the run takes it
    (nothing will ever promote an entry that was never parked) and its row
    says ACTIVE again, never a stranded QUEUED."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    p.config.max_concurrent_sessions = 1
    orch._governed.add("someone-else")
    s = await orch.create_session("nightly", provider="mock", origin="schedule:nightly")
    real_save = orch._save
    calls: list[SessionStatus] = []

    def save(session):
        calls.append(session.status)
        if len(calls) == 1:
            # The other run finishes meanwhile: its slot frees and the queue
            # is served, before this run's entry exists.
            orch._governed.discard("someone-else")
            orch._dequeue_next()
        real_save(session)

    monkeypatch.setattr(orch, "_save", save)
    task = asyncio.ensure_future(orch._wait_for_slot(s))
    while not task.done() and not any(entry[0] == s.id for entry in orch._queued):
        await asyncio.sleep(0)
    if not task.done():  # parked behind a slot that is already free: it would wait forever
        task.cancel()
        raise AssertionError("the run parked QUEUED although its slot had freed")
    admitted = task.result()

    assert admitted is s
    assert s.id in orch._governed
    assert not any(entry[0] == s.id for entry in orch._queued)
    assert calls == [SessionStatus.QUEUED, SessionStatus.ACTIVE]
    assert _row(p.engine, s.id).status is SessionStatus.ACTIVE


async def test_a_delegated_childs_settle_waits_for_a_writer_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    child = await orch.create_session("child job", AgentType.BUILDER, provider="mock")
    run = AgentRun(
        session_id=child.id, agent_type=AgentType.BUILDER, provider="mock",
        model="mock-1", state=AgentState.COMPLETED, result="did the thing",
    )
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, orch, "_save")

    stopped, turns = await _under_lock(lock, orch.settle_child(child, run))

    assert stopped is False
    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert _row(p.engine, child.id).status is SessionStatus.COMPLETED


async def test_a_failed_runs_record_waits_for_a_writer_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("doomed", provider="mock")
    lock = _WriterLock(p.engine)
    order: list[str] = []
    _arm_before(lock, monkeypatch, orch, "_save", order)
    real_publish = p.event_bus.publish

    async def publish(type_, payload, **kw):
        if type_ == EventType.SESSION_COMPLETED:
            order.append("session.completed")
        return await real_publish(type_, payload, **kw)

    monkeypatch.setattr(p.event_bus, "publish", publish)

    _, turns = await _under_lock(lock, orch._finalize_failed(s, RuntimeError("boom")))

    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert _row(p.engine, s.id).status is SessionStatus.FAILED
    assert order == ["_save", "session.completed"], order


# --- a rule fire ----------------------------------------------------------------


def _router(p, orch):
    from iron_jarvis.reflex.router import ReflexRouter

    def spawn_bg(_sid, coro):
        coro.close()  # the fire is under test, not the run it starts

    return ReflexRouter(p, orch, spawn_bg=spawn_bg)


async def test_a_webhook_rule_fire_records_its_result_off_the_loop(tmp_path, monkeypatch):
    """A rule whose workflow is gone: the fire's only write is its result row."""
    p = build_platform(str(tmp_path / "home"))
    router = _router(p, Orchestrator(p))
    rule = router.store.add(
        name="hook", source="webhook", match="gh-push", action="workflow", target="gone"
    )
    lock = _WriterLock(p.engine)
    order: list[str] = []
    _arm_before(lock, monkeypatch, router.store, "mark_result", order)
    real_publish = p.event_bus.publish

    async def publish(type_, payload, **kw):
        if type_ == EventType.REFLEX_FIRED:
            order.append("reflex.fired")
        return await real_publish(type_, payload, **kw)

    monkeypatch.setattr(p.event_bus, "publish", publish)

    fired, turns = await _under_lock(lock, router.on_webhook("gh-push", {"text": "push"}))

    assert fired and fired[0]["ok"] is False
    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert "no saved workflow" in (router.store.get(rule.id).last_error or "")
    # The row says what happened BEFORE the fire is announced.
    assert order == ["mark_result", "reflex.fired"], order


async def test_a_rule_fire_starts_its_workflow_run_off_the_loop(tmp_path, monkeypatch):
    from iron_jarvis.workflows.engine import WorkflowEngine
    from iron_jarvis.workflows.models import WorkflowRunRecord
    from iron_jarvis.workflows.store import WorkflowStore

    p = build_platform(str(tmp_path / "home"))
    WorkflowStore(p.engine).save("wf", [{"name": "Say", "kind": "notify", "message": "hi"}])
    router = _router(p, Orchestrator(p))
    rule = router.store.add(name="hook", source="webhook", match="w", action="workflow", target="wf")
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, WorkflowEngine, "create_record")

    fired, turns = await _under_lock(lock, router.on_webhook("w", {"text": "go"}))

    assert fired[0]["ok"] is True, fired
    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    with session_scope(p.engine) as db:
        assert db.get(WorkflowRunRecord, fired[0]["run_id"]) is not None
    assert router.store.get(rule.id).fire_count == 1


async def test_a_session_rule_fire_keeps_the_loop_running(tmp_path, monkeypatch):
    """The whole fire of a ``session`` rule (create the run, record the
    result) behind a writer: the lock is taken at the session row."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    router = _router(p, orch)
    rule = router.store.add(name="ask", source="webhook", match="s", action="session")
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, orch, "_save")

    fired, turns = await _under_lock(lock, router.on_webhook("s", {"text": "do it"}))

    assert fired[0]["ok"] is True, fired
    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert _row(p.engine, fired[0]["session_id"]) is not None
    assert router.store.get(rule.id).fire_count == 1


# --- autonomy and schedules -------------------------------------------------------


class _StubOrch:
    """Stands in for the orchestrator: the booking is under test, not the run."""

    def __init__(self) -> None:
        self.n = 0

    async def create_session(self, *a, **kw):
        self.n += 1
        return SimpleNamespace(id=f"sess_{self.n}")

    async def run_session(self, sid):
        return SimpleNamespace(input_tokens=3, output_tokens=4)

    def register_running(self, sid, task):
        pass


def _intent(p):
    from iron_jarvis.motivation import IntentEngine

    eng = IntentEngine(p, _StubOrch())
    goal = eng.add_goal("keep the books tidy")
    prop = eng._create_proposal(
        goal_id=goal.id, title="tidy", rationale="r", agent_type="builder",
        task="tidy up", risk="low",
    )
    return eng, goal, prop


async def test_an_autonomy_action_is_booked_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    eng, goal, prop = _intent(p)
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, eng, "_book")

    _, turns = await _under_lock(lock, eng._execute(prop, goal, wait=True))

    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    booked = eng.get_proposal(prop.id)
    assert booked.status == "executed" and booked.session_id == "sess_1"
    assert booked.tokens == 7  # the run's real spend, reconciled after it
    assert eng.get_goal(goal.id).actions_taken == 1


async def test_an_approved_proposal_is_claimed_off_the_loop(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    eng, _goal, prop = _intent(p)
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, eng, "_claim_for_execution")

    _, turns = await _under_lock(lock, eng.approve(prop.id, wait=True))

    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert eng.get_proposal(prop.id).status == "executed"


async def test_a_calendar_fire_marks_its_event_off_the_loop(tmp_path, monkeypatch):
    """The calendar poller marks an event fired (at most once) BEFORE it fires
    the rule: that mark is written off the loop, and still lands first."""
    from datetime import datetime, timedelta, timezone

    from iron_jarvis.triggers.calendar import CalendarPoller
    from iron_jarvis.triggers.models import CalendarFiredRecord

    p = build_platform(str(tmp_path / "home"))
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    soon = (now + timedelta(minutes=5)).strftime("%Y%m%dT%H%M%SZ")
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:evt-1\r\n"
        f"SUMMARY:Standup\r\nDTSTART:{soon}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    marked_when_fired: list[bool] = []

    class _Router:
        async def on_calendar(self, *, title, start="", description=""):
            with session_scope(p.engine) as db:
                marked_when_fired.append(db.get(CalendarFiredRecord, "evt-1") is not None)
            return [{"ok": True}]

    # The feature is on and a feed is set (a stand-in: no real vault touched).
    door = SimpleNamespace(
        config=SimpleNamespace(calendar_trigger_enabled=True, calendar_lead_minutes=15),
        secrets=SimpleNamespace(get=lambda _name: "https://calendar.invalid/feed.ics"),
    )
    poller = CalendarPoller(door, _Router(), p.engine, http_get=lambda _u: ics, clock=lambda: now)
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, poller, "_claim")

    results, turns = await _under_lock(lock, poller.poll_once())

    assert [r["fired"] for r in results] == [1], results
    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert marked_when_fired == [True], "the rule fired before its event was marked"


async def test_a_schedule_made_from_chat_is_saved_off_the_loop(tmp_path, monkeypatch):
    from iron_jarvis.scheduling.tools import ScheduleCreateTool
    from iron_jarvis.tools.base import ToolContext

    p = build_platform(str(tmp_path / "home"))
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    lock = _WriterLock(p.engine)
    _arm_before(lock, monkeypatch, p.scheduler, "add_task")

    result, turns = await _under_lock(
        lock,
        ScheduleCreateTool(p).execute(
            {"name": "rent-monday", "cron": "0 8 * * 1", "task": "pull the rent list"}, ctx
        ),
    )

    assert result.ok, result.error
    assert lock.taken.is_set() and lock.taken_on != threading.main_thread().ident
    assert turns >= _TURNS, f"the loop stood still behind the writer ({turns} turns)"
    assert p.scheduler.get("rent-monday") is not None
