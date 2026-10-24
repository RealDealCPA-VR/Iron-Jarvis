"""Recurrence rules: the dates of a repeating event.

The contract every rule keeps is in ``README.md``.
"""

from __future__ import annotations

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


_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and year % 4 == 0:
        return 29
    return _DAYS_IN_MONTH[month - 1]


def _add_months(start: date, months: int) -> date:
    """``start``'s day of the month, ``months`` months later (clamped)."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(start.day, _days_in_month(year, month)))


def monthly(start: date, count: int, interval: int = 1) -> list[date]:
    """``count`` dates, one every ``interval`` months, the first being ``start``."""
    _check_start(start)
    _check_count(count)
    _check_interval(interval)
    return [_add_months(start, i * interval) for i in range(count)]


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
