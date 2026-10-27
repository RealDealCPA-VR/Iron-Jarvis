"""Monthly billing dates.

A monthly plan bills on its ANCHOR day: the day of the month it started. In a
month too short for that day it bills on the month's last day instead, and the
next month it is back on the anchor day — a plan started on January 31 bills
on February 28 (29 in a leap year), then March 31, April 30, May 31, ...
"""

import calendar
from datetime import date


def _following_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _on_day(year, month, day):
    """``day`` of that month, or the month's last day when it is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _nth(anchor, n):
    """The n-th billing date after the anchor (n = 0 is the anchor)."""
    index = anchor.month - 1 + n
    return _on_day(anchor.year + index // 12, index % 12 + 1, anchor.day)


def billing_dates(anchor, count):
    """The first ``count`` billing dates of a plan started on ``anchor``
    (the anchor itself is the first)."""
    return [_nth(anchor, n) for n in range(max(count, 1))]


def next_billing_date(anchor, after):
    """The first billing date of the plan strictly after the date ``after``."""
    n = 0
    while _nth(anchor, n) <= after:
        n += 1
    return _nth(anchor, n)
