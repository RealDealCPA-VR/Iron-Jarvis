"""Recurrence rules: the dates of a repeating event.

The contract every rule keeps is in ``README.md``.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Iterable

#: Monday = 0 ... Sunday = 6; Saturday and Sunday are not business days.
WEEKEND = (5, 6)


def _check_start(start: object) -> None:
    # A datetime IS a date (a subclass), so it must be refused first.
    if isinstance(start, datetime) or not isinstance(start, date):
        raise TypeError("start must be a datetime.date (not a datetime)")


def _check_count(count: object, name: str = "count") -> None:
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError(f"{name} must be an int")
    if count < 0:
        raise ValueError(f"{name} must not be negative")


def _check_interval(interval: object) -> None:
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise TypeError("interval must be an int")
    if interval < 1:
        raise ValueError("interval must be at least 1")


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _next_month(day: date) -> date:
    """One month after ``day``: the same day of the month, or the month's last
    day when it is shorter; a date on a month's last day stays on month ends."""
    if day.month == 12:
        year, month = day.year + 1, 1
    else:
        year, month = day.year, day.month + 1
    last = _last_day(year, month)
    if day.day == _last_day(day.year, day.month):
        return date(year, month, last)  # end of month stays end of month
    return date(year, month, min(day.day, last))


def monthly(start: date, count: int, interval: int = 1) -> list[date]:
    """``count`` dates, one every ``interval`` months, the first being ``start``."""
    _check_start(start)
    _check_count(count)
    _check_interval(interval)
    dates: list[date] = []
    current = start
    for _ in range(count):
        dates.append(current)
        for _ in range(interval):
            current = _next_month(current)
    return dates


def weekly(start: date, count: int, interval: int = 1) -> list[date]:
    """``count`` dates, one every ``interval`` weeks, the first being ``start``."""
    _check_start(start)
    _check_count(count)
    _check_interval(interval)
    return [start + timedelta(weeks=i * interval) for i in range(count)]


def last_business_days(year: int, holidays: Iterable[date] = ()) -> list[date]:
    """The last business day of each month of ``year``: 12 dates, January first.

    A business day is a Monday to Friday that is not in ``holidays``.
    """
    _check_count(year, "year")
    closed = set(holidays)
    out: list[date] = []
    # A series on the 31st lands on every month's last day (README.md).
    for month_end in monthly(date(year, 1, 31), 12):
        day = month_end
        while day.weekday() in WEEKEND or day in closed:
            day -= timedelta(days=1)
        out.append(day)
    return out
