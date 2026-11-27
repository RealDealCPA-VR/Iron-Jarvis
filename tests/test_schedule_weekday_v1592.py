"""A schedule's day of the week is the crontab's: "0 9 * * 1" is Monday.

APScheduler 3 counts weekdays from Monday = 0; a crontab counts from
Sunday = 0 (or 7). Read as numbers, the Schedules page's "Weekly Mon 9am"
ran on Tuesday. The day field is written as names before it is read.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from iron_jarvis.scheduling.service import _cron_trigger, crontab_days_named
from iron_jarvis.workflows.triggers import validate_cron

FRIDAY = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)  # a Friday


def _next_days(cron: str, n: int = 7) -> list[str]:
    t = _cron_trigger(cron, timezone.utc)
    out, prev, now = [], None, FRIDAY
    for _ in range(n):
        nxt = t.get_next_fire_time(prev, now)
        out.append(nxt.strftime("%a"))
        prev, now = nxt, nxt
    return out


@pytest.mark.parametrize(("cron", "days"), [
    ("0 9 * * 1", {"Mon"}),
    ("0 9 * * 0", {"Sun"}),
    ("0 9 * * 7", {"Sun"}),
    ("0 9 * * 1-5", {"Mon", "Tue", "Wed", "Thu", "Fri"}),
    ("0 9 * * 0,6", {"Sat", "Sun"}),
    ("0 9 * * 5-7", {"Fri", "Sat", "Sun"}),
    ("0 9 * * */2", {"Sun", "Tue", "Thu", "Sat"}),
    ("0 9 * * mon", {"Mon"}),
])
def test_a_crontab_day_runs_on_that_day(cron, days):
    assert set(_next_days(cron, 9)) == days, cron


def test_every_day_and_named_days_are_left_as_written():
    assert crontab_days_named("0 9 * * *") == "0 9 * * *"
    assert crontab_days_named("0 9 * * mon-fri") == "0 9 * * mon-fri"
    assert len(set(_next_days("0 9 * * *"))) == 7


def test_the_weekly_monday_schedule_runs_on_the_coming_monday():
    t = _cron_trigger("0 9 * * 1", timezone.utc)
    assert t.get_next_fire_time(None, FRIDAY) == datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)


def test_sunday_as_seven_is_a_valid_cron_and_a_bad_day_is_not():
    assert validate_cron("0 9 * * 7")
    assert not validate_cron("0 9 * * 8")
    with pytest.raises(ValueError):
        _cron_trigger("0 9 * * 9", timezone.utc)
