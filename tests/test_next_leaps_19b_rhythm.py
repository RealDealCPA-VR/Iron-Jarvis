"""19-b (build-out plan R4.37, idea 19): THE RHYTHM of a repeat, and the
words an offer's schedule runs.

``learning/repeats.rhythm`` is PURE: the same weekday in consecutive weeks
(one skipped week at most) is weekly, the same day of the month (within two
days, a different month each time) is monthly, and the hour is the hour of
the median start. An offer's words are the owner's own line, byte for byte:
never a model's words, never the skill's name.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from iron_jarvis.learning import repeats as R
from iron_jarvis.scheduling import offers as O

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


# --------------------------------------------------------------------------- #
# an offer's words are the owner's line, byte for byte
# --------------------------------------------------------------------------- #
OWNER = "turn the bank export into a summary sheet with totals per category"


def _prop(units: list[dict], name: str = "bank-export-summary") -> SimpleNamespace:
    return SimpleNamespace(id="prop1", skill_name=name, evidence_json=json.dumps({"units": units, "days": []}))


def _unit(start: float, words: str = OWNER, **kw) -> dict:
    out = {"thread": "chat_new", "start": start, "words": words, "provider": "claude-cli",
           "model": "claude-sonnet-4-6", "account": "", "local_only": False}
    out.update(kw)
    return out


def test_an_offer_s_words_are_the_owner_s_line_byte_for_byte(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    older = "turn this bank export into a summary sheet with the totals per category"
    prop = _prop([_unit(MON[2]), _unit(MON[1], words=older), _unit(MON[0], words=older)])
    offer, why = O.offer_for(engine, prop, tz=UTC)
    assert why == "" and offer is not None
    assert offer.words == OWNER  # the newest task's own line
    assert "bank-export-summary" not in offer.words and "skill" not in offer.words.lower()
    assert offer.cadence == "0 10 * * mon"
    assert (offer.provider, offer.model) == ("claude-cli", "claude-sonnet-4-6")


def test_no_offer_without_a_rhythm_a_model_or_a_local_only_newest_chat(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine

    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    off_rhythm = _prop([_unit(MON[2]), _unit(MON[1] + 86400), _unit(MON[0])])
    assert O.offer_for(engine, off_rhythm, tz=UTC)[0] is None
    no_model = _prop([_unit(MON[2], model=""), _unit(MON[1]), _unit(MON[0])])
    assert O.offer_for(engine, no_model, tz=UTC) == (None, "the model the chats used is not on record")
    local_older = _prop([_unit(MON[2]), _unit(MON[1], local_only=True), _unit(MON[0])])
    assert O.offer_for(engine, local_older, tz=UTC)[0] is None
    unsaid = _prop([_unit(MON[2], local_only=True), _unit(MON[1], local_only=True),
                    {k: v for k, v in _unit(MON[0]).items() if k != "local_only"}])
    got, _ = O.offer_for(engine, unsaid, tz=UTC)
    assert got is not None and got.local_only is True  # unknown = Local only
