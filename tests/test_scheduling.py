"""Tests for the Scheduling subsystem (SPEC §25 cron, made durable).

Offline and non-blocking: we exercise the persistent registry and the
registration + ``run_now`` paths directly. We never wait for the scheduler to
fire a real cron tick.
"""

from __future__ import annotations

# Register the ScheduledTaskRecord table on SQLModel.metadata BEFORE init_db
# creates the tables. Must stay at the top.
import iron_jarvis.scheduling.models  # noqa: F401

from datetime import datetime, timedelta, timezone

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.scheduling.service import Scheduler


def _engine(tmp_path):
    engine = make_engine(tmp_path / "sched.db")
    init_db(engine)
    return engine


def _noop(task: ScheduledTaskRecord) -> None:
    return None


def test_add_task_persists_and_computes_next_run(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)

    rec = sched.add_task("monthly_close", "0 8 1 * *", payload={"name": "close"})

    assert rec.id.startswith("sched_")
    assert rec.name == "monthly_close"
    assert rec.cron == "0 8 1 * *"
    assert rec.kind == "workflow"
    assert rec.enabled is True
    assert rec.next_run is not None  # first fire time was computed
    assert rec.decoded_payload() == {"name": "close"}

    # It really landed in the database.
    fetched = sched.get("monthly_close")
    assert fetched is not None
    assert fetched.cron == "0 8 1 * *"


# -- new trigger types: one-time date + fixed interval ----------------------


def test_add_task_date_trigger_persists(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    run_at = datetime.now(timezone.utc) + timedelta(days=1)

    rec = sched.add_task("one_shot", run_at=run_at, kind="event")

    assert rec.trigger_type == "date"
    assert rec.run_at is not None
    assert rec.cron == ""  # cron stays empty for non-cron triggers
    assert rec.interval_seconds is None
    assert rec.next_run is not None  # first (and only) fire time was computed

    fetched = sched.get("one_shot")
    assert fetched is not None and fetched.trigger_type == "date"


def test_add_task_date_trigger_accepts_iso_string(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    run_at = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

    rec = sched.add_task("iso_shot", run_at=run_at)

    assert rec.trigger_type == "date"
    assert rec.run_at is not None
    assert rec.next_run is not None


def test_add_task_interval_trigger_persists(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)

    rec = sched.add_task("every_hour", interval_seconds=3600)

    assert rec.trigger_type == "interval"
    assert rec.interval_seconds == 3600
    assert rec.cron == ""
    assert rec.run_at is None
    assert rec.next_run is not None

    assert sched.get("every_hour").interval_seconds == 3600


def test_add_task_requires_exactly_one_trigger(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)

    with pytest.raises(ValueError):
        sched.add_task("no_trigger")  # none supplied

    with pytest.raises(ValueError):
        sched.add_task("two_triggers", "* * * * *", interval_seconds=60)  # two supplied

    # Nothing was persisted for either rejected task.
    assert sched.get("no_trigger") is None
    assert sched.get("two_triggers") is None


def test_interval_must_be_positive(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    with pytest.raises(ValueError):
        sched.add_task("zero", interval_seconds=0)


async def test_run_now_works_for_date_task(tmp_path):
    calls: list[ScheduledTaskRecord] = []

    def cb(task: ScheduledTaskRecord) -> None:
        calls.append(task)

    sched = Scheduler(_engine(tmp_path), cb)
    run_at = datetime.now(timezone.utc) + timedelta(days=1)
    sched.add_task("date_run", run_at=run_at, kind="event")

    updated = await sched.run_now("date_run")

    assert len(calls) == 1
    assert calls[0].name == "date_run"
    assert updated.last_run is not None
    # One-shot: a fired date task does not schedule another run.
    assert sched.get("date_run").next_run is None


def test_invalid_cron_raises_value_error(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)

    with pytest.raises(ValueError):
        sched.add_task("bad", "not a cron")

    # And nothing was persisted for the rejected task.
    assert sched.get("bad") is None


def test_unknown_kind_and_duplicate_name_raise(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)

    with pytest.raises(ValueError):
        sched.add_task("weird", "* * * * *", kind="nonsense")

    sched.add_task("dupe", "* * * * *")
    with pytest.raises(ValueError):
        sched.add_task("dupe", "* * * * *")


def test_list_get_remove_enable(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)

    sched.add_task("a", "* * * * *")
    sched.add_task("b", "0 0 * * *")

    names = {t.name for t in sched.list()}
    assert names == {"a", "b"}

    assert sched.get("a").name == "a"
    assert sched.get("missing") is None

    # enable toggles the flag and clears next_run when disabled.
    disabled = sched.enable("a", False)
    assert disabled is not None and disabled.enabled is False
    assert disabled.next_run is None
    assert sched.get("a").enabled is False
    assert sched.enable("missing", True) is None

    re_enabled = sched.enable("a", True)
    assert re_enabled.enabled is True
    assert re_enabled.next_run is not None

    assert sched.remove("a") is True
    assert sched.get("a") is None
    assert {t.name for t in sched.list()} == {"b"}
    assert sched.remove("missing") is False


async def test_run_now_invokes_sync_callback_and_stamps_last_run(tmp_path):
    calls: list[ScheduledTaskRecord] = []

    def cb(task: ScheduledTaskRecord) -> None:
        calls.append(task)

    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task("nightly", "0 0 * * *", kind="event", payload={"x": 1})

    assert sched.get("nightly").last_run is None

    updated = await sched.run_now("nightly")

    assert len(calls) == 1
    assert calls[0].name == "nightly"
    assert calls[0].kind == "event"
    assert updated.last_run is not None
    assert sched.get("nightly").last_run is not None


async def test_run_now_awaits_async_callback(tmp_path):
    calls: list[ScheduledTaskRecord] = []

    async def cb(task: ScheduledTaskRecord) -> None:
        calls.append(task)

    sched = Scheduler(_engine(tmp_path), cb)
    sched.add_task("hourly", "0 * * * *")

    updated = await sched.run_now("hourly")

    assert [t.name for t in calls] == ["hourly"]
    assert updated.last_run is not None


async def test_run_now_unknown_task_raises(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    with pytest.raises(ValueError):
        await sched.run_now("ghost")


def test_validate_cron(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    assert sched.validate_cron("0 8 1 * *") is True
    assert sched.validate_cron("not a cron") is False


def test_start_shutdown_zero_tasks(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    sched.start()  # no tasks registered — must not raise
    sched.shutdown()
    sched.shutdown()  # idempotent when already stopped


def test_start_shutdown_with_tasks(tmp_path):
    sched = Scheduler(_engine(tmp_path), _noop)
    sched.add_task("a", "* * * * *")
    sched.add_task("b", "0 0 * * *", enabled=False)  # disabled — not registered
    sched.start()
    # The enabled task got a live job; the disabled one did not.
    assert sched.scheduler.get_job("a") is not None
    assert sched.scheduler.get_job("b") is None
    sched.shutdown()
