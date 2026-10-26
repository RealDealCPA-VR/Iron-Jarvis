"""v1.392.0 (the long-jobs plan's Phase 3): a coding reply NEVER STALLS SILENTLY.

A Build reply with no progress — no frame, no tool output — for ten minutes
says so on its live line (a ``stall`` frame) and in the bell (ONE
``build.turn_stalled`` per episode), never while it waits on the user. It
offers **Stop the step** (``POST /build/sessions/{id}/stop-step``): EXACTLY
that tool call is cancelled — its command's process is gone, checked by
process id — its ledger row says "stopped by you", the model is told in plain
words, and the reply carries on. A long command that keeps printing is not a
stall: the command runners mark progress as output arrives
(``core/progress``).

Every interval is read through ``harness.stall.CLOCK``, which these tests
replace with a clock they move themselves; the only real waits are for the
daemon to get somewhere, bounded as hang detectors.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading

import psutil
import pytest
from sqlmodel import select

from iron_jarvis.core import procs, progress
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import process as P
from iron_jarvis.harness import stall
from iron_jarvis.providers.adapters.base import ToolCall
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


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


@pytest.fixture()
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(stall, "CLOCK", c)
    monkeypatch.setattr(stall, "TICK_S", 0.02)
    return c


@pytest.fixture()
def checks(monkeypatch):
    """How many times the watch has looked (a negative check waits for looks,
    never for a length of time)."""
    seen = {"n": 0}
    real = stall.StallWatch.check

    def counted(self):
        seen["n"] += 1
        return real(self)

    monkeypatch.setattr(stall.StallWatch, "check", counted)
    return seen


async def _looks(checks, n: int = 5) -> None:
    start = checks["n"]
    assert await until(lambda: checks["n"] >= start + n, 30), "the watch never looked"


def _hang(pidfile) -> str:
    return (
        f'"{sys.executable}" -c "import os,time,pathlib; '
        f"pathlib.Path('{pidfile.as_posix()}').write_text(str(os.getpid())); time.sleep(600)\""
    )


def _ledger(app) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all())
        for r in rows:
            db.expunge(r)
        return rows


def _stalls(app, turn: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn) if e == "stall"]


def _bell(app) -> list:
    return [e for e in app.state.platform.event_bus.history if e.type == "build.turn_stalled"]


async def _pid(pidfile) -> int:
    def read():
        try:
            text = pidfile.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(text) if text else None

    pid = await until(read, 60)
    assert pid, "the command never started"
    return pid


def _alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


# --------------------------------------------------------------------------- #
# the notice, and Stop the step
# --------------------------------------------------------------------------- #


async def test_a_hung_step_is_said_once_at_ten_minutes_and_stop_the_step_ends_only_it(
    app, work, tmp_path, clock, checks,
):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    pidfile = tmp_path / "hang.pid"
    script = wire(app, Script([
        [
            ToolCall(id="c1", name="Bash", arguments={"command": _hang(pidfile)}),
            ToolCall(id="c2", name="Bash", arguments={"command": "echo second ran"}),
        ],
        "Moved on without it.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run the slow thing", "turn_id": "bt-st"},
                                         drop=drop, timeout=120))
    pid = await _pid(pidfile)
    try:
        # Nine minutes 59 seconds of nothing: no notice yet.
        clock.advance(599)
        await _looks(checks)
        assert _stalls(app, "bt-st") == [] and _bell(app) == []
        # Ten minutes: the notice, naming the step in the run log's words.
        clock.advance(2)
        got = await until(lambda: _stalls(app, "bt-st"), 30)
        assert got, "no stall notice at ten minutes"
        notice = got[0]
        assert notice["words"].startswith("No progress for 10 minutes — waiting on this step: running a command")
        assert notice["step"] == {"id": "c1", "name": "shell"}
        # ONE notice for the episode: more silence says nothing more.
        clock.advance(3600)
        await _looks(checks)
        assert len(_stalls(app, "bt-st")) == 1
        bell = _bell(app)
        assert len(bell) == 1 and bell[0].session_id == f"build:{sid}"
        assert bell[0].payload["words"] == notice["words"] and bell[0].payload["step"]["id"] == "c1"
        # Stop the step: exactly that call, by process id.
        assert _alive(pid)
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step", {"step_id": "c1"})
        assert status == 200 and out["step"] is True, out
        assert await until(lambda: not _alive(pid), 30), "the stopped step's process is still running"
        lg = turn_log(app, "bt-st")
        assert await until(lambda: lg.status != "running", 60)
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
    frames = turn_frames(app, "bt-st")
    done = dict(frames)["done"]
    # The reply carried on: the next call ran and the model answered.
    assert done["tools_used"] == ["shell"] and done["reply"] == "Moved on without it."
    # The episode ended with the next frame.
    assert any(d.get("over") for e, d in frames if e == "stall")
    rows = [r for r in _ledger(app) if r.session_id == f"build:{sid}"]
    assert len(rows) == 2
    stopped = [r for r in rows if not r.ok]
    assert len(stopped) == 1 and "stopped by you" in (stopped[0].output or "")
    assert [r for r in rows if r.ok][0].output and "second ran" in [r for r in rows if r.ok][0].output
    # The model was told, in plain words, that the user stopped that step.
    told = [m for m in script.calls[1]["messages"] if getattr(m, "tool_call_id", "") == "c1"]
    assert told and "stopped by the user" in told[0].content


async def test_stop_the_step_ends_exactly_that_calls_process_and_nothing_else_the_session_runs(
    app, work, tmp_path, clock, checks,
):
    """A program the reply started earlier (a dev server, ``process_start``)
    keeps running: only the stopped call's process is gone — by pid."""
    sid = new_session(app, work, approval_mode="yolo")["id"]
    server_pid = tmp_path / "server.pid"
    hang_pid = tmp_path / "hang.pid"
    server = (
        f'"{sys.executable}" -c "import os,time,pathlib; '
        f"pathlib.Path('{server_pid.as_posix()}').write_text(str(os.getpid())); time.sleep(600)\""
    )
    wire(app, Script([
        [ToolCall(id="b1", name="Bash", arguments={"command": server, "run_in_background": True})],
        [ToolCall(id="c1", name="Bash", arguments={"command": _hang(hang_pid)})],
        "Carried on.",
    ]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "start it, then the slow thing", "turn_id": "bt-two"},
                                         drop=drop, timeout=120))
    try:
        background = await _pid(server_pid)
        hung = await _pid(hang_pid)
        assert background != hung
        clock.advance(601)
        got = await until(lambda: _stalls(app, "bt-two"), 30)
        assert got and got[0]["step"]["id"] == "c1"
        # A step id that is not running stops nothing: 404, the step goes on.
        status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step", {"step_id": "b1"})
        assert status == 404 and _alive(hung)
        status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step", {"step_id": "c1"})
        assert status == 200
        assert await until(lambda: not _alive(hung), 30)
        lg = turn_log(app, "bt-two")
        assert await until(lambda: lg.status != "running", 60)
        assert _alive(background), "Stop the step ended a program the session started earlier"
        assert dict(turn_frames(app, "bt-two"))["done"]["reply"] == "Carried on."
    finally:
        drop.set()
        await task
        procs.end_session(f"build:{sid}")
        P.end_session(f"build:{sid}")


