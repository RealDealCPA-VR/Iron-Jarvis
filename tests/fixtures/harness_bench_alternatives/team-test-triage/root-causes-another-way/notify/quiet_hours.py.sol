"""Quiet hours: when staff are not messaged."""

from datetime import datetime, time, timedelta

_DAY = 24 * 60 * 60 * 1_000_000


def _micros(t):
    return ((t.hour * 60 + t.minute) * 60 + t.second) * 1_000_000 + t.microsecond


def in_quiet_hours(moment, start=time(22, 0), end=time(7, 0)):
    """Whether the local time of ``moment`` (a datetime or a time) falls in
    the quiet hours from ``start`` (included) to ``end`` (excluded). The quiet
    hours may run past midnight (22:00 to 07:00, the default) or not (13:00
    to 14:30). ``start == end`` means there are no quiet hours."""
    now = moment.time() if isinstance(moment, datetime) else moment
    # Measure everything from the start, going round the clock: the quiet
    # hours are the first (end - start) of that, which is nothing when the
    # two are equal.
    since_start = (_micros(now) - _micros(start)) % _DAY
    length = (_micros(end) - _micros(start)) % _DAY
    return since_start < length


def next_send_time(moment, start=time(22, 0), end=time(7, 0)):
    """``moment`` itself when it is outside the quiet hours, otherwise the
    datetime at which they end."""
    if not in_quiet_hours(moment, start, end):
        return moment
    candidate = datetime.combine(moment.date(), end.replace(second=0, microsecond=0), moment.tzinfo)
    while candidate <= moment:
        candidate += timedelta(days=1)
    return candidate
