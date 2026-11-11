"""Closing a terminal on Linux or macOS never waits for ever.

On macOS a killed shell cannot finish exiting while output it wrote is still
unread on the app's side of the terminal; ``kill`` waited on it with no bound
and two Creative Studio tests hung in the 2026-11-11 Mac trial. ``kill`` now
drops waiting output while it waits, and closes its side if that is not enough.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the POSIX terminal backend")


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def test_kill_returns_with_unread_output_waiting():
    from iron_jarvis.terminals.backend import PosixPtyBackend

    b = PosixPtyBackend()
    # Far more output than a terminal holds unread, then a long sleep.
    code = "import sys, time\nsys.stdout.write('x' * 400000)\nsys.stdout.flush()\ntime.sleep(300)\n"
    b.start([sys.executable, "-c", code], cwd="", env=None, cols=80, rows=24)
    pid = b._pid
    time.sleep(1.0)  # let it fill the terminal; nothing reads it

    done = threading.Event()
    threading.Thread(target=lambda: (b.kill(), done.set()), daemon=True).start()
    assert done.wait(20), "kill() did not come back"
    assert b._pid is None
    assert _gone(pid)
    b.kill()  # a second call signals nothing and returns
    b.write("x")  # typing into a closed terminal is not an error


def test_a_shell_that_exits_normally_is_reaped_and_reports_its_code():
    from iron_jarvis.terminals.backend import PosixPtyBackend

    b = PosixPtyBackend()
    b.start([sys.executable, "-c", "raise SystemExit(3)"], cwd="", env=None, cols=80, rows=24)
    end = time.monotonic() + 20
    while b.is_alive() and time.monotonic() < end:
        b.read_nonblocking()
        time.sleep(0.05)
    assert not b.is_alive() and b.exit_code == 3