async def test_stop_the_step_answers_404_for_a_step_that_is_not_running(app, work):
    sid = new_session(app, work)["id"]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/stop-step", {"step_id": "nope"})
    assert status == 404 and out["detail"] == "That step is no longer running."


async def test_a_reply_waiting_on_the_user_is_never_a_stall(app, work, clock, checks):
    """The card is the notice: an hour on an unanswered card says nothing."""
    sid = new_session(app, work)["id"]  # approve-for-me: a command cards
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-wait"}, drop=drop))
    try:
        card = await until(lambda: [d for e, d in turn_frames(app, "bt-wait") if e == "approval"], 30)
        assert card
        clock.advance(3600)
        await _looks(checks)
        assert _stalls(app, "bt-wait") == [] and _bell(app) == []
        # Answered: the clock starts again from the answer (no notice owed).
        assert (await asgi(app, "POST", f"/chat/approvals/{card[0]['id']}", {"decision": "deny"}))[0] == 200
        lg = turn_log(app, "bt-wait")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        drop.set()
        await task
    assert _stalls(app, "bt-wait") == []


async def test_a_model_that_says_nothing_for_ten_minutes_is_named_and_offers_no_step(app, work, clock, checks):
    gate = asyncio.Event()
    sid = new_session(app, work, approval_mode="yolo")["id"]

    async def hold(_script):
        await gate.wait()
        return "Finally."

    wire(app, Script([hold]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "think", "turn_id": "bt-model"}, drop=drop))
    try:
        assert await until(lambda: any(e == "round" for e, _ in turn_frames(app, "bt-model")), 30)
        clock.advance(601)
        got = await until(lambda: _stalls(app, "bt-model"), 30)
        assert got and "step" not in got[0]
        assert got[0]["words"].startswith("No progress for 10 minutes — waiting on ")
        assert got[0]["words"].endswith("answer")
        gate.set()
        lg = turn_log(app, "bt-model")
        assert await until(lambda: lg.status != "running", 30)
    finally:
        gate.set()
        drop.set()
        await task
    # The answer ended the episode, and a later silence would be a new one.
    assert any(d.get("over") for e, d in turn_frames(app, "bt-model") if e == "stall")


