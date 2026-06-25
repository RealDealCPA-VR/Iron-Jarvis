"""Scheduled-task persistence model (SPEC §25 cron, made durable).

``ScheduledTaskRecord`` is the persistent registry the daemon's scheduler reads
on startup: each row is a named cron-fired task bound to an *action* (run a
workflow, emit an event, or invoke a callback). It is a plain SQLModel table;
importing this module before ``init_db`` registers the table on
``SQLModel.metadata`` so it auto-creates.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

# The action kinds a scheduled task may carry.
KINDS: tuple[str, ...] = ("workflow", "event", "callback")

# The trigger kinds a scheduled task may fire on.
TRIGGER_TYPES: tuple[str, ...] = ("cron", "date", "interval")


class ScheduledTaskRecord(SQLModel, table=True):
    """A persistent scheduled task (registry row for the Scheduler).

    A task fires on one of three triggers: a recurring ``cron`` expression, a
    one-time ``date`` (``run_at``), or a fixed ``interval`` (``interval_seconds``).
    ``trigger_type`` records which; the unused fields stay empty/None.
    """

    id: str = Field(default_factory=lambda: new_id("sched"), primary_key=True)
    name: str = Field(index=True, unique=True)
    cron: str = ""  # crontab expression (empty for date/interval triggers)
    trigger_type: str = "cron"  # cron | date | interval
    run_at: datetime | None = None  # one-time fire time (date trigger)
    interval_seconds: int | None = None  # repeat period (interval trigger)
    kind: str = "workflow"  # workflow | event | callback
    payload_json: str = "{}"
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)

    def decoded_payload(self) -> dict:
        """Parse ``payload_json`` into a dict (action arguments)."""
        try:
            return json.loads(self.payload_json or "{}")
        except (TypeError, ValueError):
            return {}
