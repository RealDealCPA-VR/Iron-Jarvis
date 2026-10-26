"""THE NIGHT WINDOW (v1.398.0, night shift N1 — ``docs/NIGHT-SHIFT-PLAN.md``,
decision 4: "night tasks start between 11 PM and 7 AM; a task not started by
7 AM waits for the next night").

Pure and import-light (``core/config`` reads :func:`clean_time` at load): a
time of day is ``"HH:MM"`` in LOCAL time, and the window may cross midnight
(23:00 → 07:00, the default). A window whose start equals its end is empty:
nothing starts (said in words, never read as "all day").
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

_TIME_RX = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def clean_time(raw: Any) -> str:
    """``"7:00"`` / ``" 23:00 "`` → ``"07:00"`` / ``"23:00"``; "" when the
    value is not a time of day (00:00 to 23:59). Never raises."""
    if not isinstance(raw, str):
        return ""
    m = _TIME_RX.match(raw)
    if not m:
        return ""
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def check_time(value: Any) -> None:
    """The settings writer's check (``settings/schema``): refuses in words."""
    if not clean_time(value):
        raise ValueError("must be a time of day like 23:00 (hours 00 to 23, minutes 00 to 59)")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(now: datetime, start: str, end: str) -> bool:
    """Is the local time ``now`` inside the window ``[start, end)``? A window
    that crosses midnight wraps; ``start == end`` is empty."""
    s, e = clean_time(start), clean_time(end)
    if not s or not e or s == e:
        return False
    at = now.hour * 60 + now.minute
    a, b = _minutes(s), _minutes(e)
    if a < b:
        return a <= at < b
    return at >= a or at < b


def next_start(now: datetime, start: str, end: str) -> "datetime | None":
    """When the window next opens after ``now`` (``now`` itself when inside
    it). None for an empty window."""
    s, e = clean_time(start), clean_time(end)
    if not s or not e or s == e:
        return None
    if in_window(now, s, e):
        return now
    h, m = (int(x) for x in s.split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at


def window_end(now: datetime, start: str, end: str) -> "datetime | None":
    """When the window that holds ``now`` closes (None when ``now`` is not
    inside one)."""
    s, e = clean_time(start), clean_time(end)
    if not in_window(now, s, e):
        return None
    h, m = (int(x) for x in e.split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at


def clock_words(hhmm: str) -> str:
    """``"23:00"`` → ``"11:00 PM"``, ``"07:00"`` → ``"7:00 AM"``."""
    s = clean_time(hhmm)
    if not s:
        return ""
    h, m = (int(x) for x in s.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def window_words(now: datetime, start: str, end: str) -> str:
    """The window in one plain sentence, from ``now``: "Night tasks run now,
    until 7:00 AM." / "Starts at 11:00 PM tonight." / "Starts at 1:00 AM
    tomorrow." / "The night window is empty: …"."""
    s, e = clean_time(start), clean_time(end)
    if not s or not e or s == e:
        return "The night window starts and ends at the same time, so night tasks never start. Change it in Settings."
    if in_window(now, s, e):
        return f"Night tasks run now, until {clock_words(e)}."
    nxt = next_start(now, s, e)
    if nxt is None:  # pragma: no cover — handled above
        return ""
    if nxt.date() == now.date():
        day = "tonight" if nxt.hour >= 17 else "today"
    elif nxt.date() == (now + timedelta(days=1)).date():
        day = "tomorrow"
    else:  # pragma: no cover — a window always opens within a day
        day = nxt.strftime("%A")
    return f"Starts at {clock_words(s)} {day}."


__all__ = [
    "check_time", "clean_time", "clock_words", "in_window", "next_start", "window_end", "window_words",
]
