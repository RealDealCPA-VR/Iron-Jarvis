"""v1.422.0 (Wave 2 "Never lose work", B2): STOP stops the running command.

Before: the reply's Stop (and Esc, which presses it) ended the answer being
written and prevented the NEXT round, while a command already running ran on
for up to its whole deadline (10 minutes) behind a reply that said it had
stopped — the stop route's own docstring said so. Now every step of a coding
session's turn is raced against the turn's Stop (``harness/stall.run_step``):
the command's process TREE is killed through the tool's own cancel path,
checked here BY PROCESS ID, its ledger row says the user stopped it, its
result never says it succeeded, and the reply ends stopped. A program the
session started IN THE BACKGROUND keeps running.

Driven through the REAL app factory, the real stream lane, the real tool
registry and real commands (``DOCKER_HOST`` pointed nowhere, as on CI, so they
run on this PC); only the model is a scripted double.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import process as PR
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
from sqlmodel import select
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    new_session,
    open_turn,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_build_stall_v1392 import _alive, _hang, _pid

#: How long a stopped command's tree may take to be gone (a hang detector —
#: the kill itself is immediate; this is never a speed bar).
GONE_WITHIN_S = 20


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    harness = folder / ".ironjarvis" / "harness"
    harness.mkdir(parents=True)
    (harness / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


def _rows(app, sid: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all()
                if r.session_id == f"build:{sid}"]
        for r in rows:
            db.expunge(r)
        return rows


def _server(pidfile) -> str:
    return (
        f'"{sys.executable}" -c "import os,time,pathlib; '
        f"pathlib.Path('{pidfile.as_posix()}').write_text(str(os.getpid())); time.sleep(600)\""
    )


async def test_stop_kills_the_running_command_and_leaves_the_background_program(app, work, tmp_path):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    server_pid = tmp_path / "server.pid"
    hang_pid = tmp_path / "hang.pid"
    script = wire(app, Script([
        [ToolCall(id="b1", name="Bash", arguments={"command": _server(server_pid), "run_in_background": True})],
        [ToolCall(id="c1", name="Bash", arguments={"command": _hang(hang_pid)})],
        "Never asked.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "start it, then the slow thing", "turn_id": "bt-stop"},
                                         drop=drop, timeout=120))
    pids: list[int] = []
    try:
        background = await _pid(server_pid)
        hung = await _pid(hang_pid)
        pids = [background, hung]
        assert _alive(hung) and _alive(background)
        status, _ = await asgi(app, "POST", "/chat/turns/bt-stop/stop", {})
        assert status == 200
        # The running command's tree is GONE, by pid — within seconds, not
        # its ten-minute deadline.
        assert await until(lambda: not _alive(hung), GONE_WITHIN_S), "Stop left the running command alive"
        lg = turn_log(app, "bt-stop")
        assert await until(lambda: lg.status != "running", 60)
        # A program the session started in the background keeps running.
        assert _alive(background), "Stop ended a program the session started in the background"
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
        PR.end_session(f"build:{sid}")
    assert await until(lambda: not any(_alive(p) for p in pids), GONE_WITHIN_S), "a test process was left running"
    frames = turn_frames(app, "bt-stop")
    # The reply ended STOPPED: no done, no error frame; the model was never
    # asked again after the stop.
    assert not [e for e, _ in frames if e in ("done", "error")]
    assert len(script.calls) == 2
    # The stopped call's row says the user stopped it; never that it worked.
    rows = _rows(app, sid)
    stopped = [r for r in rows if not r.ok]
    assert len(stopped) == 1 and "you stopped the reply" in (stopped[0].output or "")
    # The step's own frame (what the screen and the reply line keep) says so.
    finished = [d for e, d in frames if e == "tool_call" and d.get("id") == "c1" and d.get("status") == "finished"]
    assert finished and finished[-1].get("ok") is False
    assert "stopped by the user" in str(finished[-1].get("output") or "")
    assert "succeeded" not in str(finished[-1].get("output") or "")


@pytest.mark.skipif(sys.platform != "win32", reason="the gpt dialect's command runs in cmd here")
async def test_stop_kills_a_command_the_call_waits_on_process_start_until_exit(app, work, tmp_path):
    who = ("openai", "gpt-5.1")
    sid = new_session(app, work, provider=who[0], model=who[1], approval_mode="yolo")["id"]
    S.update(app.state.platform.engine, sid, setup={"granted_tools": ["process_start"]})
    hang_pid = tmp_path / "hang.pid"
    wire(app, Script([
        [ToolCall(id="x1", name="exec_command", arguments={"cmd": _hang(hang_pid), "yield_time_ms": 30000})],
        "Never asked.",
    ], who=who))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run the slow thing", "turn_id": "bt-gpt"},
                                         drop=drop, timeout=120))
    hung = 0
    try:
        hung = await _pid(hang_pid)
        status, _ = await asgi(app, "POST", "/chat/turns/bt-gpt/stop", {})
        assert status == 200
        assert await until(lambda: not _alive(hung), GONE_WITHIN_S), "Stop left the waited-on command alive"
        lg = turn_log(app, "bt-gpt")
        assert await until(lambda: lg.status != "running", 60)
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
        PR.end_session(f"build:{sid}")
    assert not hung or not _alive(hung)
    rows = [r for r in _rows(app, sid) if r.tool == "process_start"]
    assert rows and rows[-1].ok is False


async def test_stop_the_step_keeps_its_own_words(app, work, tmp_path):
    """A step stopped with Stop the step (then the reply) keeps Stop the
    step's words — the reply's Stop never overwrites a reason set first."""
    sid = new_session(app, work, approval_mode="yolo")["id"]
    app.state.platform.config.tool_call_timeout_s = 0
    hang_pid = tmp_path / "hang.pid"
    wire(app, Script([[ToolCall(id="c1", name="Bash", arguments={"command": _hang(hang_pid)})], "x"]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "slow", "turn_id": "bt-both"}, drop=drop, timeout=120))
    hung = 0
    try:
        hung = await _pid(hang_pid)
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step",
                                 {"step_id": "c1", "reply": True})
        assert status == 200, out
        assert await until(lambda: not _alive(hung), GONE_WITHIN_S)
        lg = turn_log(app, "bt-both")
        assert await until(lambda: lg.status != "running", 60)
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
    rows = [r for r in _rows(app, sid) if not r.ok]
    assert rows and stall.STOPPED_LEDGER_NOTE in (rows[-1].output or "")


async def test_a_step_raced_against_a_turn_with_no_handle_runs_as_before():
    """``run_step`` for a turn that is not registered (no Stop handle): the
    step simply runs (no race) and answers its own result."""
    async def make():
        await asyncio.sleep(0)
        return "done"

    assert await stall.run_step("bt-unknown", "c9", "shell", make, lambda: "stopped") == "done"


async def test_run_step_answers_turn_stopped_when_the_turns_stop_wins():
    from iron_jarvis.core.turns import TURNS

    handle = TURNS.register("bt-unit")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def make():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "never"

    try:
        run = asyncio.create_task(stall.run_step(
            "bt-unit", "c1", "shell", make, lambda: "step-words", turn_stopped=lambda: "turn-words",
        ))
        await asyncio.wait_for(started.wait(), 10)
        assert stall.running_steps("bt-unit") == ["c1"]
        TURNS.stop("bt-unit")
        assert await asyncio.wait_for(run, 10) == "turn-words"
        assert cancelled.is_set()
        assert stall.running_steps("bt-unit") == []
    finally:
        TURNS.release("bt-unit", handle)


async def test_a_step_that_watches_the_stop_itself_answers_in_its_own_words():
    """The agent's browser races the turn's Stop on its own (v1.376.0) and
    says what happened: it gets ``STOP_GRACE_S`` to do so before it is
    cancelled."""
    from iron_jarvis.core.turns import TURNS

    handle = TURNS.register("bt-own")

    async def make():
        await handle.wait_stopped()
        await asyncio.sleep(0.05)  # it tidies up (closes its browser) first
        return "own-words"

    try:
        run = asyncio.create_task(stall.run_step(
            "bt-own", "c1", "page_click", make, lambda: "step-words", turn_stopped=lambda: "turn-words",
        ))
        await asyncio.sleep(0)
        TURNS.stop("bt-own")
        assert await asyncio.wait_for(run, 10) == "own-words"
    finally:
        TURNS.release("bt-own", handle)


async def test_a_step_that_waits_on_a_person_is_not_raced(monkeypatch):
    """``race=False`` (the lane passes it for a ``deadline_exempt`` tool:
    ``ask_user`` ends on Stop in its own words, however long that takes —
    longer than the grace here)."""
    from iron_jarvis.core.turns import TURNS

    monkeypatch.setattr(stall, "STOP_GRACE_S", 0.02)
    handle = TURNS.register("bt-person")
    gate = asyncio.Event()

    async def make():
        await gate.wait()
        return "the person's answer"

    try:
        run = asyncio.create_task(stall.run_step(
            "bt-person", "c1", "ask_user", make, lambda: "step-words",
            turn_stopped=lambda: "turn-words", race=False,
        ))
        await asyncio.sleep(0)
        TURNS.stop("bt-person")
        await asyncio.sleep(0.2)
        assert not run.done(), "a person's question was cancelled by the race"
        gate.set()
        assert await asyncio.wait_for(run, 10) == "the person's answer"
    finally:
        TURNS.release("bt-person", handle)


async def test_stop_the_step_then_the_reply_keeps_the_steps_own_words(monkeypatch):
    """A step the user stopped with Stop the step that is still unwinding when
    the reply's Stop lands (past the grace) keeps Stop the step's words — the
    reply's Stop never overwrites a reason set first."""
    from iron_jarvis.core.turns import TURNS

    monkeypatch.setattr(stall, "STOP_GRACE_S", 0.02)
    handle = TURNS.register("bt-twice")
    started = asyncio.Event()

    async def make():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)  # its unwinding takes a moment
            raise
        return "never"

    try:
        run = asyncio.create_task(stall.run_step(
            "bt-twice", "c1", "shell", make, lambda: "step-words", turn_stopped=lambda: "turn-words",
        ))
        await asyncio.wait_for(started.wait(), 10)
        assert stall.stop_step("bt-twice", "c1")
        TURNS.stop("bt-twice")
        assert await asyncio.wait_for(run, 10) == "step-words"
    finally:
        TURNS.release("bt-twice", handle)


