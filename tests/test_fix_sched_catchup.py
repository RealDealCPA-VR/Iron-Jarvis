"""Catch-up reconciliation for one-time date tasks (daemon-downtime survival).

APScheduler drops a past-due ``DateTrigger`` job on (re-)registration (misfire),
so a ``trigger_type == "date"`` task whose ``run_at`` elapsed while the daemon
was down would silently never fire. ``Scheduler._catch_up`` reconciles those on
``start()``: fire-once when only modestly late, mark *missed* when long past.

Offline and non-blocking: we drive ``_catch_up`` directly and never wait for a
real BackgroundScheduler tick.
"""

from __future__ import annotations

# Register the ScheduledTaskRecord table on SQLModel.metadata BEFORE init_db
# creates the tables. Must stay at the top.
import iron_jarvis.scheduling.models  # noqa: F401

from datetime import datetime, timedelta, timezone

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.scheduling.service import Scheduler


def _engine(tmp_path):
    engine = make_engine(tmp_path / "sched.db")
    init_db(engine)
    return engine


def _recorder():
    calls: list[str] = []

    def cb(task: ScheduledTaskRecord) -> None:
        calls.append(task.name)

    return calls, cb


def test_catchup_fires_recent_past_due_date_task_once(tmp_path):
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    run_at = datetime.now(timezone.utc) - timedelta(hours=1)  # 1h late: within window
    sched.add_task("recent_overdue", run_at=run_at, kind="event")

    sched._catch_up()

    # Fired exactly once via the normal callback path.
    assert calls == ["recent_overdue"]
    rec = sched.get("recent_overdue")
    assert rec.last_run is not None  # stamped as run
    assert rec.next_run is None  # one-shot — no future fire left
    assert rec.enabled is False  # done — a later restart won't re-fire it


def test_catchup_marks_long_past_date_task_missed_without_firing(tmp_path):
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    run_at = datetime.now(timezone.utc) - timedelta(days=3)  # far past the 24h window
    sched.add_task("stale_overdue", run_at=run_at, kind="event")

    sched._catch_up()

    # Never fired — only marked missed.
    assert calls == []
    rec = sched.get("stale_overdue")
    assert rec.last_run is None  # never ran
    assert rec.next_run is None  # stale next_run cleared
    assert rec.enabled is False  # disabled (missed)


def test_catchup_window_boundary_is_24h(tmp_path):
    assert Scheduler.CATCHUP_WINDOW_SECONDS == 24 * 60 * 60

    # Just inside the window fires; just outside does not.
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task(
        "edge_in",
        run_at=datetime.now(timezone.utc) - timedelta(hours=23, minutes=59),
        kind="event",
    )
    sched.add_task(
        "edge_out",
        run_at=datetime.now(timezone.utc) - timedelta(hours=24, minutes=1),
        kind="event",
    )

    sched._catch_up()

    assert calls == ["edge_in"]
    assert sched.get("edge_in").last_run is not None
    assert sched.get("edge_out").last_run is None


def test_catchup_leaves_future_date_task_alone(tmp_path):
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task(
        "future",
        run_at=datetime.now(timezone.utc) + timedelta(hours=2),
        kind="event",
    )

    sched._catch_up()

    assert calls == []
    rec = sched.get("future")
    assert rec.enabled is True  # untouched
    assert rec.next_run is not None  # APScheduler still owns it


def test_catchup_ignores_recurring_tasks(tmp_path):
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task("nightly", "0 0 * * *")  # cron
    sched.add_task("every_min", interval_seconds=60)  # interval

    sched._catch_up()

    assert calls == []
    assert sched.get("nightly").enabled is True
    assert sched.get("every_min").enabled is True


def test_catchup_does_not_refire_already_run_date_task(tmp_path):
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    run_at = datetime.now(timezone.utc) - timedelta(hours=1)
    sched.add_task("ran_once", run_at=run_at, kind="event")

    # Simulate the task having already fired before the daemon went down.
    sched._fire("ran_once")
    assert calls == ["ran_once"]
    assert sched.get("ran_once").last_run is not None
    calls.clear()

    sched._catch_up()

    # last_run is set, so catch-up must skip it (no double-fire).
    assert calls == []


def test_catchup_skips_disabled_past_due_date_task(tmp_path):
    calls, cb = _recorder()
    sched = Scheduler(_engine(tmp_path), cb)
    run_at = datetime.now(timezone.utc) - timedelta(hours=1)
    sched.add_task("off", run_at=run_at, kind="event", enabled=False)

    sched._catch_up()

    assert calls == []
    assert sched.get("off").enabled is False
