"""Public holidays on fixed dates, and the day each one is observed."""

from __future__ import annotations

from datetime import date, timedelta

#: (name, month, day) of the holidays that fall on the same date every year.
FIXED_HOLIDAYS = (
    ("New Year's Day", 1, 1),
    ("Independence Day", 7, 4),
    ("Christmas Day", 12, 25),
)


def observed(day: date) -> date:
    """The day a holiday on ``day`` is observed: a Saturday holiday is
    observed on the Friday before, a Sunday holiday on the Monday after."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def holidays_for(year: int) -> set[date]:
    """Every fixed holiday OBSERVED during ``year``, as a set of dates.

    New Year's Day of ``year + 1`` falling on a Saturday is observed on
    Friday December 31 of ``year`` — so it is included here; and New Year's
    Day of ``year`` falling on a Saturday is not (it was observed in
    ``year - 1``).
    """
    out: set[date] = set()
    for y in (year, year + 1):
        for _name, month, day in FIXED_HOLIDAYS:
            when = observed(date(y, month, day))
            if when.year == year:
                out.add(when)
    return out
