"""19-b (build-out plan R4.37, idea 19): THE RHYTHM of a repeat, and the
words an offer's schedule runs.

``learning/repeats.rhythm`` is PURE: the same weekday in consecutive weeks
(one skipped week at most) is weekly, the same day of the month (within two
days, a different month each time) is monthly, and the hour is the hour of
the median start. (An offer's words, the owner's line byte for byte, are
pinned through the served routes in ``test_next_leaps_19b_offer.py``.)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from iron_jarvis.learning import repeats as R

UTC = timezone.utc


def at(y: int, m: int, d: int, h: int = 10, mi: int = 0) -> float:
    return datetime(y, m, d, h, mi, tzinfo=UTC).timestamp()


# 2026-11-02, 09, 16, 23, 30 are Mondays.
MON = [at(2026, 11, 2), at(2026, 11, 9), at(2026, 11, 16), at(2026, 11, 23), at(2026, 11, 30)]


def test_three_mondays_in_a_row_are_weekly_at_the_median_hour():
    got = R.rhythm([MON[0], MON[1] + 3600 * 5, MON[2] - 3600], tz=UTC)
    assert got == R.Rhythm(kind="weekly", hour=10, weekday=0)
    assert got.cron == "0 10 * * mon"
    assert got.words == "each Monday at 10am"
    assert got.on_days_words == "on a Monday"


def test_the_hour_is_the_median_start_s_hour_not_the_mean():
    starts = [at(2026, 11, 2, 9, 40), at(2026, 11, 9, 9, 10), at(2026, 11, 16, 16, 0)]
    assert R.rhythm(starts, tz=UTC).hour == 9


def test_one_skipped_week_is_still_weekly_two_are_not():
    assert R.rhythm([MON[0], MON[1], MON[3]], tz=UTC).kind == "weekly"
    assert R.rhythm([MON[0], MON[2], MON[4]], tz=UTC) is None


def test_a_three_week_gap_is_no_rhythm():
    assert R.rhythm([MON[0], MON[1], MON[1] + 21 * 86400], tz=UTC) is None


def test_different_weekdays_are_no_weekly_rhythm():
    assert R.rhythm([MON[0], MON[1] + 86400, MON[2]], tz=UTC) is None


def test_fewer_than_three_days_are_no_rhythm():
    assert R.rhythm([MON[0], MON[1]], tz=UTC) is None
    # three tasks, but two on one day
    assert R.rhythm([MON[0], MON[0] + 600, MON[1]], tz=UTC) is None


def test_the_weekday_is_read_in_the_local_zone():
    late = [datetime(2026, 11, d, 23, 30, tzinfo=UTC).timestamp() for d in (2, 9, 16)]
    assert R.rhythm(late, tz=UTC).weekday == 0
    plus2 = timezone(timedelta(hours=2))
    got = R.rhythm(late, tz=plus2)
    assert got.weekday == 1 and got.hour == 1  # Tuesday, 1:30am there


def test_monthly_within_two_days_of_one_day_of_the_month():
    got = R.rhythm([at(2026, 9, 1), at(2026, 10, 3), at(2026, 11, 2)], tz=UTC)
    assert got == R.Rhythm(kind="monthly", hour=10, day=2)
    assert got.cron == "0 10 2 * *"
    assert got.words == "on the 2nd of each month at 10am"


def test_monthly_edges():
    assert R.rhythm([at(2026, 9, 1), at(2026, 10, 5), at(2026, 11, 2)], tz=UTC) is None  # 3 days past the 2nd
    assert R.rhythm([at(2026, 9, 1), at(2026, 10, 4), at(2026, 11, 2)], tz=UTC).day == 2  # 2 days: within
    # one skipped month is allowed, two are not
    assert R.rhythm([at(2026, 7, 1), at(2026, 9, 1), at(2026, 10, 1)], tz=UTC).kind == "monthly"
    assert R.rhythm([at(2026, 5, 1), at(2026, 7, 1), at(2026, 9, 1)], tz=UTC) is None
    # two in one month is no monthly rhythm
    assert R.rhythm([at(2026, 10, 1), at(2026, 10, 2), at(2026, 11, 1)], tz=UTC) is None
    # past the 28th a month would be missed: never offered
    assert R.rhythm([at(2026, 8, 30), at(2026, 9, 30), at(2026, 10, 30)], tz=UTC) is None


def test_a_group_reads_its_units_starts():
    units = [R.Unit(source="chat", ref=f"c{i}", start=s, words="w") for i, s in enumerate(MON[:3])]
    assert R.rhythm(R.Group(units=units)) == R.rhythm(MON[:3])


def test_clock_words():
    assert [R.clock_words(h) for h in (0, 9, 12, 16, 23)] == ["12am", "9am", "12pm", "4pm", "11pm"]


def test_the_cron_names_the_day_so_the_scheduler_reads_monday_as_monday():
    from apscheduler.triggers.cron import CronTrigger

    trig = CronTrigger.from_crontab(R.Rhythm(kind="weekly", hour=9, weekday=0).cron, timezone=UTC)
    nxt = trig.get_next_fire_time(None, datetime(2026, 11, 26, tzinfo=UTC))
    assert nxt.weekday() == 0 and nxt.hour == 9
