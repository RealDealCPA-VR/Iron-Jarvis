"""Trigger persistence models (CX-05 "inbound everything").

``CalendarFiredRecord`` is the durable AT-MOST-ONCE cursor for the calendar
trigger: one row per calendar event UID that has already fired its ``calendar``
reflex rules. The :class:`~iron_jarvis.triggers.calendar.CalendarPoller` inserts
the row and commits *before* firing the rule, so a crash mid-fire drops the
in-flight signal on restart rather than double-firing a world-triggered action
(a duplicate side effect is worse than a missed one).

Importing this module before ``init_db`` registers the table on
``SQLModel.metadata`` so it auto-creates with the rest of the schema — exactly
like ``comm/models.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import utcnow


class CalendarFiredRecord(SQLModel, table=True):
    """The durable at-most-once cursor: a calendar event UID already fired."""

    #: the event's iCalendar UID (globally unique per event).
    event_uid: str = Field(primary_key=True)
    fired_at: datetime = Field(default_factory=utcnow)
