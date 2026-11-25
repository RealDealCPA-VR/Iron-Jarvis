"""12-a (build-out plan R2.5): what the phone hears when the coding work it
started starts and ends, read from the TASK'S RECORD (its state, its notes,
the settle's morning facts: tests, files changed, branch, commit), never
the model's words, masked as the night summary masks what reaches a phone,
and a Local only task's words and branch left out. "Checks failed" when its
tests failed; "Needs you" with the first reason.

One notice per outcome: a phone task's ending goes to the phone chat that
pressed Start — never also into a night's summary (a night with only phone
work makes none) — and a task the user stopped themselves says nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from iron_jarvis.comm import Notifier
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_morning as M
from iron_jarvis.harness import phone_code as PC
from tests._fakes.phone_back import FakeTelegramBack, telegram_back
from tests._fakes.phone_files import ME

pytestmark = pytest.mark.usefixtures("no_network")

REPORT = {"channel": "tg", "sender_id": str(ME), "thread_id": "", "reply_to": str(ME), "name": "website"}


def _local(h: int, m: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 10, day, h, m).astimezone()


def _put(engine, tid: str, state: str, *, phone: bool = True, morning: dict | None = None,
         notes: list | None = None, night: str = "2026-10-26", **over) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = N.NightTaskRecord(
        id=tid, folder="C:\\code\\website", words="fix the broken contact form", state=state, night=night,
        branch=f"ij/fix-the-broken-contact-for-{tid}", started_at=now, ended_at=now if state in N.ENDED else None,
        morning_json=json.dumps(morning or {}), notes_json=json.dumps(notes or []),
        asked_from="comm:tg" if phone else "", report_json=json.dumps(REPORT) if phone else "",
        start_now=phone, **over,
    )
    with session_scope(engine) as db:
        db.add(row)
        db.commit()
    return N.get_task(engine, tid)


def _note(kind: str, text: str) -> dict:
    return {"kind": kind, "text": text, "at": None}


DONE_FACTS = {"tests": "passed", "tests_trusted": True, "files": 3, "added": 20, "removed": 4,
              "commit": {"sha": "abc1234def5678", "subject": "x", "files": 3}, "local_only": False}


def test_done_says_files_tests_branch_and_nothing_is_merged(platform):
    task = _put(platform.engine, "a", N.DONE, morning=DONE_FACTS, commit_sha="abc1234def5678")
    assert PC.ending_words(task, REPORT) == (
        "Done in website: 3 files changed; tests passed. Its work is committed on the branch "
        "ij/fix-the-broken-contact-for-a; nothing is merged."
    )


def test_checks_failed_and_needs_you_say_why_from_the_record(platform):
    failed = _put(platform.engine, "b", N.FAILED, morning={**DONE_FACTS, "tests": "failed"},
                  commit_sha="abc1234def5678",
                  notes=[_note("tests", "Its tests failed.")])
    words = PC.ending_words(failed, REPORT)
    assert words.startswith("Checks failed in website: ") and "tests failed" in words
    needs = _put(platform.engine, "c", N.NEEDS_YOU, morning={**DONE_FACTS, "tests": ""},
                 notes=[_note("declined", "Needs your OK: run web_fetch (nobody answered in 15 minutes, so it was skipped).")])
    words = PC.ending_words(needs, REPORT)
    assert words.startswith("Needs you in website: Needs your OK: run web_fetch (nobody answered in 15 minutes")
    broken = _put(platform.engine, "d", N.FAILED, morning={},
                  notes=[_note("error", "This folder cannot be used: it does not exist")])
    assert PC.ending_words(broken, REPORT).startswith(
        "Failed in website: This folder cannot be used: it does not exist.")
    stopped = _put(platform.engine, "e", N.STOPPED, morning={},
                   notes=[_note("limit", "Stopped: it ran past its 90-minute limit.")])
    assert PC.ending_words(stopped, REPORT).startswith(
        "Stopped in website: Stopped: it ran past its 90-minute limit.")


#: Split so no secret-shaped literal sits in the file (the repository is
#: public; the night summary's tests build theirs the same way).
GITHUB_SHAPE = "ghp" + "_" + "x1Y2" * 10


def test_a_secret_in_a_note_is_masked_and_a_local_only_task_says_where_to_look(platform):
    leaky = _put(platform.engine, "f", N.NEEDS_YOU, morning={},
                 notes=[_note("declined", f"Needs your OK: run curl -H token={GITHUB_SHAPE}")])
    words = PC.ending_words(leaky, REPORT)
    assert GITHUB_SHAPE not in words and words.startswith("Needs you in website: Needs your OK: run curl")
    local = _put(platform.engine, "g", N.DONE, morning={**DONE_FACTS, "local_only": True})
    words = PC.ending_words(local, REPORT)
    assert words == f"Done in website: {M.LOCAL_ONLY_LINE}. Nothing is merged."
    assert "ij/" not in words
    started = PC.started_words(local, REPORT)
    assert started.startswith("Started in website, on a branch of its own.") and "ij/" not in started


def test_started_names_the_branch_and_the_limit(platform):
    task = _put(platform.engine, "h", N.RUNNING, morning={})
    assert PC.started_words(task, REPORT, platform.config) == (
        "Started on branch ij/fix-the-broken-contact-for-h in website. It may take up to 90 minutes; "
        "I'll tell you how it ends. Nothing is merged."
    )


# --------------------------------------------------------------------------- #
# delivery: to the chat that pressed Start, from the record
# --------------------------------------------------------------------------- #


def _phone(platform):
    fake = FakeTelegramBack([])
    notifier = Notifier()
    notifier.add_channel("tg", telegram_back(fake, chat_id=ME))
    store = CommThreadStore(platform.engine)
    store.resolve("tg", str(ME), "Val")
    return fake, notifier, store


def _event(task_id: str, step: str) -> dict:
    return {"type": EventType.BUILD_PHONE_CODE, "payload": {"task_id": task_id, "step": step}}


def test_the_ending_reaches_the_chat_that_pressed_start_and_its_line_on_the_pc(platform):
    fake, notifier, store = _phone(platform)
    task = _put(platform.engine, "a", N.DONE, morning=DONE_FACTS, commit_sha="abc1234def5678")
    out = PC.deliver(_event("a", PC.STEP_ENDED), notifier=notifier, thread_store=store, engine=platform.engine)
    assert out["ok"] is True
    assert fake.texts == [f"Iron Jarvis: {PC.ending_words(task, REPORT)}"]
    tid = store.resolve("tg", str(ME), "").id
    assert [m["content"] for m in store.history_body(tid)][-1] == PC.ending_words(task, REPORT)


def test_a_task_you_stopped_yourself_says_nothing_and_a_sender_no_longer_allowed_hears_nothing(platform):
    fake, notifier, store = _phone(platform)
    _put(platform.engine, "s", N.STOPPED, notes=[_note("stopped", N.CANCELLED_RUNNING)])
    out = PC.deliver(_event("s", PC.STEP_ENDED), notifier=notifier, thread_store=store, engine=platform.engine)
    assert out == {"ok": False, "why": "stopped by you"} and fake.texts == []
    _put(platform.engine, "t", N.DONE, morning=DONE_FACTS)
    notifier.get("tg").config["allowed_senders"] = ["12345"]
    out = PC.deliver(_event("t", PC.STEP_ENDED), notifier=notifier, thread_store=store, engine=platform.engine)
    assert out["ok"] is False and fake.texts == []


def test_a_task_from_the_tonight_list_is_never_told_to_a_phone(platform):
    fake, notifier, store = _phone(platform)
    _put(platform.engine, "n", N.DONE, phone=False, morning=DONE_FACTS)
    out = PC.deliver(_event("n", PC.STEP_ENDED), notifier=notifier, thread_store=store, engine=platform.engine)
    assert out == {"ok": False, "why": "not a phone task"} and fake.texts == []


def test_finishing_a_phone_task_rings_its_ending_once_and_writes_no_chat_report(platform):
    seen: list = []
    platform.event_bus.add_handler(lambda ev: seen.append((ev.type, dict(ev.payload or {}))))
    _put(platform.engine, "r", N.RUNNING, morning=DONE_FACTS)
    assert N.finish_task(platform.engine, "r", N.DONE, event_bus=platform.event_bus) is True
    assert N.finish_task(platform.engine, "r", N.DONE, event_bus=platform.event_bus) is False
    mine = [p for t, p in seen if t == EventType.BUILD_PHONE_CODE]
    assert mine == [{"task_id": "r", "step": "ended"}]
    assert not [t for t, _p in seen if t in (EventType.REPORT_ARRIVED, EventType.REPORT_CHAT_GONE)]


# --------------------------------------------------------------------------- #
# never in a night's summary too
# --------------------------------------------------------------------------- #


@pytest.fixture()
def world(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    seen: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: seen.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    return SimpleNamespace(d=app.state.d, platform=platform, engine=platform.engine, seen=seen)


async def test_a_phone_task_is_left_out_of_the_nights_one_summary(world):
    _put(world.engine, "night1", N.DONE, phone=False, morning=DONE_FACTS)
    _put(world.engine, "phone1", N.DONE, morning=DONE_FACTS)
    assert await night_routes.morning_pass(world.d, ["night1", "phone1"]) == ["2026-10-26"]
    [summary] = M.list_summaries(world.engine)
    assert [r["id"] for r in summary["rows"]] == ["night1"]


async def test_a_night_with_only_phone_work_makes_no_summary_and_a_running_one_holds_none_back(world):
    _put(world.engine, "phone1", N.DONE, morning=DONE_FACTS)
    assert await night_routes.morning_pass(world.d, ["phone1"]) == []
    assert M.list_summaries(world.engine) == []
    # A phone task still running never holds the night's own summary back.
    _put(world.engine, "phone2", N.RUNNING)
    _put(world.engine, "night1", N.DONE, phone=False, morning=DONE_FACTS)
    assert await night_routes.morning_pass(world.d, ["night1"]) == ["2026-10-26"]
