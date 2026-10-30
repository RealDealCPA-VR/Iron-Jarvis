"""v1.409.1 (a USER BUG): a freeze that silences every Python report still
leaves a trace.

The first start after the v1.409.0 update stopped answering about a second
after the dashboard opened, and nothing more was written by the daemon until
the desktop watchdog killed it 90 s later — not the access log, and not the
v1.407.1 freeze watch either. Whatever stopped it held something every
Python thread needs (the interpreter lock, a log handler's lock) or left no
request worker free. ``daemon/boot_watch.LoopWatch`` now (a) arms
faulthandler's C watchdog, which needs neither, (b) writes its own reports
to the trace file before it tries the log, and (c) reports every request
worker being busy.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

from iron_jarvis.daemon import boot_watch
from iron_jarvis.daemon.boot_watch import LoopWatch, trace_path_for

SRC = str(Path(boot_watch.__file__).resolve().parents[2])


def _run_child(code: str, tmp_path: Path, timeout: float = 60) -> subprocess.CompletedProcess:
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(code), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(tmp_path),
    )


HOLD_THE_INTERPRETER = """
import ctypes, sys, time
from iron_jarvis.daemon.boot_watch import LoopWatch
trace = sys.argv[0].replace("child.py", "freeze.txt")
w = LoopWatch(trace_path=trace, trace_after_s=1.0, trace_rearm_s=0.2, poll_s=0.1).start()
time.sleep(0.6)  # the watch runs and re-arms while Python is free
# PyDLL never releases the interpreter lock: every Python thread, the watch's
# included, stands still for 3.5 s, as in the user's freeze.
ctypes.PyDLL("kernel32").Sleep({hold_ms})
w.stop()
print("done")
"""


def test_a_held_interpreter_lock_still_dumps_every_threads_stack(tmp_path):
    done = _run_child(HOLD_THE_INTERPRETER.format(hold_ms=3500), tmp_path)
    assert done.returncode == 0, done.stderr
    text = (tmp_path / "freeze.txt").read_text(encoding="utf-8")
    assert "watching for freezes" in text
    # faulthandler's own words: the stacks were written while Python held still.
    assert "Timeout (0:00:01)!" in text, text
    assert "child.py" in text, text  # the frame stuck in the held call


def test_a_healthy_process_writes_no_dump(tmp_path):
    done = _run_child(HOLD_THE_INTERPRETER.format(hold_ms=0).replace("time.sleep(0.6)", "time.sleep(3.0)"), tmp_path)
    assert done.returncode == 0, done.stderr
    text = (tmp_path / "freeze.txt").read_text(encoding="utf-8")
    assert "watching for freezes" in text
    assert "Timeout" not in text, "the watch's own re-arm must keep a healthy process quiet"


def test_a_frozen_loops_words_reach_the_file_even_when_the_log_is_stuck(tmp_path):
    now = [0.0]
    trace = tmp_path / "freeze.txt"
    seen_in_file: list[bool] = []

    def stuck_log(text: str) -> None:
        # The words are already on disk when the log is tried.
        seen_in_file.append("event loop frozen for 21 s" in trace.read_text(encoding="utf-8"))
        raise RuntimeError("the log handler is stuck")

    w = LoopWatch(clock=lambda: now[0], report=stuck_log, trace_path=str(trace))
    w._open_trace()
    try:
        now[0] = 21.0
        w.check()
    finally:
        w.stop()
    assert seen_in_file == [True]


def test_every_worker_busy_is_reported_once_per_threshold_and_resets(tmp_path):
    now = [0.0]
    said: list[str] = []
    full = [True]
    w = LoopWatch(clock=lambda: now[0], report=said.append, pool_full=lambda: full[0])
    for t in (0.0, 10.0, 21.0, 30.0, 46.0, 50.0):
        now[0] = t
        w.beat()  # the loop itself is alive
        w.check()
    assert len(said) == 2 and all("every worker" in s for s in said)
    full[0] = False
    for t, f in ((51.0, False), (60.0, True), (81.0, True)):
        now[0] = t
        full[0] = f
        w.beat()
        w.check()
    assert len(said) == 3, "a fresh busy spell is reported again"


def test_the_trace_sits_beside_daemon_log_when_that_folder_exists(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    assert trace_path_for(home) == str(home / "daemon-freeze.txt")
    (tmp_path / "logs").mkdir()
    assert trace_path_for(home) == str(tmp_path / "logs" / "daemon-freeze.txt")


def test_the_daemon_arms_the_trace_only_when_frozen_or_asked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    (tmp_path / "logs").mkdir()
    home = tmp_path / ".ironjarvis"
    monkeypatch.delenv("IRONJARVIS_FREEZE_TRACE", raising=False)
    with TestClient(create_app(str(tmp_path))):
        pass
    assert not (tmp_path / "logs" / "daemon-freeze.txt").exists()

    monkeypatch.setenv("IRONJARVIS_FREEZE_TRACE", "1")
    with TestClient(create_app(str(tmp_path))):
        trace = tmp_path / "logs" / "daemon-freeze.txt"
        assert "watching for freezes" in trace.read_text(encoding="utf-8")
    assert home.is_dir()


def test_stop_cancels_the_dump_so_a_test_process_is_never_dumped_later(tmp_path):
    import faulthandler

    calls: list[str] = []
    real_cancel = faulthandler.cancel_dump_traceback_later

    def cancel() -> None:
        calls.append("cancel")
        real_cancel()

    w = LoopWatch(trace_path=str(tmp_path / "freeze.txt"), poll_s=60)
    try:
        faulthandler.cancel_dump_traceback_later = cancel  # type: ignore[assignment]
        w.start()
        w.stop()
    finally:
        faulthandler.cancel_dump_traceback_later = real_cancel  # type: ignore[assignment]
        real_cancel()
    assert calls == ["cancel"]
    assert threading.active_count() >= 1