# --------------------------------------------------------------------------- #
# a long command that keeps printing is not a stall
# --------------------------------------------------------------------------- #

#: One line (cmd.exe ends a command at a newline): 20 lines, 50 ms apart.
PRINTER = (
    f'"{sys.executable}" -c "import time; [(print(i, flush=True), time.sleep(0.05)) for i in range(20)]"'
)


def _printer_cmd() -> list[str]:
    return [sys.executable, "-c", "import time\nfor i in range(5):\n    print(i, flush=True); time.sleep(0.05)"]


def test_a_command_run_in_a_watched_turn_marks_progress_as_it_prints():
    """``procs.run`` reads a watched command's output piece by piece: each
    piece is a progress mark (with nothing listening, ``communicate`` as
    before)."""
    marks: list[str] = []
    with progress.scope(lambda: marks.append(threading.current_thread().name)):
        res = procs.run(_printer_cmd(), timeout=60)
    assert res.returncode == 0 and res.stdout.split() == [b"0", b"1", b"2", b"3", b"4"]
    assert len(marks) >= 1 and all(n != threading.main_thread().name for n in marks)
    # Nothing listening: the same output, no marks.
    res2 = procs.run(_printer_cmd(), timeout=60)
    assert res2.stdout.split() == [b"0", b"1", b"2", b"3", b"4"]


def test_a_command_writing_to_files_in_a_watched_turn_marks_progress(monkeypatch):
    monkeypatch.setattr(procs, "MOVING_LOOK_S", 0.01)
    marks: list[int] = []
    cmd = [sys.executable, "-c", "import time\nfor i in range(10):\n    print(i, flush=True); time.sleep(0.05)"]
    with progress.scope(lambda: marks.append(1)):
        res = procs.run_to_files(cmd, timeout=60)
    assert res.returncode == 0 and b"9" in res.stdout
    assert marks, "a command whose output file grew was never a progress mark"


def test_a_watched_command_still_times_out_and_is_killed():
    """The piece-by-piece read keeps ``communicate``'s bound: the child AND
    its pipes within the timeout, else the tree is killed."""
    with progress.scope(lambda: None):
        res = procs.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert res.timed_out is True


