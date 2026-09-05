"""Audit Wave 5, task 5C (v1.231.0): the scheduler says what it skipped,
cancels across loops, and boots on any time zone.

AE9 (+N1): a recurring fire the app was closed for is stamped ``missed`` on
the row — never fired late, never fired twice — and recurring jobs carry a
misfire grace so a PC that slept through the minute still fires on wake.
AE11: an overlapping fire is ``skipped`` on the row, and Run-now refuses a
second copy of a task still in flight (``already running``).
AE2: a schedule-fired session (``asyncio.run`` on the APScheduler thread) is
really cancelled from the daemon's side, and the response says so.
AE12: an unresolvable local zone falls back to UTC with a doctor warning
instead of aborting ``build_platform``.

Converted from tests/_audit_20260904/test_a3_scheduler.py and
test_a3b_scheduler_cross_loop_cancel.py. Two tests tick a REAL
BackgroundScheduler for ~3 s because the overlap semantics live inside
APScheduler's job executor.
"""

from __future__ import annotations

import iron_jarvis.scheduling.models  # noqa: F401 — register the table

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.onboarding.doctor import RECOMMENDED, runtime_checks
from iron_jarvis.platform import build_platform
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.scheduling.service import Scheduler


def _engine(tmp_path):
    engine = make_engine(tmp_path / "sched.db")
    init_db(engine)
    return engine


def _set(engine, name, **cols):
    with session_scope(engine) as db:
        row = db.exec(select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == name)).first()
        for k, v in cols.items():
            setattr(row, k, v)
        db.add(row)
        db.commit()


def _due(sched: Scheduler, row: ScheduledTaskRecord) -> datetime:
    """A recurring row's ``next_run`` is the scheduler zone's wall clock
    (SQLite dropped the tzinfo) — read it on that clock, not as UTC."""
    return row.next_run.replace(tzinfo=sched.scheduler.timezone)


# --------------------------------------------------------------------------- #
# AE9 — missed while the app was closed: stamped, not fired
# --------------------------------------------------------------------------- #


def test_missed_nightly_cron_fire_is_stamped_missed_and_not_fired(tmp_path):
    calls: list[str] = []
    sched = Scheduler(_engine(tmp_path), lambda t: calls.append(t.name))
    sched.add_task("nightly", "0 3 * * *", kind="event", payload={})
    yesterday_3am = (utcnow() - timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
    _set(
        sched.engine,
        "nightly",
        next_run=yesterday_3am,  # what the row said when the daemon went down
        last_run=yesterday_3am - timedelta(days=1),
        last_status="ok",
        last_detail="session completed",
    )
    sched.start()
    try:
        time.sleep(0.3)
    finally:
        sched.shutdown(wait=True)
    assert calls == []  # no fire on boot, no double fire
    row = sched.get("nightly")
    assert _due(sched, row) > utcnow(), f"row.next_run is stale: {row.next_run}"
    assert row.last_status == "missed"
    assert row.last_detail.startswith("the app was not running at ")
    assert yesterday_3am.strftime("%Y-%m-%d 03:00") in row.last_detail


def test_recurring_row_that_recorded_its_fire_is_not_stamped_missed(tmp_path):
    """``last_run`` at or after the stored ``next_run`` means the fire happened
    (the row just never got its refresh) — the ``ok`` stays, next_run moves."""
    sched = Scheduler(_engine(tmp_path), lambda t: None)
    sched.add_task("hourly", interval_seconds=3600, kind="event", payload={})
    an_hour_ago = utcnow() - timedelta(hours=1)
    _set(
        sched.engine,
        "hourly",
        next_run=an_hour_ago,
        last_run=an_hour_ago + timedelta(seconds=2),
        last_status="ok",
        last_detail="done",
    )
    sched.start()
    sched.shutdown(wait=True)
    row = sched.get("hourly")
    assert row.last_status == "ok"
    assert row.last_detail == "done"
    assert _due(sched, row) > utcnow()


def test_disabled_and_date_rows_are_left_alone_by_the_reconcile(tmp_path):
    sched = Scheduler(_engine(tmp_path), lambda t: None)
    sched.add_task("off", "0 3 * * *", kind="event", payload={}, enabled=False)
    long_ago = utcnow() - timedelta(days=3)
    _set(sched.engine, "off", next_run=long_ago, last_status="ok")
    sched.start()
    sched.shutdown(wait=True)
    row = sched.get("off")
    assert row.last_status == "ok"  # disabled: nothing was expected to fire


# --------------------------------------------------------------------------- #
# N1 — slept through the minute: the grace fires on wake; past it, "missed"
# --------------------------------------------------------------------------- #


def test_recurring_jobs_carry_the_wake_from_sleep_grace_and_date_jobs_do_not(tmp_path):
    sched = Scheduler(_engine(tmp_path), lambda t: None)
    sched.add_task("c", "0 3 * * *", kind="event", payload={})
    sched.add_task("i", interval_seconds=3600, kind="event", payload={})
    sched.add_task("d", run_at=utcnow() + timedelta(days=1), kind="event", payload={})
    sched.start()
    try:
        assert sched.scheduler.get_job("c").misfire_grace_time == Scheduler.MISFIRE_GRACE_SECONDS
        assert sched.scheduler.get_job("i").misfire_grace_time == Scheduler.MISFIRE_GRACE_SECONDS
        assert Scheduler.MISFIRE_GRACE_SECONDS >= 120  # "a few minutes", not APScheduler's 1 s
        # _catch_up owns the date task's late story; its grace stays the default.
        assert sched.scheduler.get_job("d").misfire_grace_time == 1
    finally:
        sched.shutdown(wait=True)


def test_a_fire_missed_past_the_grace_is_written_on_the_row(tmp_path):
    from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent

    sched = Scheduler(_engine(tmp_path), lambda t: None)
    sched.add_task("nightly", "0 3 * * *", kind="event", payload={})
    stale = utcnow() - timedelta(hours=6)
    _set(sched.engine, "nightly", next_run=stale, last_status="ok", last_detail="done")
    when = datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc)
    # Through APScheduler's own dispatch, so the listener REGISTRATION is what
    # is under test, not a direct method call.
    sched.scheduler._dispatch_event(JobExecutionEvent(EVENT_JOB_MISSED, "nightly", None, when))
    row = sched.get("nightly")
    assert row.last_status == "missed"
    assert row.last_detail == "the app was asleep or busy at 2026-09-05 03:00"
    assert _due(sched, row) > utcnow()  # refreshed past the missed tick


