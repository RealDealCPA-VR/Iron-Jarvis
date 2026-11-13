"""Chat can list the scheduled tasks (v1.486.0).

Seen in the live runs: "move the daily 8 o'clock reminder to 9" had only the
writers (``schedule_update`` / ``schedule_delete`` take the schedule's NAME),
so the model asked the user which schedule was meant. ``schedule_list`` reads
every schedule (name, timing in plain words, what it does, on or paused, next
and last run); a message about a schedule offers it first, and a question
about schedules offers only it. An unknown name now says which schedules
there are.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.scheduling import tools as ST
from iron_jarvis.settings import records
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_settings_chat_tools_v1321 import _body
from tests.test_settings_records_v1321 import _app, _approve_all

pytestmark = pytest.mark.usefixtures("no_network")


def _rec(**kw):
    base = dict(trigger_type="cron", cron="", run_at=None, interval_seconds=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("rec,words", [
    (_rec(cron="0 8 * * *"), "every day at 8:00 AM (cron 0 8 * * *)"),
    (_rec(cron="30 17 * * 1-5"), "every weekday at 5:30 PM (cron 30 17 * * 1-5)"),
    (_rec(cron="0 9 * * 1"), "every Monday at 9:00 AM (cron 0 9 * * 1)"),
    (_rec(cron="0 9 * * 1,4"), "every Monday and Thursday at 9:00 AM (cron 0 9 * * 1,4)"),
    (_rec(cron="0 0 1 * *"), "on day 1 of every month at 12:00 AM (cron 0 0 1 * *)"),
    (_rec(cron="0 */2 * * 1"), "cron 0 */2 * * 1"),
    (_rec(cron="0 9 * 6 *"), "cron 0 9 * 6 *"),
    (_rec(trigger_type="interval", interval_seconds=3600), "every 1 hour"),
    (_rec(trigger_type="interval", interval_seconds=90), "every 90 seconds"),
])
def test_timing_is_said_in_words_and_anything_odd_is_kept_as_stored(rec, words):
    assert ST.when_words(rec) == words


@pytest.mark.parametrize("text,arms", [
    ("move the daily 8 o'clock reminder to 9", True),
    ("pause my weekly rent schedule", True),
    ("what schedules do I have?", True),
    ("I go to the gym every morning", False),
])
def test_a_schedule_named_offers_the_reader(text, arms):
    assert ("schedule_list" in records.record_tools_for(text)) is arms


def test_a_question_about_schedules_offers_only_the_reader():
    assert records.record_tools_for("what schedules do I have?") == ["schedule_list"]


@pytest.mark.asyncio
async def test_the_list_finds_the_schedule_and_the_change_lands(tmp_path):
    app = _app(tmp_path / "home", [
        ("schedule_list", {}),
        ("schedule_update", {"name": "morning-nudge", "cron": "0 9 * * *"}),
    ])
    sch = app.state.platform.scheduler
    sch.add_task("morning-nudge", "0 8 * * *", kind="task", payload={"task": "Remind me to check the bank feed"})
    sch.add_task("rent-list", "0 7 * * 1", kind="task", payload={"task": "List the rent due this week"},
                 enabled=False)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("move the daily 8 o'clock reminder to 9"), await _approve_all(app, seen))
    assert seen == ["schedule_update"], "listing needs no card; the change asks"
    listed = [d for ev, d in frames if ev == "tool_call" and d.get("name") == "schedule_list"
              and d.get("status") == "finished"]
    assert listed and listed[-1].get("ok") is True
    out = str(listed[-1].get("output") or "")
    assert "morning-nudge: every day at 8:00 AM" in out
    assert "does: Remind me to check the bank feed" in out
    assert "rent-list" in out and "paused" in out
    assert sch.get("morning-nudge").cron == "0 9 * * *"


@pytest.mark.asyncio
async def test_a_query_keeps_only_matching_schedules(tmp_path):
    app = _app(tmp_path / "home", [])
    p = app.state.platform
    p.scheduler.add_task("morning-nudge", "0 8 * * *", kind="task", payload={"task": "check the bank feed"})
    p.scheduler.add_task("rent-list", "0 7 * * 1", kind="task", payload={"task": "rent due"})
    tool = ST.ScheduleListTool(p)
    res = await tool.execute({"query": "bank"}, SimpleNamespace())
    assert res.ok and "morning-nudge" in res.output and "rent-list" not in res.output
    none = await tool.execute({"query": "payroll"}, SimpleNamespace())
    assert none.ok and "No scheduled task matches" in none.output and "rent-list" in none.output
    for name in ("morning-nudge", "rent-list"):
        p.scheduler.remove(name)
    empty = await tool.execute({}, SimpleNamespace())
    assert empty.output == "There are no scheduled tasks."


@pytest.mark.asyncio
async def test_an_unknown_name_says_which_schedules_there_are(tmp_path):
    app = _app(tmp_path / "home", [("schedule_update", {"name": "daily reminder", "cron": "0 9 * * *"})])
    sch = app.state.platform.scheduler
    sch.add_task("morning-nudge", "0 8 * * *", kind="task", payload={"task": "check the bank feed"})
    frames = await _drive_stream(app, _body("move my daily schedule to 9"), await _approve_all(app, []))
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("name") == "schedule_update"
                and d.get("status") == "finished"]
    text = str(finished[-1].get("output") or finished[-1].get("error") or "")
    assert "no schedule called 'daily reminder'" in text and "'morning-nudge'" in text
    assert sch.get("morning-nudge").cron == "0 8 * * *"


def test_the_reader_is_allowed_and_only_reads():
    from iron_jarvis.core.config import default_permissions
    from iron_jarvis.tools.base import Reversibility

    assert default_permissions().get("schedule_list") == "allow"
    assert ST.ScheduleListTool.reversibility == Reversibility.READONLY
