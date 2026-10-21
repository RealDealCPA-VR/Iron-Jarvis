"""v1.364.0 (IJ harness Phase 1): long-running programs a session starts
and talks to — ``process_start`` / ``process_read`` / ``process_write`` /
``process_stop`` (Codex's exec_command + write_stdin, Claude Code's
background Bash + reading its output + TaskStop).

Real child processes (the Python running the suite), started through
``core.procs.spawn`` (its own kill-on-close Job). Pinned:

* output arrives, is read once (a second read returns only what is new), a
  write reaches the program's input, a stop ends it and its whole tree;
* the program belongs to the SESSION that started it: another session can
  neither read nor stop it, and ``procs.end_session`` (what the runtime
  calls when a run ends) stops it — ``procs.end_all`` at shutdown too;
* at most four run per session; output is a bounded ring and a read that
  skipped dropped bytes says so; a wait never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness import process as P
from iron_jarvis.tools.base import ToolContext

PY = f'"{sys.executable}"'
ECHO = PY + " -u -c \"import sys;print('ready',flush=True);[print('got',l.strip(),flush=True) for l in sys.stdin]\""
SLEEPER = PY + " -u -c \"import time;print('up',flush=True);time.sleep(120)\""


def _ctx(ws: Path, session: str = "s-proc") -> ToolContext:
    return ToolContext(
        workspace=ws, session_id=session, agent_run_id="r",
        config=SimpleNamespace(home=ws.parent, sandbox={"modify_env": "allow"}),
        event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


@pytest.fixture(autouse=True)
def _clean_table():
    yield
    # Whatever a test left running ends here (and the table is empty again).
    P.end_session(None)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x1000 | 0x00100000, False, pid)  # QUERY_LIMITED | SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # WAIT_TIMEOUT
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _gone(pid: int, within: float = 15.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


async def _start(ws, command=ECHO, session="s-proc", **extra):
    return await P.ProcessStartTool().execute({"command": command, "wait_ms": 10_000, **extra}, _ctx(ws, session))


async def test_start_read_write_stop_round_trip(ws):
    started = await _start(ws)
    assert started.ok, started.error
    assert started.output.startswith("Started p")
    assert "ready" in started.output and started.data["running"] is True
    pid, handle = started.data["pid"], started.data["id"]
    again = await P.ProcessReadTool().execute({"id": handle}, _ctx(ws))
    assert "No new output." in again.output, "a read returns only what is NEW"
    wrote = await P.ProcessWriteTool().execute({"id": handle, "input": "hello\n", "wait_ms": 10_000}, _ctx(ws))
    assert wrote.ok and "got hello" in wrote.output
    stopped = await P.ProcessStopTool().execute({"id": handle}, _ctx(ws))
    assert stopped.ok and stopped.output.startswith(f"Stopped {handle}:")
    assert stopped.data["was_running"] is True
    assert _gone(pid), "the program really ended"
    after = await P.ProcessReadTool().execute({"id": handle}, _ctx(ws))
    assert after.ok is False and "no program" in after.error


async def test_a_program_that_ends_reports_its_exit_code(ws):
    res = await _start(ws, PY + " -c \"print('done')\"")
    assert res.ok
    for _ in range(100):
        read = await P.ProcessReadTool().execute({"id": res.data["id"], "wait_ms": 200}, _ctx(ws))
        if read.data["running"] is False:
            break
    assert "Exited with code 0" in read.output
    assert read.data["exit_code"] == 0


async def test_another_session_cannot_read_or_stop_it(ws):
    """MUTATION: look programs up by id alone (not by session) -> red."""
    res = await _start(ws, SLEEPER)
    handle = res.data["id"]
    other = _ctx(ws, "s-other")
    assert (await P.ProcessReadTool().execute({"id": handle}, other)).ok is False
    stop = await P.ProcessStopTool().execute({"id": handle}, other)
    assert stop.ok is False and "no program" in stop.error
    assert _alive(res.data["pid"])


async def test_the_runs_end_stops_its_programs(ws):
    """``procs.end_session`` is what the agent runtime calls when a run ends
    (v1.333.0); the process table is one of its end hooks. MUTATION: remove
    ``procs.add_end_hook(end_session)`` -> the program outlives its run -> red."""
    res = await _start(ws, SLEEPER, session="s-run")
    keep = await _start(ws, SLEEPER, session="s-keep")
    stopped = await asyncio.to_thread(procs.end_session, "s-run")
    assert res.data["pid"] in stopped
    assert _gone(res.data["pid"])
    assert _alive(keep.data["pid"]), "another session's program is untouched"
    await asyncio.to_thread(procs.end_all)
    assert _gone(keep.data["pid"])


async def test_at_most_four_run_per_session(ws):
    """MUTATION: drop the cap -> a fifth starts -> red."""
    pids = []
    for _ in range(P.MAX_RUNNING_PER_SESSION):
        res = await _start(ws, SLEEPER, wait_ms=0)
        assert res.ok
        pids.append(res.data["pid"])
    fifth = await _start(ws, SLEEPER, wait_ms=0)
    assert fifth.ok is False and "already has 4 programs running" in fifth.error
    assert len(P.TABLE.running("s-proc")) == 4


async def test_stop_ends_the_whole_tree(ws):
    """The child starts a grandchild; stopping the child ends both (the
    kill-on-close Job)."""
    marker = ws / "grand.pid"
    (ws / "grand.py").write_text(
        "import os, time\n"
        f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    (ws / "child.py").write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(ws / 'grand.py')!r}])\n"
        "print('spawned', flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    res = await _start(ws, f"{PY} -u child.py")
    assert res.ok
    deadline = time.monotonic() + 20
    while not marker.exists() or not marker.read_text().strip():
        assert time.monotonic() < deadline, "the grandchild never started"
        await asyncio.sleep(0.05)
    grand = int(marker.read_text())
    await P.ProcessStopTool().execute({"id": res.data["id"]}, _ctx(ws))
    assert _gone(res.data["pid"]) and _gone(grand)


async def test_cwd_is_confined_to_the_workspace(ws):
    (ws / "sub").mkdir()
    ok = await _start(ws, PY + " -c \"import os;print(os.getcwd())\"", cwd="sub")
    assert ok.ok
    for _ in range(50):
        if str((ws / "sub").resolve()) in (ok.output + (await P.ProcessReadTool().execute({"id": ok.data["id"], "wait_ms": 200}, _ctx(ws))).output):
            break
    else:
        pytest.fail("the program did not run in its cwd")
    bad = await _start(ws, SLEEPER, cwd="..")
    assert bad.ok is False and "outside the session's folder" in bad.error


async def test_waiting_never_blocks_the_event_loop(ws):
    """THREAD-FREE PROOF: a ticker on the same loop keeps ticking while a
    read waits for output that never comes."""
    res = await _start(ws, SLEEPER)
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    read = await P.ProcessReadTool().execute({"id": res.data["id"], "wait_ms": 600}, _ctx(ws))
    done.set()
    await task
    assert read.ok and "No new output." in read.output
    assert ticks >= 10, ticks


async def test_bad_arguments_are_refused_in_words(ws):
    empty = await P.ProcessStartTool().execute({"command": "  "}, _ctx(ws))
    assert empty.ok is False and "`command` is empty" in empty.error
    nowait = await P.ProcessStartTool().execute({"command": "x", "wait_ms": "later"}, _ctx(ws))
    assert nowait.ok is False and "`wait_ms` must be a whole number" in nowait.error
    noid = await P.ProcessReadTool().execute({}, _ctx(ws))
    assert noid.ok is False and "`id` is required" in noid.error
    res = await _start(ws, SLEEPER)
    blank = await P.ProcessWriteTool().execute({"id": res.data["id"]}, _ctx(ws))
    assert blank.ok is False and "give `input`" in blank.error
    huge = await P.ProcessWriteTool().execute({"id": res.data["id"], "input": "x" * (P.MAX_INPUT_CHARS + 1)}, _ctx(ws))
    assert huge.ok is False and "at most" in huge.error


# --------------------------------------------------------------------------- #
# The ring buffer and the UTF-8 tail (units)
# --------------------------------------------------------------------------- #


class _FakeProc:
    def __init__(self, code=None):
        self.code = code
        self.pid = 1

    def poll(self):
        return self.code


def test_a_read_that_skipped_dropped_output_says_how_much(monkeypatch):
    monkeypatch.setattr(P, "RING_BYTES", 10)
    mp = P.ManagedProcess(handle="p9", session_id="s", command="c", cwd=".", proc=_FakeProc())
    mp.append(b"0123456789")
    mp.append(b"abcde")
    text, dropped, more = mp.take()
    assert dropped == 5 and text == "56789abcde" and more is False
    rendered = P._render(mp, text, dropped, more)
    assert "[5 bytes of earlier output were dropped (only the newest 10 are kept)]" in rendered


def test_a_read_is_capped_and_says_more_is_waiting():
    mp = P.ManagedProcess(handle="p9", session_id="s", command="c", cwd=".", proc=_FakeProc())
    mp.append(b"x" * 100)
    text, dropped, more = mp.take(limit_chars=40)
    assert len(text) == 40 and more is True
    assert "[more output is waiting — call process_read on p9 again]" in P._render(mp, text, 0, more)
    rest, _, more2 = mp.take()
    assert len(rest) == 60 and more2 is False


def test_a_split_utf8_character_waits_for_its_last_byte():
    mp = P.ManagedProcess(handle="p9", session_id="s", command="c", cwd=".", proc=_FakeProc())
    euro = "€".encode("utf-8")  # three bytes
    mp.append(b"price " + euro[:2])
    first, _, _ = mp.take()
    assert first == "price "
    mp.append(euro[2:])
    second, _, _ = mp.take()
    assert second == "€"
    assert P._incomplete_tail(b"ab") == 0 and P._incomplete_tail(euro[:1]) == 1


def test_an_ended_program_says_how_long_it_ran_not_how_long_ago_it_started():
    mp = P.ManagedProcess(handle="p9", session_id="s", command="c", cwd=".", proc=_FakeProc(3))
    mp.started_at = time.monotonic() - 100
    assert mp.returncode() == 3
    assert mp.ended_at is not None, "the first sight of the exit is stamped"
    mp.ended_at = mp.started_at + 2.0
    assert P._status_line(mp) == "Exited with code 3 after 2.0 s."
    running = P.ManagedProcess(handle="p8", session_id="s", command="c", cwd=".", proc=_FakeProc())
    assert running.returncode() is None and running.ended_at is None


def test_the_end_hook_is_registered_once():
    assert P.end_session in procs._END_HOOKS
    procs.add_end_hook(P.end_session)
    assert procs._END_HOOKS.count(P.end_session) == 1


def test_an_end_hook_that_raises_never_stops_the_others(monkeypatch):
    calls: list[str | None] = []

    def bad(_sid):
        raise RuntimeError("boom")

    def good(sid):
        calls.append(sid)
        return [7]

    monkeypatch.setattr(procs, "_END_HOOKS", [bad, good])
    assert procs.end_session("x") == [7] and calls == ["x"]
    assert procs.end_all() == [7] and calls == ["x", None]


def test_the_reader_thread_is_a_daemon():
    """A reader blocked on a pipe never holds the process open at exit."""
    mp = P.ManagedProcess(handle="p9", session_id="s", command="c", cwd=".", proc=_FakeProc(0))
    t = threading.Thread(target=P._reader, args=(mp,), daemon=True)
    t.start()
    t.join(2)
    assert mp.eof is True