# --------------------------------------------------------------------------- #
# AE11 — overlap: skipped on the row; Run-now: no second copy
# --------------------------------------------------------------------------- #


def test_overlapping_interval_fire_is_skipped_and_the_row_says_so(tmp_path, caplog):
    gate = threading.Event()
    calls: list[float] = []

    def slow(task):
        calls.append(time.monotonic())
        gate.wait(15)

    sched = Scheduler(_engine(tmp_path), slow)
    sched.add_task("tick", interval_seconds=1, kind="event", payload={})
    caplog.set_level(logging.WARNING, logger="apscheduler")
    sched.start()
    try:
        time.sleep(3.4)
        row = sched.get("tick")  # read WHILE the overlap persists
    finally:
        gate.set()
        sched.shutdown(wait=True)
    assert len(calls) == 1, calls  # skipped, not run concurrently
    assert any("maximum number of running instances" in r.getMessage() for r in caplog.records)
    assert row.last_status == "skipped"
    assert row.last_detail == "still running from the previous fire"
    assert _due(sched, row) > utcnow() - timedelta(seconds=2)


async def test_run_now_does_not_start_the_same_task_twice_at_once(tmp_path):
    gate = asyncio.Event()
    calls: list[int] = []

    async def cb(task):
        calls.append(1)
        await gate.wait()

    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task("job", "0 3 * * *", kind="task", payload={"task": "tidy the folder"})
    t1 = asyncio.create_task(sched.run_now("job"))
    await asyncio.sleep(0.05)
    second = await sched.run_now("job")
    try:
        assert len(calls) == 1, f"run_now started {len(calls)} concurrent fires"
        assert second.last_status == "skipped"
        assert second.last_detail == "already running"
        # The route re-reads the row, so the refusal must be ON the row.
        assert sched.get("job").last_detail == "already running"
    finally:
        gate.set()
        await t1
    # The claim is released: a later Run-now runs again.
    gate.clear()
    t3 = asyncio.create_task(sched.run_now("job"))
    await asyncio.sleep(0.05)
    assert len(calls) == 2
    gate.set()
    await t3


async def test_a_scheduled_tick_does_not_start_a_second_copy_beside_a_run_now(tmp_path):
    """APScheduler's max_instances only counts ITS fires; the in-flight set
    spans both doors."""
    gate = asyncio.Event()
    calls: list[int] = []

    async def cb(task):
        calls.append(1)
        await gate.wait()

    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task("job", "0 3 * * *", kind="task", payload={"task": "tidy"})
    t1 = asyncio.create_task(sched.run_now("job"))
    await asyncio.sleep(0.05)
    try:
        sched._fire("job")  # the APScheduler entrypoint, mid Run-now
        assert len(calls) == 1
        row = sched.get("job")
        assert row.last_status == "skipped"
        assert row.last_detail == "still running from the previous fire"
    finally:
        gate.set()
        await t1


# --------------------------------------------------------------------------- #
# AE12 — an unresolvable local zone: UTC + a doctor warning, never a boot abort
# --------------------------------------------------------------------------- #


def _no_zone():
    try:
        from zoneinfo import ZoneInfoNotFoundError
    except ImportError:  # pragma: no cover
        ZoneInfoNotFoundError = LookupError  # type: ignore[assignment]
    raise ZoneInfoNotFoundError("No time zone found with key Foo/Bar")