async def test_a_command_that_keeps_printing_reaches_the_watch_from_its_own_thread(app, work, monkeypatch):
    """End to end: a coding reply's command prints, and every piece reaches
    the reply's stall watch from the thread reading it — the progress mark
    travelled from the turn's pump task into the tool's worker thread."""
    marks: list[str] = []
    real = stall.StallWatch.progress

    def spied(self):
        marks.append(threading.current_thread().name)
        return real(self)

    monkeypatch.setattr(stall.StallWatch, "progress", spied)
    # Git's bash writes to files, looked at every MOVING_LOOK_S; cmd's pipes
    # are read as they come.
    monkeypatch.setattr(procs, "MOVING_LOOK_S", 0.01)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([[ToolCall(id="p1", name="Bash", arguments={"command": PRINTER})], "printed"]))
    status, raw = await open_turn(app, sid, {"text": "print", "turn_id": "bt-print"}, timeout=120)
    assert status == 200
    off_loop = [n for n in marks if n != threading.main_thread().name]
    assert off_loop, f"no progress mark came from the command's reader: {marks}"


async def test_a_process_tool_that_prints_while_waited_on_marks_progress(app, work):
    from iron_jarvis.tools.base import ToolContext

    marks: list[int] = []
    platform = app.state.platform
    ctx = ToolContext(workspace=work, session_id="build:x", agent_run_id="r", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    tool = P.ProcessStartTool()
    with progress.scope(lambda: marks.append(1)):
        res = await tool.execute({"command": PRINTER, "until_exit": True, "wait_ms": 20000}, ctx)
    try:
        assert res.ok, res.error
        assert marks, "output that arrived while the call waited was never a progress mark"
    finally:
        P.end_session("build:x")


# --------------------------------------------------------------------------- #
# the watch itself
# --------------------------------------------------------------------------- #


def test_the_watch_opens_one_episode_and_progress_ends_it(clock):
    parked = {"v": False}
    w = stall.StallWatch(parked=lambda: parked["v"])
    clock.advance(599)
    assert w.check() is None
    clock.advance(1)
    first = w.check()
    assert first and first["words"] == "No progress for 10 minutes — waiting on the model's answer"
    clock.advance(600)
    assert w.check() is None  # the same episode: said once
    w.progress()
    assert w.check() == {"over": True}
    # Waiting on the user is never a stall.
    parked["v"] = True
    clock.advance(10_000)
    assert w.check() is None
    parked["v"] = False
    assert w.check() is not None and w.episodes == 2


def test_a_frame_is_progress_and_starts_the_ten_minutes_again(clock):
    """Any frame of the turn (a token, a step, an app's progress) moves the
    clock: a reply that keeps answering is never a stall, and after an
    episode ends on a frame the next notice is another ten minutes away."""
    w = stall.StallWatch()
    frame = "event: token\ndata: " + json.dumps({"text": "still going"}) + "\n\n"
    clock.advance(601)
    assert w.saw(frame) is True
    assert w.check() is None
    clock.advance(601)
    assert w.check() is not None  # the episode opens
    w.saw(frame)
    assert w.ended() == {"over": True}
    assert w.check() is None, "a frame did not start the ten minutes again"
    clock.advance(599)
    assert w.check() is None
    # A comment (a keep-alive) is no frame, and no progress.
    assert w.saw(": keepalive\n\n") is False


def test_progress_after_the_clock_moved_keeps_the_reply_out_of_a_stall(clock):
    w = stall.StallWatch()
    for _ in range(20):
        clock.advance(300)
        w.progress()  # a command printed
        assert w.check() is None


def test_the_words_name_the_running_step_in_the_run_logs_words(clock):
    w = stall.StallWatch()
    w.saw(
        "event: tool_call\ndata: "
        + json.dumps({"id": "c9", "name": "read_file", "status": "started", "args": {"path": "src/app.py"}})
        + "\n\n"
    )
    clock.advance(601)
    notice = w.check()
    assert notice["words"] == "No progress for 10 minutes — waiting on this step: reading app.py"
    assert notice["step"] == {"id": "c9", "name": "read_file"}
