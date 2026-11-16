"""North-star plan 3.6 (v1.503.0): a night task whose own finish check found
failing tests or unmet points is not "done".

``harness/night.outcome_of`` read the reply line's error, stop, limits and
trust, and only an UNTRUSTED pass was downgraded (``routes/night``); a finish
record that said the tests FAILED, or that points of the request were still
open (``fin["unmet"]``), left the task "done" and the morning notice counted
it as done. The day-time "Your coding session finished" notice said the same.

Now either makes the task NEEDS_YOU with a note that says why, the morning
notice counts it there, and the finished notice carries the same verdict.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.comm import notifier as N
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import night as night
from iron_jarvis.harness import night_morning as morning


def _line(**finish):
    record = {"tests": "passed", "command": "pytest", "removed": 0, "kept": 0,
              "points": 3, "unmet": 0, "after": ""}
    record.update(finish)
    return {"role": "assistant", "content": "Done: the nav bar is fixed.", "finish": record}


TASK = {"id": "nt1", "notes": []}


def test_failing_tests_make_the_task_need_you():
    state, notes = night.outcome_of(TASK, _line(tests="failed"))
    assert state == night.NEEDS_YOU


def test_unmet_points_make_the_task_need_you_and_say_how_many():
    state, notes = night.outcome_of(TASK, _line(unmet=2))
    assert state == night.NEEDS_YOU
    assert ("unmet", "2 points of the request still need work.") in notes
    _, one = night.outcome_of(TASK, _line(unmet=1))
    assert ("unmet", "1 point of the request still needs work.") in one


@pytest.mark.parametrize("finish", [{}, {"tests": "none"}, {"tests": "not_run", "why": "no tests"}])
def test_a_finish_check_with_nothing_short_stays_done(finish):
    state, notes = night.outcome_of(TASK, _line(**finish))
    assert state == night.DONE
    assert not any(k == "unmet" for k, _ in notes)


def test_a_reply_with_no_finish_record_stays_done():
    state, _ = night.outcome_of(TASK, {"role": "assistant", "content": "ok"})
    assert state == night.DONE


def test_a_failed_reply_is_still_failed_not_needs_you():
    state, _ = night.outcome_of(TASK, {**_line(tests="failed"), "error": "The model failed."})
    assert state == night.FAILED


def test_the_morning_notice_counts_it_under_needs_you():
    task = {"id": "nt1", "words": "Fix the nav bar", "state": night.NEEDS_YOU,
            "notes": [{"kind": "unmet", "text": "2 points of the request still need work."}],
            "morning": {"tests": "failed"}}
    row = morning.summary_row(task)
    assert "2 points of the request still need work." in row["needs"]
    words = morning.notice_words([row])
    assert words["title"] == "Last night: 1 needs you"
    assert words["body"].startswith("Needs you: Fix the nav bar")


def test_finish_needs_you_reads_the_saved_record():
    assert night.finish_needs_you({"tests": "failed", "unmet": 0}) is True
    assert night.finish_needs_you({"tests": "passed", "unmet": 1}) is True
    assert night.finish_needs_you({"tests": "passed", "unmet": 0}) is False
    assert night.finish_needs_you(None) is False
    assert night.finish_needs_you({"tests": "passed", "unmet": True}) is False  # a bool is not a count


class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, type_, payload, session_id=None):
        self.events.append((type_, payload))


@pytest.mark.parametrize(("fin", "outcome"), [
    ({"tests": "failed", "unmet": 0}, "needs_you"),
    ({"tests": "passed", "unmet": 2}, "needs_you"),
    ({"tests": "passed", "unmet": 0}, "done"),
    (None, "done"),
])
def test_the_finished_notice_carries_the_same_verdict(monkeypatch, fin, outcome):
    bus = _Bus()
    platform = SimpleNamespace(event_bus=bus, engine=None)
    monkeypatch.setattr(build_routes.S, "get_row", lambda engine, tid: None)
    asyncio.run(build_routes._announce_turn_finished(platform, "t1", "turn1", "done", 75.0, fin=fin))
    [(_, payload)] = bus.events
    assert payload["outcome"] == outcome


def test_a_failed_turn_still_reads_failed(monkeypatch):
    bus = _Bus()
    monkeypatch.setattr(build_routes.S, "get_row", lambda engine, tid: None)
    asyncio.run(build_routes._announce_turn_finished(
        SimpleNamespace(event_bus=bus, engine=None), "t1", "turn1", "error", 75.0,
        fin={"tests": "failed", "unmet": 0},
    ))
    assert bus.events[0][1]["outcome"] == "failed"


def test_the_phone_words_for_a_finished_session_that_needs_you():
    assert N.build_finished_title({"outcome": "needs_you"}) == (
        "Your coding session finished, but something needs you"
    )
    assert N.build_finished_title({"outcome": "done"}) == "Your coding session finished"
    assert N.build_finished_title({"outcome": "failed"}) == "Your coding session could not finish"