def test_build_platform_survives_an_unresolvable_local_timezone(tmp_path, monkeypatch):
    import apscheduler.schedulers.base as _base

    # BackgroundScheduler.__init__ -> configure -> get_localzone() at
    # construction; Scheduler.__init__ runs inside build_platform.
    monkeypatch.setattr(_base, "get_localzone", _no_zone)
    platform = build_platform(str(tmp_path))  # must boot (UTC fallback), not abort
    sched = platform.scheduler
    assert sched is not None
    assert sched.scheduler.timezone is timezone.utc
    assert "ZoneInfoNotFoundError" in sched.timezone_note
    # The doctor names it (RECOMMENDED, not a broken install).
    rows = {c["name"]: c for c in runtime_checks(platform)}
    tz = rows["scheduler_timezone"]
    assert tz["ok"] is False and tz["level"] == RECOMMENDED
    assert "UTC" in tz["detail"] and "ZoneInfoNotFoundError" in tz["detail"]
    assert tz["fix"]


def test_triggers_follow_the_fallback_zone_so_schedules_still_register(tmp_path, monkeypatch):
    """The scheduler booting is half the fix: every trigger re-resolves the
    local zone unless handed one, so an unfixed cron task would still abort
    start() on the same box."""
    import apscheduler.schedulers.base as _base
    import apscheduler.triggers.cron as _cron
    import apscheduler.triggers.interval as _interval

    monkeypatch.setattr(_base, "get_localzone", _no_zone)
    monkeypatch.setattr(_cron, "get_localzone", _no_zone)
    monkeypatch.setattr(_interval, "get_localzone", _no_zone)
    assert Scheduler.validate_cron("0 3 * * *") is True
    sched = Scheduler(_engine(tmp_path), lambda t: None)
    rec = sched.add_task("nightly", "0 3 * * *", kind="event", payload={})
    assert rec.next_run is not None
    sched.add_task("often", interval_seconds=3600, kind="event", payload={})
    sched.start()
    try:
        assert sched.scheduler.get_job("nightly") is not None
        assert sched.scheduler.get_job("often") is not None
    finally:
        sched.shutdown(wait=True)


def test_doctor_timezone_check_is_green_when_the_local_zone_resolves(platform):
    rows = {c["name"]: c for c in runtime_checks(platform)}
    assert rows["scheduler_timezone"]["ok"] is True
    assert platform.scheduler.timezone_note == ""


# --------------------------------------------------------------------------- #
# AE2 — Cancel reaches a session running on another thread's loop
# --------------------------------------------------------------------------- #


def test_cancel_reaches_a_schedule_fired_session_promptly(platform):
    orch = Orchestrator(platform)
    seen: dict = {}
    parked = threading.Event()

    class HangingRuntime:
        async def run(self, session, agent_def):
            parked.set()
            await asyncio.Event().wait()  # the model call that never returns

    orch.runtime = HangingRuntime()

    async def fire():
        # Exactly what a kind="task" schedule fire does on the scheduler
        # thread: create + run through the SHARED orchestrator.
        s = await orch.create_session("nightly tidy", AgentType.BUILDER, origin="schedule:nightly")
        seen["sid"] = s.id
        try:
            await orch.run_session(s.id)
        except asyncio.CancelledError:
            seen["ended"] = "cancelled"
            return
        seen["ended"] = "returned"

    worker = threading.Thread(target=lambda: asyncio.run(fire()), name="sched-worker", daemon=True)
    worker.start()
    assert parked.wait(5)
    for _ in range(100):  # run_session self-registers its task once it starts
        if orch._running.get(seen.get("sid")) is not None:
            break
        time.sleep(0.02)
    assert orch._running.get(seen["sid"]) is not None

    t0 = time.monotonic()
    got = orch.cancel_session(seen["sid"])  # the dashboard's Cancel, from another thread
    assert got.status is SessionStatus.CANCELLED  # the response reflects the request
    worker.join(timeout=3)
    took = time.monotonic() - t0
    assert not worker.is_alive(), (
        f"the schedule-fired session is still running {took:.1f}s after Cancel "
        f"(ended={seen.get('ended')}) — the foreign-loop task.cancel() never woke it"
    )
    assert took < 1.5
    assert seen["ended"] == "cancelled"
    row = orch.get_session(seen["sid"])
    assert row.status is SessionStatus.CANCELLED
    assert "cancelled" in (row.summary or "").lower()


@pytest.mark.asyncio
async def test_cancel_on_the_same_loop_still_cancels_directly(platform):
    """The same-loop path is unchanged: cancel lands through task.cancel()."""
    orch = Orchestrator(platform)
    started = asyncio.Event()

    class HangingRuntime:
        async def run(self, session, agent_def):
            started.set()
            await asyncio.Event().wait()

    orch.runtime = HangingRuntime()
    s = await orch.create_session("hang", AgentType.BUILDER)
    task = asyncio.create_task(orch.run_session(s.id))
    await started.wait()
    orch.cancel_session(s.id)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert orch.get_session(s.id).status is SessionStatus.CANCELLED
