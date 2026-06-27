"""Scheduler service (SPEC §25 cron — made durable).

The Trigger System (``workflows/triggers.py``) can schedule a callback on a
crontab, but it had no *persistent registry*: nothing survived a daemon restart
and nothing recorded when a task last ran or runs next. :class:`Scheduler` is
that registry. It wraps APScheduler's ``BackgroundScheduler`` and persists every
task as a :class:`ScheduledTaskRecord`, so the daemon can re-register all enabled
tasks on startup and fire each one's ``run_callback``.

The cron-validation approach mirrors ``workflows.triggers``: a crontab string is
parsed with ``CronTrigger.from_crontab`` (a bad expression raises ``ValueError``).
Nothing here ever sleeps waiting for a real fire — APScheduler owns the clock.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import Engine
from sqlalchemy import update as sql_update
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import KINDS, ScheduledTaskRecord

# A run_callback may be sync (returns None) or async (returns an awaitable).
RunCallback = Callable[[ScheduledTaskRecord], Awaitable[Any] | None]


def _cron_trigger(cron: str):
    """Parse a crontab string into a ``CronTrigger`` (raises ``ValueError``).

    Reuses the ``workflows.triggers`` validation approach
    (``CronTrigger.from_crontab``); a malformed expression becomes a clean
    ``ValueError`` instead of APScheduler's lower-level exception.
    """
    from apscheduler.triggers.cron import CronTrigger

    try:
        return CronTrigger.from_crontab(cron)
    except Exception as exc:  # noqa: BLE001 — normalise to ValueError
        raise ValueError(f"invalid cron expression {cron!r}: {exc}") from exc


def _date_trigger(run_at: datetime):
    """Build a one-time ``DateTrigger`` for ``run_at``."""
    from apscheduler.triggers.date import DateTrigger

    return DateTrigger(run_date=run_at)


def _interval_trigger(seconds: int):
    """Build a recurring ``IntervalTrigger`` firing every ``seconds`` seconds."""
    from apscheduler.triggers.interval import IntervalTrigger

    return IntervalTrigger(seconds=seconds)


def _parse_datetime(value: datetime | str) -> datetime:
    """Coerce ``value`` (a datetime or ISO-8601 string) to a datetime.

    Accepts a trailing ``Z`` (UTC). Raises ``ValueError`` on anything else.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(f"invalid run_at datetime {value!r}: {exc}") from exc
    raise ValueError(f"run_at must be a datetime or ISO string, got {type(value).__name__}")


def _next_fire(trigger) -> datetime | None:
    """Compute the next fire time for any APScheduler trigger (tz-aware), or None."""
    tz = getattr(trigger, "timezone", None)
    now = datetime.now(tz) if tz is not None else datetime.now(timezone.utc)
    try:
        return trigger.get_next_fire_time(None, now)
    except Exception:  # noqa: BLE001 — never let scheduling math break a call
        return None


