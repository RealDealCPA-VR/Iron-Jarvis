"""Export a series of dates as iCalendar text: one all-day event per date."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable

from .rules import monthly


def slug(text: str) -> str:
    """``text`` in lower case, every run of other characters than a-z and 0-9
    made one ``-``; ``"event"`` when nothing is left."""
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out or "event"


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _yyyymmdd(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}{day.day:02d}"


def export_series(summary: str, dates: Iterable[date]) -> str:
    """The iCalendar text of an event called ``summary`` on each of ``dates``.

    The dates must be ``datetime.date`` values in strictly increasing order
    (``ValueError`` otherwise; ``TypeError`` for a datetime). Event ``n``
    (counting from 1) gets the UID ``<slug(summary)>-<n>@recur``. Every line
    ends with CRLF, as iCalendar requires.
    """
    days = list(dates)
    for day in days:
        if isinstance(day, datetime) or not isinstance(day, date):
            raise TypeError("dates must be datetime.date values (not datetimes)")
    for earlier, later in zip(days, days[1:]):
        if later <= earlier:
            raise ValueError("dates must be in strictly increasing order")
    uid = slug(summary)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//recur//EN"]
    for n, day in enumerate(days, start=1):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}-{n}@recur",
            f"DTSTART;VALUE=DATE:{_yyyymmdd(day)}",
            f"SUMMARY:{_escape(summary)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def export_monthly(summary: str, start: date, count: int, interval: int = 1) -> str:
    """The iCalendar text of a monthly series (``rules.monthly``)."""
    return export_series(summary, monthly(start, count, interval))
