"""Sales totals by day.

Every report counts an order on its UTC calendar day, whatever time zone the
shop that took it is in.
"""

import re
from datetime import date, timedelta

_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):\d{2}(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def order_day(order):
    """The UTC calendar day of an order, as "YYYY-MM-DD".

    ``order["created"]`` is an ISO 8601 timestamp with its own UTC offset
    ("2026-03-01T23:30:00-05:00", "2026-03-02T03:00:00+05:30", or a "Z" for
    UTC): 23:30 at -05:00 is 04:30 UTC the next day, so that order belongs to
    March 2.
    """
    day, hour, minute, zone = _STAMP.match(order["created"]).groups()
    offset = 0 if zone == "Z" else (1 if zone[0] == "+" else -1) * (int(zone[1:3]) * 60 + int(zone[4:6]))
    minutes = int(hour) * 60 + int(minute) - offset  # UTC minutes after the local day's midnight
    return (date.fromisoformat(day) + timedelta(days=minutes // (24 * 60))).isoformat()


def daily_totals(orders):
    """``{day: total cents}`` over the orders, by UTC day, days in order."""
    totals = {}
    for order in orders:
        day = order_day(order)
        totals[day] = totals.get(day, 0) + order["total_cents"]
    return dict(sorted(totals.items()))


def busiest_day(orders):
    """The UTC day with the most orders (the earliest such day on a tie), or
    None when there are no orders."""
    counts = {}
    for order in orders:
        day = order_day(order)
        counts[day] = counts.get(day, 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda day: (-counts[day], day))