class Scheduler:
    """Persistent registry of cron-scheduled tasks over APScheduler (SPEC §25).

    Construct with the shared ``engine`` and a ``run_callback`` invoked (with the
    task record) whenever a task fires — on a real cron tick *or* via
    :meth:`run_now`. The callback may be sync or async.
    """

    # Fire a one-time "date" task at most this many seconds late after the
    # daemon was down; anything more overdue is recorded *missed* (never fired).
    CATCHUP_WINDOW_SECONDS: int = 24 * 60 * 60

    def __init__(self, engine: Engine, run_callback: RunCallback) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        self.engine = engine
        self.run_callback = run_callback
        self.scheduler = BackgroundScheduler()

    # --- validation -------------------------------------------------------

    @staticmethod
    def validate_cron(expr: str) -> bool:
        """Return True iff ``expr`` is a valid 5-field crontab expression."""
        try:
            _cron_trigger(expr)
            return True
        except ValueError:
            return False

    # --- persistence helpers ---------------------------------------------

    def _fetch(self, db, name: str) -> ScheduledTaskRecord | None:
        return db.exec(
            select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == name)
        ).first()

    def get(self, name: str) -> ScheduledTaskRecord | None:
        """Return the persisted task named ``name`` (or None)."""
        with session_scope(self.engine) as db:
            return self._fetch(db, name)

    def list(self) -> list[ScheduledTaskRecord]:
        """Return all persisted scheduled tasks, oldest first."""
        with session_scope(self.engine) as db:
            return list(
                db.exec(
                    select(ScheduledTaskRecord).order_by(ScheduledTaskRecord.created_at)
                )
            )

    # --- trigger helpers --------------------------------------------------

    def _trigger_for_record(self, rec: ScheduledTaskRecord):
        """Build the APScheduler trigger matching ``rec.trigger_type``."""
        if rec.trigger_type == "date":
            return _date_trigger(rec.run_at)
        if rec.trigger_type == "interval":
            return _interval_trigger(rec.interval_seconds)
        return _cron_trigger(rec.cron)

    def _next_run_for_record(self, rec: ScheduledTaskRecord) -> datetime | None:
        """Compute the next fire time for ``rec`` from its trigger, or None."""
        try:
            return _next_fire(self._trigger_for_record(rec))
        except Exception:  # noqa: BLE001 — bad/expired trigger -> no next run
            return None

    # --- mutation ---------------------------------------------------------

    def add_task(
        self,
        name: str,
        cron: str | None = None,
        *,
        run_at: datetime | str | None = None,
        interval_seconds: int | None = None,
        kind: str = "workflow",
        payload: dict | None = None,
        enabled: bool = True,
    ) -> ScheduledTaskRecord:
        """Persist a new scheduled task and compute its first ``next_run``.

        Exactly one of ``cron`` (recurring crontab), ``run_at`` (one-time date),
        or ``interval_seconds`` (fixed repeat) must be supplied. Raises
        ``ValueError`` on a bad trigger, an unknown ``kind``, a duplicate
        ``name``, or the wrong number of triggers.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown task kind {kind!r}; expected one of {KINDS}")

        provided = [cron is not None, run_at is not None, interval_seconds is not None]
        if sum(provided) != 1:
            raise ValueError(
                "exactly one of cron, run_at, interval_seconds must be set"
            )
        if self.get(name) is not None:
            raise ValueError(f"scheduled task {name!r} already exists")

        trigger_type = "cron"
        run_at_dt: datetime | None = None
        interval: int | None = None
        if cron is not None:
            _cron_trigger(cron)  # validate — raises ValueError on a bad expression
            trigger_type = "cron"
        elif run_at is not None:
            run_at_dt = _parse_datetime(run_at)
            trigger_type = "date"
        else:
            interval = int(interval_seconds)
            if interval <= 0:
                raise ValueError("interval_seconds must be a positive integer")
            trigger_type = "interval"

        record = ScheduledTaskRecord(
            name=name,
            cron=cron or "",
            trigger_type=trigger_type,
            run_at=run_at_dt,
            interval_seconds=interval,
            kind=kind,
            payload_json=json.dumps(payload or {}, default=str),
            enabled=enabled,
        )
        record.next_run = self._next_run_for_record(record) if enabled else None
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)

        # If the scheduler is already live, register the job immediately.
        if enabled and self.scheduler.running:
            self._schedule_job(record)
        return record

    def remove(self, name: str) -> bool:
        """Delete a task (and unschedule its live job). Returns False if absent."""
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return False
            db.delete(rec)
            db.commit()
        self._unschedule_job(name)
        return True

    def enable(self, name: str, enabled: bool) -> ScheduledTaskRecord | None:
        """Toggle a task's ``enabled`` flag (and its live job). None if absent."""
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return None
            rec.enabled = enabled
            rec.next_run = self._next_run_for_record(rec) if enabled else None
            db.add(rec)
            db.commit()
            db.refresh(rec)

        if self.scheduler.running:
            if enabled:
                self._schedule_job(rec)
            else:
                self._unschedule_job(name)
        return rec

    # --- lifecycle --------------------------------------------------------

    def _schedule_job(self, task: ScheduledTaskRecord) -> None:
        self.scheduler.add_job(
            self._fire,
            trigger=self._trigger_for_record(task),
            args=[task.name],
            id=task.name,
            name=task.name,
            replace_existing=True,
        )

    def _unschedule_job(self, name: str) -> None:
        try:
            self.scheduler.remove_job(name)
        except Exception:  # noqa: BLE001 — job may not exist / scheduler stopped
            pass

    def start(self) -> None:
        """Register every enabled persisted task and start the scheduler.

        Idempotent: safe to call when already running and with zero tasks.

        Also reconciles one-time ``date`` tasks that came due while the daemon
        was down (see :meth:`_catch_up`). APScheduler drops a past-due
        ``DateTrigger`` on (re-)registration (misfire), so without this a
        one-time task whose ``run_at`` elapsed during downtime would silently
        never fire. The catch-up runs on its own daemon thread so a slow
        callback never blocks daemon startup.
        """
        for task in self.list():
            if task.enabled:
                self._schedule_job(task)
        if not self.scheduler.running:
            self.scheduler.start()
        threading.Thread(
            target=self._catch_up, name="sched-catchup", daemon=True
        ).start()

    def _catch_up(self) -> None:
        """Fire (or mark missed) one-time date tasks that came due while down.

        For each enabled, not-yet-run ``trigger_type == "date"`` task whose
        ``run_at`` is already in the past: fire it once immediately when it is
        only modestly late (within :data:`CATCHUP_WINDOW_SECONDS`), otherwise
        record it *missed* (clear ``next_run`` and disable it) without firing.
        Either way the task is disabled afterwards so a subsequent restart never
        double-fires it. Recurring ``cron``/``interval`` tasks are left untouched
        — APScheduler reschedules those normally. Never raises (a single bad task
        must not abort the sweep or crash startup).
        """
        now = utcnow()
        for task in self.list():
            if (
                not task.enabled
                or task.trigger_type != "date"
                or task.run_at is None
                or task.last_run is not None  # already fired — never double-fire
            ):
                continue
            run_at = task.run_at
            if run_at.tzinfo is None:  # SQLite round-trips datetimes as naive
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at >= now:
                continue  # still in the future — APScheduler owns this one
            late = (now - run_at).total_seconds()
            try:
                if late <= self.CATCHUP_WINDOW_SECONDS:
                    self._fire(task.name)  # reuse the normal fire + last_run path
                self._finish_catch_up(task.name)
            except Exception:  # noqa: BLE001 — keep reconciling the rest
                continue

    def _finish_catch_up(self, name: str) -> None:
        """Disable a reconciled date task and clear its next_run (no more fires)."""
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return
            rec.enabled = False
            rec.next_run = None
            db.add(rec)
            db.commit()
        self._unschedule_job(name)

    def shutdown(self, wait: bool = False) -> None:
        """Stop the scheduler if it is running (never raises when stopped)."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)

    # --- firing -----------------------------------------------------------

    def _mark_ran(self, name: str) -> ScheduledTaskRecord | None:
        """Record that ``name`` just ran: stamp last_run + recompute next_run."""
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return None
            rec.last_run = utcnow()
            if not rec.enabled or rec.trigger_type == "date":
                # A one-time date task does not fire again once it has run.
                rec.next_run = None
            else:
                rec.next_run = self._next_run_for_record(rec)
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec

    def _claim_once(self, name: str) -> bool:
        """Atomically claim a one-time ``date`` task so exactly one runner fires
        it. A task due within APScheduler's misfire grace at boot can be raced by
        the catch-up thread AND the APScheduler worker; a conditional UPDATE
        (``WHERE last_run IS NULL``) is the only race-safe claim in SQLite (a
        read-then-write across two transactions is not). Returns True if THIS
        caller won the claim (last_run is now stamped)."""
        with session_scope(self.engine) as db:
            result = db.execute(
                sql_update(ScheduledTaskRecord)
                .where(
                    ScheduledTaskRecord.name == name,
                    ScheduledTaskRecord.last_run.is_(None),
                    ScheduledTaskRecord.enabled.is_(True),
                )
                .values(last_run=utcnow(), next_run=None)
            )
            db.commit()
        return (result.rowcount or 0) == 1

    def _fire(self, name: str) -> None:
        """APScheduler job entrypoint (runs in the BackgroundScheduler thread)."""
        task = self.get(name)
        if task is None or not task.enabled:
            return
        if task.trigger_type == "date":
            # Claim atomically first so the catch-up + APScheduler paths can't
            # both fire the same one-time task.
            if not self._claim_once(name):
                return
            result = self.run_callback(task)
            if inspect.isawaitable(result):
                asyncio.run(result)
            return  # last_run / next_run already stamped by the claim
        result = self.run_callback(task)
        if inspect.isawaitable(result):
            # No event loop runs in the scheduler's worker thread, so drive the
            # coroutine to completion here.
            asyncio.run(result)
        self._mark_ran(name)

    async def run_now(self, name: str) -> ScheduledTaskRecord:
        """Invoke ``run_callback`` for ``name`` immediately and stamp last_run.

        Awaits the callback if it is async. Raises ``ValueError`` if no such task.
        """
        task = self.get(name)
        if task is None:
            raise ValueError(f"no scheduled task named {name!r}")
        result = self.run_callback(task)
        if inspect.isawaitable(result):
            await result
        return self._mark_ran(name) or task
