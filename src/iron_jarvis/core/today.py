"""Today's date, said to a model the same way in every kind of work.

Until the intelligence plan's release 7 only a coding session's prompt named
the date (``harness/mode``); a plain chat, an agent run, a mission and a
workflow's model step did not, so "what is due next Friday?" was answered from
whatever year the model was trained in. This is the ONE helper they all call.

* The date only, never the time of day: a line that changed every minute would
  change what a model server has already read on every message.
* In the owner's local time zone (the PC's own), through :data:`now`, the
  injected clock: a test sets it, nothing here sleeps or reads another clock.
* Where it rides: a plain chat reply carries it BESIDE the newest message
  (``context/prefix``), so a chat open across midnight re-reads nothing; an
  agent run, a mission's steps and a workflow's model step carry it in the
  prompt they are built with, once, so a run that crosses midnight keeps the
  date it began on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable


def _local_now() -> datetime:
    return datetime.now().astimezone()


#: The injected clock: a callable answering "now" in the owner's local time.
#: A test replaces it (``monkeypatch.setattr(today, "now", ...)``).
now: Callable[[], datetime] = _local_now


def today_iso() -> str:
    """Today's local date, ``YYYY-MM-DD``. Never raises ("" when the clock fails)."""
    try:
        return now().date().isoformat()
    except Exception:  # noqa: BLE001 — a date line is never worth a failed reply
        return ""


def today_line(day: "str | None" = None) -> str:
    """The one sentence every prompt says: ``Today's date is 2026-11-26.``
    (a coding session's own words since v1.366.0). "" when the date is not
    known. *day* is a date already read (a coding session reads it once per
    reply)."""
    day = today_iso() if day is None else str(day or "")
    return f"Today's date is {day}." if day else ""


def today_block(day: "str | None" = None) -> str:
    """:func:`today_line` as a prompt section to append (``\\n\\n`` first), or ""."""
    line = today_line(day)
    return f"\n\n{line}" if line else ""


__all__ = ["now", "today_block", "today_iso", "today_line"]
