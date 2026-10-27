"""v1.399.0 (night shift N2, "the morning") — the rules, one at a time.

* THE NIGHT: a task belongs to the night its window opened on (01:30 on the
  27th is the night of the 26th); a task an older Iron Jarvis started is
  placed by its ``started_at``.
* THE COMMIT MESSAGE is the task's own words, and says how the task ended.
* THE FACTS are read off the records: usage per account from each reply
  line's receipt (a subscription's money is a list-price equivalent), the
  rows failed and needs-you first, what needs the user said open.
* THE SUMMARY is ONE per night, made when the night's last task ends or its
  window closes — whichever comes first — announced ONCE (an old night an
  upgrade finds is listed quietly), its rows brought up to date by a task
  that ends later (no second notice), the newest 14 kept.
* THE NOTICE: the bell, the tray and the phone read the daemon's words;
  "This PC" stays quiet; the phone gets ONE message with a line per task.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import Notifier, bell_attention, bell_pings, format_event
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_morning as M

pytestmark = pytest.mark.usefixtures("no_network")


def _local(h: int, m: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 10, day, h, m).astimezone()


# --------------------------------------------------------------------------- #
# the night
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "at, start, want",
    [
        ((26, 23, 30), "23:00", "2026-10-26"),
        ((27, 1, 30), "23:00", "2026-10-26"),
        ((27, 6, 59), "23:00", "2026-10-26"),
        ((26, 23, 0), "23:00", "2026-10-26"),
        ((26, 22, 59), "23:00", "2026-10-25"),
        ((27, 2, 0), "01:00", "2026-10-27"),  # a window that does not cross midnight
        ((27, 0, 30), "01:00", "2026-10-26"),
    ],
)
def test_a_moment_belongs_to_the_night_its_window_opened_on(at, start, want):
    day, h, m = at
    assert M.night_key(datetime(2026, 10, day, h, m), start, "07:00") == want


def test_a_task_keeps_its_night_else_it_is_placed_by_when_it_started():
    assert M.task_night({"night": "2026-10-20", "started_at": "2026-10-27T05:00:00Z"}, "23:00") == "2026-10-20"
    began = _local(1, 30, day=27).astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    assert M.task_night({"night": "", "started_at": began}, "23:00") == "2026-10-26"
    assert M.task_night({"night": "", "started_at": None}, "23:00") == ""


def test_the_label_is_the_day_in_words():
    assert M.night_label("2026-10-26") == "Monday, October 26"
    assert M.night_label("nonsense") == "nonsense"


# --------------------------------------------------------------------------- #
# the commit message (decision 2: the task's words)
# --------------------------------------------------------------------------- #


def test_the_commit_message_is_the_tasks_own_words_and_says_how_it_ended():
    msg = M.commit_message("Fix the parser\n\nThe test in test_parse.py says how.", "needs_you")
    subject, *rest = msg.split("\n")
    assert subject == "Fix the parser"
    assert "The test in test_parse.py says how." in msg
    assert msg.endswith("Committed by Iron Jarvis when this night task ended (needs you).")
    one = M.commit_message("Make add() add", "done")
    assert one == "Make add() add\n\nCommitted by Iron Jarvis when this night task ended (done)."
    long = M.commit_message("x" * 100, "failed")
    assert len(long.split("\n")[0]) == M.SUBJECT_CHARS and long.split("\n")[0].endswith("…")
    assert ("x" * 100) in long  # the words are kept whole in the body
    huge = M.commit_message("y\n" + "z" * 50_000, "done")
    from iron_jarvis.harness.branches import MAX_MESSAGE_CHARS

    assert len(huge) <= MAX_MESSAGE_CHARS and huge.endswith("(done).")


# --------------------------------------------------------------------------- #
# usage per account (each reply line's receipt)
# --------------------------------------------------------------------------- #


def test_usage_is_read_per_account_off_each_reply_line_and_its_helpers():
    lines = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "route": {"provider": "claude-cli", "model": "opus", "account": {"id": "a1", "name": "Work Max"}},
         "usage": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5, "list_price_equivalent": True},
         "helpers": [{"usage": {"input_tokens": 50, "output_tokens": 5, "cost_usd": 0.25, "list_price_equivalent": True}}]},
        {"role": "assistant", "route": {"provider": "claude-cli", "model": "opus", "account": {"id": "a1", "name": "Work Max"}},
         "usage": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.01, "list_price_equivalent": True}},
        {"role": "assistant", "route": {"provider": "ollama", "model": "qwen3"}, "usage": {"input_tokens": 7, "output_tokens": 3}},
        {"role": "assistant", "route": {"provider": "openai", "model": "gpt-6"}, "usage": {"input_tokens": 2, "output_tokens": 2, "cost_usd": 0.02}},
        {"role": "assistant", "error": "boom"},  # a failed reply: no receipt
    ]
    kinds = {"ollama": "free", "openai": "pay", "claude-cli": "pay"}
    got = M.usage_by_account(lines, lambda p, m: kinds.get(p, ""))
    by = {(u["account"], u["provider"]): u for u in got}
    work = by[("Work Max", "claude-cli")]
    assert (work["input_tokens"], work["output_tokens"], work["cost_usd"], work["kind"]) == (151, 16, 0.76, "plan")
    local = by[("", "ollama")]
    assert local["kind"] == "free" and "cost_usd" not in local  # never a made-up 0
    assert by[("", "openai")]["kind"] == "pay" and by[("", "openai")]["cost_usd"] == 0.02
    assert len(got) == 3
    assert M.usage_words(work) == "167 tokens · ≈$0.76 at list price (your plan)"
    assert M.usage_words(local) == "10 tokens · free"
    assert M.usage_words(by[("", "openai")]) == "4 tokens · $0.02"


# --------------------------------------------------------------------------- #
# rows, order, words
# --------------------------------------------------------------------------- #


def _task(tid: str, state: str, **over) -> dict:
    base = {
        "id": tid, "state": state, "words": f"Task {tid}\nmore words", "folder": "C:\\work\\shop",
        "branch": f"ij/task-{tid}", "thread_id": f"bs_{tid}", "ran_s": 61.0, "notes": [], "try_on": [],
        "main_changed": False, "night": "2026-10-26", "started_at": f"2026-10-27T03:3{len(tid)}:00Z",
        "ended_at": None, "morning": {},
    }
    base.update(over)
    return base


def test_rows_put_failed_and_needs_you_first_and_say_what_needs_you_open():
    tasks = [
        _task("d", "done", morning={"tests": "passed", "files": 2, "commit": {"sha": "abc123def456", "subject": "Task d"}}),
        _task("s", "stopped"),
        _task("n", "needs_you", main_changed=True, notes=[
            {"kind": "outside", "text": "Your main folder changed while this task ran: a file changed (a.py)."},
            {"kind": "declined", "text": "Needs your OK: run a command (nobody answered in 15 minutes, so it was skipped)."},
            {"kind": "info", "text": "Not confined."},
        ], morning={"tests": "passed", "tests_trusted": False, "commit": {"sha": "fff111", "subject": "Task n"}}),
        _task("f", "failed", notes=[{"kind": "error", "text": "The model failed."}]),
        _task("r", "running"),
        _task("q", "queued"),
    ]
    rows = M.rows_for_night(tasks, "2026-10-26", "23:00")
    assert [r["state"] for r in rows] == ["failed", "needs_you", "running", "done", "stopped"]
    n = next(r for r in rows if r["id"] == "n")
    assert n["main_changed"] and n["main_changed_words"].startswith("Your main folder changed")
    assert n["needs"] == [
        "Needs your OK: run a command (nobody answered in 15 minutes, so it was skipped).",
        "Its tests ran, but the result is not trusted: open the session to see why.",
    ]
    assert n["committed_words"] == "Its work so far is committed on ij/task-n, so nothing is lost."
    d = next(r for r in rows if r["id"] == "d")
    assert d["committed_words"] == "" and d["commit"] == {"sha": "abc123def456", "subject": "Task d"}
    assert d["title"] == "Task d" and d["seconds"] == 61.0 and d["files"] == 2
    f = next(r for r in rows if r["id"] == "f")
    assert f["failure"] == "The model failed."
    words = M.notice_words(rows)
    assert words["title"] == "Last night: 1 failed, 1 needs you, 1 still running, 1 done, 1 stopped"
    assert words["body"].startswith("Failed: Task f · Needs you: Task n")
    assert len(words["body"]) <= M.BODY_CHARS


def test_the_phone_gets_one_message_with_a_line_per_task():
    rows = M.rows_for_night([_task(str(i), "done") for i in range(12)], "2026-10-26", "23:00")
    payload = M.notice_payload("2026-10-26", rows)
    text = M.phone_message(payload)
    lines = text.splitlines()
    assert lines[0] == "Last night: 12 done"
    assert len([ln for ln in lines if ln.startswith("• ")]) == M.PHONE_LINES
    assert "…and 2 more." in lines
    assert lines[-1].startswith("Open Coding sessions › Last night in Iron Jarvis")
    assert all(len(ln) <= M.PHONE_LINE_CHARS for ln in lines[1:-1])
    # The daemon's alert IS this message (comm/notifier.format_event).
    assert format_event({"type": EventType.BUILD_NIGHT_SUMMARY, "payload": payload}) == text
    # Titles and branch names only: never a folder path.
    assert "C:\\work" not in json.dumps(payload)


def test_this_pc_is_quiet_and_the_phone_hears_the_night_once(monkeypatch):
    toasts: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: toasts.append(message))
    n = Notifier()
    phone = MockChannel()
    n.add_channel("phone", phone)
    n.add_channel("this-pc", DesktopChannel({"type": "desktop"}))
    payload = M.notice_payload("2026-10-26", M.rows_for_night([_task("a", "done")], "2026-10-26", "23:00"))
    ev = {"id": "e1", "type": EventType.BUILD_NIGHT_SUMMARY, "session_id": None, "payload": payload}
    assert bell_pings(ev) is True and bell_attention(ev) is True
    n.on_event(ev)
    assert len(phone.sent) == 1 and phone.sent[0].startswith("Last night: 1 done")
    assert toasts == []
    assert bell_pings({"type": EventType.BUILD_NIGHT_SUMMARY, "payload": {}}) is False


# --------------------------------------------------------------------------- #
# the summary: ready when the last task ends or the window closes
# --------------------------------------------------------------------------- #


@pytest.fixture()
def world(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    seen: list[dict] = []
    platform.event_bus.add_handler(
        lambda ev: seen.append(dict(ev.payload or {})) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None
    )
    clock = {"now": _local(23, 30)}
    monkeypatch.setattr(N, "CLOCK", lambda: clock["now"])
    return SimpleNamespace(app=app, d=app.state.d, platform=platform, engine=platform.engine, seen=seen, clock=clock)


def _put(engine, tid: str, state: str, night: str = "2026-10-26", **over) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = N.NightTaskRecord(id=tid, folder="C:\\work\\shop", words=f"Task {tid}", state=state, night=night,
                            branch=f"ij/task-{tid}", started_at=None if state == N.QUEUED else now,
                            ended_at=now if state in N.ENDED else None, **over)
    with session_scope(engine) as db:
        db.add(row)
        db.commit()


def _set_state(engine, tid: str, state: str) -> None:
    with session_scope(engine) as db:
        row = db.get(N.NightTaskRecord, tid)
        row.state = state
        row.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(row)
        db.commit()


async def _flush(world, at_least: int = 1) -> None:
    """Wait for the bus's handlers (they run off the publish) to have seen
    ``at_least`` notices — a bound, never a speed bar — then a moment more so
    a SECOND notice, were there one, would be seen too."""
    import asyncio

    end = asyncio.get_running_loop().time() + 20
    while len(world.seen) < at_least and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)


async def test_not_ready_while_the_nights_work_goes_on_inside_the_window(world):
    _put(world.engine, "a", N.DONE)
    _put(world.engine, "b", N.RUNNING)
    assert await night_routes.morning_pass(world.d, []) == []
    # A queued task still starts in this window.
    _set_state(world.engine, "b", N.DONE)
    _put(world.engine, "c", N.QUEUED, night="")
    assert await night_routes.morning_pass(world.d, ["b"]) == []
    assert M.list_summaries(world.engine) == []


async def test_ready_when_the_last_task_ends_announced_once(world):
    _put(world.engine, "a", N.DONE)
    _put(world.engine, "b", N.FAILED)
    assert await night_routes.morning_pass(world.d, ["b"]) == ["2026-10-26"]
    for _ in range(3):
        assert await night_routes.morning_pass(world.d, []) == []
    await _flush(world)
    assert len(world.seen) == 1
    assert world.seen[0]["title"] == "Last night: 1 failed, 1 done"
    [summary] = M.list_summaries(world.engine)
    assert [r["id"] for r in summary["rows"]] == ["b", "a"] and summary["notified"] is True


async def test_emergency_stop_holding_the_queue_makes_the_night_end(world):
    _put(world.engine, "a", N.STOPPED)
    _put(world.engine, "q", N.QUEUED, night="")
    world.platform.config.autonomy_kill_switch = True
    assert await night_routes.morning_pass(world.d, ["a"]) == ["2026-10-26"]


async def test_ready_when_the_window_closes_even_with_a_task_still_running(world):
    _put(world.engine, "a", N.DONE)
    _put(world.engine, "b", N.RUNNING)
    world.clock["now"] = _local(7, 1, day=27)  # the window closed
    assert await night_routes.morning_pass(world.d, []) == ["2026-10-26"]
    [summary] = M.list_summaries(world.engine)
    assert [r["state"] for r in summary["rows"]] == ["running", "done"]
    # It ends later: its row is brought up to date, and nothing is said again.
    _set_state(world.engine, "b", N.NEEDS_YOU)
    assert await night_routes.morning_pass(world.d, ["b"]) == []
    [summary] = M.list_summaries(world.engine)
    assert [r["state"] for r in summary["rows"]] == ["needs_you", "done"]
    await _flush(world)
    assert len(world.seen) == 1
    assert world.seen[0]["counts"]["running"] == 1  # said as it was when it was made


async def test_an_old_night_an_upgrade_finds_is_listed_quietly(world):
    world.clock["now"] = _local(12, 0, day=28)
    _put(world.engine, "old", N.DONE, night="2026-10-26")
    _put(world.engine, "new", N.DONE, night="2026-10-27")
    made = await night_routes.morning_pass(world.d, [])
    assert sorted(made) == ["2026-10-26", "2026-10-27"]
    await _flush(world)
    assert [p["night"] for p in world.seen] == ["2026-10-27"]  # last night only
    assert [s["night"] for s in M.list_summaries(world.engine)] == ["2026-10-27", "2026-10-26"]


async def test_the_newest_fourteen_nights_are_kept(world):
    for i in range(16):
        key = (datetime(2026, 10, 1) + timedelta(days=i)).date().isoformat()
        assert M.make_summary(world.engine, key, [])
    assert M.prune(world.engine) == 2
    nights = [s["night"] for s in M.list_summaries(world.engine, limit=100)]
    assert len(nights) == M.KEEP_NIGHTS and nights[0] == "2026-10-16" and nights[-1] == "2026-10-03"
    assert not M.make_summary(world.engine, "2026-10-16", [])  # ONE per night


async def test_a_task_record_pruned_from_the_list_keeps_its_row(world):
    _put(world.engine, "a", N.DONE)
    _put(world.engine, "b", N.DONE)
    await night_routes.morning_pass(world.d, ["b"])
    with session_scope(world.engine) as db:
        db.delete(db.get(N.NightTaskRecord, "a"))
        db.commit()
    rows = M.rows_for_night(N.list_tasks(world.engine), "2026-10-26", "23:00")
    M.refresh_summary(world.engine, "2026-10-26", rows)
    [summary] = M.list_summaries(world.engine)
    assert sorted(r["id"] for r in summary["rows"]) == ["a", "b"]
