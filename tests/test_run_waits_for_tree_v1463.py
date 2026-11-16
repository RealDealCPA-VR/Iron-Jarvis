"""v1.463.0: a run can wait for everything its child STARTED.

Found by running the app's own page screenshot on this PC (Edge 157): the
browser's first process returns in 0.05 s with exit 0 and the browser it
started writes the picture about half a second later. ``core/procs.run``
closed the kill-on-close job the moment the first process returned, which
ended that browser, so the tool answered "the browser wrote no picture
(exit 0)" every time.

These tests use a stand-in "browser": a Python child that starts a second
process and exits at once; the second process writes the file later.
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness import page_shot

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")

#: The first process: start the real worker and return straight away.
_LAUNCHER = (
    "import subprocess, sys;"
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]],"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
)
#: The worker: write its pid, wait, then write the "picture".
_WORKER = (
    "import os, sys, time;"
    "open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()));"
    "time.sleep(float(sys.argv[2]));"
    "open(sys.argv[1], 'w').write('picture')"
)


def _argv(out: Path, delay: float) -> list[str]:
    return [sys.executable, "-c", _LAUNCHER, _WORKER, str(out), str(delay)]


def _worker_alive(out: Path) -> bool:
    try:
        return psutil.pid_exists(int(Path(str(out) + ".pid").read_text()))
    except (OSError, ValueError):
        return False


def test_without_the_wait_the_late_writer_is_ended_and_writes_nothing(tmp_path):
    """The control: this is the failure the screenshot tool had."""
    out = tmp_path / "shot.txt"
    res = procs.run(_argv(out, 1.0), timeout=30)
    assert res.returncode == 0 and not res.timed_out
    time.sleep(2.0)
    assert not out.exists()
    assert not _worker_alive(out)


def test_with_the_wait_the_file_is_there_when_the_run_returns(tmp_path):
    out = tmp_path / "shot.txt"
    res = procs.run(_argv(out, 1.0), timeout=30, wait_tree=True)
    assert res.returncode == 0 and not res.timed_out
    assert out.read_text() == "picture"
    assert not _worker_alive(out)


def test_the_wait_shares_the_calls_own_time_limit_and_ends_the_tree(tmp_path):
    out = tmp_path / "shot.txt"
    began = time.monotonic()
    res = procs.run(_argv(out, 60.0), timeout=2.0, wait_tree=True)
    took = time.monotonic() - began
    assert res.timed_out is True
    # A hang detector, not a speed bar: the worker would run for a minute.
    assert took < 30
    deadline = time.monotonic() + 10
    while _worker_alive(out) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _worker_alive(out)
    assert not out.exists()


def test_a_child_that_starts_nothing_returns_as_before(tmp_path):
    res = procs.run([sys.executable, "-c", "print('hi')"], timeout=30, wait_tree=True)
    assert res.returncode == 0 and res.stdout.strip() == b"hi" and not res.timed_out


def test_the_page_screenshot_no_longer_runs_a_bare_command_line():
    """v1.515.0 (second review S1): the one-shot ``page_screenshot`` that
    first needed ``wait_tree`` is now taken through the agent browser's
    backend (``page_browser.AgentBrowsers.one_shot``: its driver adopted into
    a kill-on-close job, the address rules on every navigation), so the
    bare ``--screenshot`` call site this file pinned is gone. ``wait_tree``
    itself stays pinned above."""
    src = inspect.getsource(page_shot.PageScreenshotTool.execute)
    assert "BROWSERS.one_shot(" in src
    assert not hasattr(page_shot, "_capture") and not hasattr(page_shot, "browser_argv")
