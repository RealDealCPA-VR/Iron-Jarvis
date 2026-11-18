"""Fourth plan group 27, CS6/DT4 — the STEP half: a step Iron Jarvis stopped
is never recorded as the user's.

Before: ``stall.stop_step`` always marked its step "stopped by you", and a
step the reply's Stop reached always read "you stopped the reply" — whoever
stopped it. The night's time limit (the backstop) and Emergency stop both
stop steps, so the step's ledger row (the record outcomes are read from) and
the model's result said the user stopped something the user never touched;
the finish check's test run cut by such a stop read "stopped by you" too, and
the fix-request report would have read the honest new words as a defect.

Now every stop names who made it (``core.turns.STOP_AUTHORS``): the night's
backstop "time_limit", Emergency stop "emergency", the night's Cancel "you".
The user's own stops keep today's words byte for byte.

Driven through the REAL night loop, the real stream lane, the real tool
registry (its ledger row) and real commands; only the model is a scripted
double.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.agents import fix_request as FR
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import durable_turns as DT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import night as N
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import make_repo
from tests.test_build_sessions_v1367 import wire
from tests.test_night_shift_review_v1398 import (  # noqa: F401 — fixtures
    NightScript,
    _ended,
    _git_of,
    _no_gh,
    _queue,
    _task,
    night,
)

pytestmark = pytest.mark.usefixtures("no_network")

#: Today's words for the user's own stops — pinned as LITERALS, so a change
#: to the constants themselves shows here too.
YOU_STEP_NOTE = (
    "stopped by you while it was running (it had shown no progress) — "
    "anything it did before it was stopped may have landed"
)
YOU_TURN_NOTE = (
    "stopped by you while it was running (you stopped the reply) — "
    "anything it did before it was stopped may have landed"
)
YOU_STEP_RESULT = (
    "The shell step was stopped by the user: it had shown no progress for 10 minutes. It did not finish, "
    "and anything it did before it was stopped may have taken effect. Carry on without it; do "
    "not start it again unless the user asks."
)
YOU_TURN_RESULT = (
    "The shell step was stopped by the user, who stopped the reply while it was running. It did not "
    "finish, and anything it did before it was stopped may have taken effect. Do not start it "
    "again unless the user asks."
)
TIME_NOTE = (
    "stopped by Iron Jarvis when the reply reached its time limit — "
    "anything it did before it was stopped may have landed"
)
EMERGENCY_NOTE = "stopped by Emergency stop — anything it did before it was stopped may have landed"
UNKNOWN_NOTE = "stopped while it was running — anything it did before it was stopped may have landed"


# --------------------------------------------------------------------------- #
# the step's words, through the real run_step and the real turn handle
# --------------------------------------------------------------------------- #


async def _stop_one(turn: str, how, monkeypatch) -> tuple[list[str], object]:
    """Run one step of ``turn`` through ``stall.run_step`` (the lane's own
    callbacks), stop it with ``how()``; answer (the ledger note the registry
    would write — read inside the cancelled step, where the registry reads
    it —, the call's result)."""
    monkeypatch.setattr(stall, "STOP_GRACE_S", 0.02)
    handle = TURNS.register(turn)
    started = asyncio.Event()
    notes: list[str] = []

    async def make():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            notes.append(stall.stop_note())
            raise
        return "never"

    try:
        run = asyncio.create_task(stall.run_step(
            turn, "c1", "shell", make,
            lambda: stall.stopped_result("shell"),
            turn_stopped=lambda: stall.turn_stopped_result("shell"),
            not_started=lambda: stall.not_started_result("shell"),
        ))
        await asyncio.wait_for(started.wait(), 10)
        how()
        result = await asyncio.wait_for(run, 10)
    finally:
        TURNS.release(turn, handle)
    return notes, result


async def test_a_users_stop_the_step_keeps_todays_words_byte_for_byte(monkeypatch):
    notes, result = await _stop_one("bt-g27b-you", lambda: stall.stop_step("bt-g27b-you", "c1", by="you"),
                                    monkeypatch)
    assert notes == [YOU_STEP_NOTE] and stall.STOPPED_LEDGER_NOTE == YOU_STEP_NOTE
    assert result == YOU_STEP_RESULT


async def test_a_users_stop_of_the_reply_keeps_todays_words_byte_for_byte(monkeypatch):
    notes, result = await _stop_one("bt-g27b-you2", lambda: TURNS.stop("bt-g27b-you2", by="you"), monkeypatch)
    assert notes == [YOU_TURN_NOTE] and stall.TURN_STOPPED_LEDGER_NOTE == YOU_TURN_NOTE
    assert result == YOU_TURN_RESULT


async def test_the_time_limit_stopping_a_step_names_iron_jarvis_not_you(monkeypatch):
    notes, result = await _stop_one(
        "bt-g27b-time", lambda: stall.stop_step("bt-g27b-time", "c1", by="time_limit"), monkeypatch)
    assert notes == [TIME_NOTE]
    assert result == (
        "The shell step was stopped by Iron Jarvis when your reply reached its time limit. It did not "
        "finish, and anything it did before it was stopped may have taken effect. Do not start it again "
        "unless the user asks."
    )


async def test_emergency_stop_reaching_a_running_step_names_emergency_stop(monkeypatch):
    notes, result = await _stop_one("bt-g27b-em", lambda: TURNS.stop("bt-g27b-em", by="emergency"), monkeypatch)
    assert notes == [EMERGENCY_NOTE]
    assert str(result).startswith("The shell step was stopped by Emergency stop. It did not finish")
    assert "user" not in str(result).split(".")[0]


async def test_a_stop_nobody_named_is_never_worded_as_yours(monkeypatch):
    """No stop function defaults to "you" (the check E6)."""
    notes, result = await _stop_one("bt-g27b-un", lambda: stall.stop_step("bt-g27b-un", "c1"), monkeypatch)
    assert notes == [UNKNOWN_NOTE]
    assert "by the user" not in str(result) and "stopped while it was running" in str(result)


async def test_a_step_of_a_reply_the_time_limit_already_stopped_is_never_started_and_says_why():
    handle = TURNS.register("bt-g27b-early")
    try:
        TURNS.stop("bt-g27b-early", by="time_limit")

        async def make():
            raise AssertionError("a step of a stopped reply was started")

        got = await stall.run_step("bt-g27b-early", "c1", "shell", make, lambda: "x",
                                   not_started=lambda: stall.not_started_result("shell"))
    finally:
        TURNS.release("bt-g27b-early", handle)
    assert got == (
        "The shell step was not run: Iron Jarvis stopped the reply at its time limit before it started. "
        "Nothing it would have done took effect. Do not start it again unless the user asks."
    )


# --------------------------------------------------------------------------- #
# the night's own stops, end to end: the turn, the step's ledger row, the
# model's result
# --------------------------------------------------------------------------- #


_HUNG = ('python -c "import os, pathlib, time; pathlib.Path(\'pid.txt\').write_text(str(os.getpid())); '
         'time.sleep(120)"')


async def _night_task_running_a_hung_command(tmp_path, key: str):
    from tests.test_build_attempts_v1397 import _alive
    from tests.test_build_sessions_v1367 import until

    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({key: [
        [ToolCall(id="h1", name="Bash", arguments={"command": _HUNG, "timeout": 600000})],
        "Never said.",
    ]}))
    task = await _queue(app, repo, f"{key}: run the long thing")
    await night_routes.night_pass(app.state.d)
    started = _task(app, task["id"])
    worktree = Path(_git_of(app, started)["worktree"])

    def _pid() -> int:
        try:
            return int((worktree / "pid.txt").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0

    pid = await until(_pid, 60)
    assert pid and _alive(pid)
    return app, task, started, pid


def _step_rows(app, thread_id: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all()
                if r.session_id == f"build:{thread_id}" and not r.ok]
        for r in rows:
            db.expunge(r)
        return rows


def _step_output(app, turn_id: str) -> str:
    import json

    lg = app.state.platform.turn_logs.get(turn_id)
    frames = [(e, json.loads(d or "{}")) for e, d in (lg.frames if lg is not None else [])]
    done = [d for e, d in frames if e == "tool_call" and d.get("id") == "h1" and d.get("status") == "finished"]
    return str(done[-1].get("output") or "") if done else ""


async def _wound_down(app, turn_id: str, pid: int) -> None:
    from tests.test_build_attempts_v1397 import _alive
    from tests.test_build_sessions_v1367 import until

    lg = app.state.platform.turn_logs.get(turn_id)
    assert await until(lambda: lg.status != "running", 45), "the turn waited for its hung command"
    assert await until(lambda: not _alive(pid), 30), "the hung command outlived the stop"


@pytest.mark.usefixtures("night")
async def test_the_night_backstop_records_its_time_limit_on_the_turn_and_the_steps_row(tmp_path):
    app, task, started, pid = await _night_task_running_a_hung_command(tmp_path, "TASK-LIMIT")
    turn = started["turn_id"]
    lg = app.state.platform.turn_logs.get(turn)
    lg.started_at = lg.started_at - timedelta(minutes=90) - timedelta(seconds=night_routes.OVERRUN_GRACE_S + 60)
    await night_routes.settle_running(app.state.d)
    await _wound_down(app, turn, pid)
    # The turn: Iron Jarvis's time limit stopped it, never "you".
    assert lg.stop_author == "time_limit"
    fact = await asyncio.to_thread(DT.stop_fact, app.state.platform.engine, turn)
    assert fact is not None and fact[1] == "time_limit", fact
    # The step's ledger row, the record outcomes are read from.
    rows = _step_rows(app, started["thread_id"])
    assert rows and TIME_NOTE in (rows[-1].output or ""), [r.output for r in rows]
    assert "stopped by you" not in (rows[-1].output or "")
    # What the model's result says about the step.
    told = _step_output(app, turn)
    assert "stopped by Iron Jarvis when your reply reached its time limit" in told, told
    assert "by the user" not in told


@pytest.mark.usefixtures("night")
async def test_emergency_stop_records_emergency_on_the_turn_and_the_steps_row(tmp_path):
    app, task, started, pid = await _night_task_running_a_hung_command(tmp_path, "TASK-EMERG")
    turn = started["turn_id"]
    app.state.platform.config.autonomy_kill_switch = True
    stopped = await asyncio.to_thread(app.state.platform.orchestrator.stop_unattended)
    assert f"turn:{turn}" in stopped
    await _wound_down(app, turn, pid)
    assert app.state.platform.turn_logs.get(turn).stop_author == "emergency"
    rows = _step_rows(app, started["thread_id"])
    assert rows and EMERGENCY_NOTE in (rows[-1].output or ""), [r.output for r in rows]
    assert "stopped by Emergency stop" in _step_output(app, turn)
    ended = await _ended(app, task["id"], 60)
    assert ended["state"] == N.STOPPED


@pytest.mark.usefixtures("night")
async def test_the_users_cancel_on_the_tonight_list_keeps_todays_words(tmp_path):
    from tests.test_build_sessions_v1367 import asgi

    app, task, started, pid = await _night_task_running_a_hung_command(tmp_path, "TASK-CANCEL")
    turn = started["turn_id"]
    status, out = await asgi(app, "POST", f"/build/night/{task['id']}/cancel", {})
    assert status == 200 and out["stopped_turn"] == turn, out
    await _wound_down(app, turn, pid)
    assert app.state.platform.turn_logs.get(turn).stop_author == "you"
    rows = _step_rows(app, started["thread_id"])
    assert rows and (rows[-1].output or "").endswith((YOU_STEP_NOTE, YOU_TURN_NOTE)), [r.output for r in rows]


# --------------------------------------------------------------------------- #
# the readers: fix requests, the finish check, the night's outcome
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("row", [TIME_NOTE, EMERGENCY_NOTE, UNKNOWN_NOTE])
def test_fix_request_never_reads_a_step_stopped_by_a_limit_or_emergency_stop_as_a_defect(row):
    """The check R1: the new ledger words are a stop, never a defect."""
    for tool in ("read_file", "web_fetch"):
        assert FR.classify_call(tool, row, "") is None, (tool, row)


def test_every_ledger_word_of_a_stop_is_a_fix_request_stop_marker():
    """The two tables stay in step: a word added to the step's ledger words
    without its marker would be read as a defect."""
    for words in stall.STOPPED_BY_WORDS.values():
        assert any(m in words.lower() for m in FR._STOP_MARKERS), words


def test_a_real_defect_is_still_read_as_one():
    """The new markers do not swallow a real failure."""
    assert FR.classify_call("read_file", "Error: the file could not be decoded as text", "") is not None


@pytest.mark.parametrize("by,why", [
    ("you", "stopped by you"),
    ("time_limit", "stopped by Iron Jarvis when the reply reached its time limit"),
    ("emergency", "stopped by Emergency stop"),
    ("", "stopped while it was running"),
])
async def test_a_finish_check_test_run_cut_by_the_turns_stop_names_who_stopped_it(by, why):
    """The check R2: ``finish`` wrote "stopped by you" whoever stopped the
    turn."""
    from tests.test_second_review_finish_stop_v1512 import TURN, SlowRun, _check, _started

    handle = TURNS.register(TURN)
    fc = _check()
    run = SlowRun()
    try:
        taking = asyncio.ensure_future(fc.take(SimpleNamespace(id="ij-finish-1", name="process_start"),
                                               _started(), "first output", refused=False, invoke=run))
        await asyncio.wait_for(run.reading.wait(), 5)
        TURNS.stop(TURN, by=by)
        await asyncio.wait_for(asyncio.shield(taking), 5)
    finally:
        run.release.set()
        TURNS.release(TURN, handle)
    rec = fc._by_id["ij-finish-1"]
    assert rec.verdict == F.TESTS_NOT_RUN and rec.why == why


@pytest.mark.parametrize("by,kind", [("time_limit", "limit"), ("emergency", "emergency"), ("", "stopped")])
def test_the_nights_outcome_reads_a_reply_iron_jarvis_stopped_as_stopped_not_failed(by, kind):
    line = {"role": "assistant", "content": "part", "interrupted": True, "stoppedBy": by,
            "error": "This reply was stopped. Iron Jarvis did not carry it on by itself."}
    state, notes = N.outcome_of({"id": "t1", "notes": []}, line)
    assert state == N.STOPPED, (state, notes)
    assert notes == [(kind, line["error"])]
    # The backstop's own note already says it: no second one.
    if kind == "limit":
        task = {"id": "t1", "notes": [{"kind": "limit", "text": "Stopped: it ran past its 60-minute limit."}]}
        assert N.outcome_of(task, line) == (N.STOPPED, [])


def test_the_nights_outcome_of_a_users_stopped_line_is_read_as_before():
    line = {"role": "assistant", "content": "part", "interrupted": True, "stoppedBy": "you",
            "error": "You stopped this reply. Iron Jarvis did not carry it on by itself."}
    before = dict(line)
    before.pop("stoppedBy")
    assert N.outcome_of({"id": "t1", "notes": []}, line) == N.outcome_of({"id": "t1", "notes": []}, before)


# --------------------------------------------------------------------------- #
# the scan: the night's stops name themselves
# --------------------------------------------------------------------------- #


def test_the_nights_stop_calls_all_name_who_stops():
    from tests import test_fourth_review_stop_v1535 as SCAN

    unnamed = SCAN._unnamed_stop_calls()
    for rel in ("daemon/routes/night.py", "harness/night.py"):
        assert rel not in SCAN.UNNAMED_ALLOWED, rel
        assert not unnamed.get(rel), (rel, unnamed.get(rel))
    SCAN.test_every_stop_call_names_who_stops()
