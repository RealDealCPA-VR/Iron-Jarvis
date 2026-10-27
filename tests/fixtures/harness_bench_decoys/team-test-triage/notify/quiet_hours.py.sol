"""Quiet hours: when staff are not messaged."""

from datetime import datetime, time, timedelta


def in_quiet_hours(moment, start=time(22, 0), end=time(7, 0)):
    """Whether the local time of ``moment`` (a datetime or a time) falls in
    the quiet hours from ``start`` (included) to ``end`` (excluded). The quiet
    hours may run past midnight (22:00 to 07:00, the default) or not (13:00
    to 14:30). ``start == end`` means there are no quiet hours."""
    now = moment.time() if isinstance(moment, datetime) else moment
    if start < end:
        return start <= now < end
    # Past midnight: quiet from the start until the end.
    return now >= start or now < end


def next_send_time(moment, start=time(22, 0), end=time(7, 0)):
    """``moment`` itself when it is outside the quiet hours, otherwise the
    datetime at which they end."""
    if not in_quiet_hours(moment, start, end):
        return moment
    candidate = moment.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    return candidate