async def test_a_question_waiting_on_the_user_ends_in_its_own_words_however_slow(app, work, monkeypatch):
    """``ask_user`` is ``deadline_exempt`` (it waits on a PERSON): the reply's
    Stop never cancels it, even when it takes longer than the grace to see the
    Stop — its row says the turn was stopped before the user answered."""
    from iron_jarvis.daemon import mcp_turn
    from iron_jarvis.harness import session_tools as T
    from tests.test_build_plan_ask_v1367 import _QUESTION, _finish, _start
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    monkeypatch.setattr(stall, "STOP_GRACE_S", 0.0)
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="q1", name="AskUserQuestion", arguments=_QUESTION)], "never"]))
    drop, task = await _start(app, sid, "go", "ba-own")
    ask = (await until(lambda: [d for e, d in turn_frames(app, "ba-own") if e == "ask_user"]))[0]
    assert mcp_turn.find(ask["id"]) is not None
    assert (await _asgi_post(app, "/chat/turns/ba-own/stop"))[0] == 200
    lg = await _finish(app, "ba-own", drop, task)
    assert lg.status == "stopped"
    rows = [r for r in _rows(app, sid) if r.tool == "ask_user"]
    assert rows and T.STOPPED_BEFORE_ANSWER in (rows[0].output or "")
