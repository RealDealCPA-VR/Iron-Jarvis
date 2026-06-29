"""Scheduling subsystem (SPEC §25 cron — made durable).

Persistent **scheduled tasks** that fire on a crontab and run an action
(workflow / event). This is the registry that makes the daemon's cron
scheduling real: enabled tasks survive restarts and are re-registered on startup.

Importing :mod:`iron_jarvis.scheduling.models` before ``init_db`` registers the
``ScheduledTaskRecord`` table on ``SQLModel.metadata``.
"""

from __future__ import annotations

from .models import KINDS, ScheduledTaskRecord
from .service import Scheduler

__all__ = ["KINDS", "ScheduledTaskRecord", "Scheduler"]
