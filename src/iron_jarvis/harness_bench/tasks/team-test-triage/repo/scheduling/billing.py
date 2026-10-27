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


def billing_dates(anchor, count):
    """The first ``count`` billing dates of a plan started on ``anchor``
    (the anchor itself is the first)."""
    dates = [anchor]
    current = anchor
    while len(dates) < count:
        year, month = _following_month(current.year, current.month)
        current = _on_day(year, month, current.day)
        dates.append(current)
    return dates


def next_billing_date(anchor, after):
    """The first billing date of the plan strictly after the date ``after``."""
    current = anchor
    while current <= after:
        year, month = _following_month(current.year, current.month)
        current = _on_day(year, month, current.day)
    return current
