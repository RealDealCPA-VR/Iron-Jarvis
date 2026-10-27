"""Meetings that repeat on "the second Tuesday" or "the last Friday" of a
month."""

import calendar
from datetime import date

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def nth_weekday(year, month, weekday, n):
    """The date of the n-th ``weekday`` (0 = Monday ... 6 = Sunday) of the
    month. ``n`` = 1 is the first, 2 the second, up to 5; ``n`` = -1 is the
    LAST one in the month, -2 the one before it, down to -5. ValueError when
    ``n`` is 0 or outside -5..5, or when the month has no such day (most
    months have no fifth Monday)."""
    if n == 0 or not -5 <= n <= 5:
        raise ValueError(f"n must be 1..5 or -1..-5, not {n}")
    if n > 0:
        first = date(year, month, 1)
        day = 1 + (weekday - first.weekday()) % 7 + 7 * (n - 1)
    else:
        last = date(year, month, calendar.monthrange(year, month)[1])
        day = last.day - (last.weekday() - weekday) % 7 - 7 * (-n - 1)
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        raise ValueError(f"{year}-{month:02d} has no such {WEEKDAYS[weekday]}")
    return date(year, month, day)


def describe(weekday, n):
    """"the second Tuesday", "the last Friday", "the second-to-last Monday"."""
    names = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last", -2: "second-to-last"}
    return f"the {names.get(n, str(n))} {WEEKDAYS[weekday]}"
