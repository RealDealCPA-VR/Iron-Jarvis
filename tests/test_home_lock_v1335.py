"""v1.335.0 — one daemon per data folder.

The port probe in ``serve`` only stopped a second daemon on the SAME port; two
on different ports could open one home and write one database at once.
``core/home_lock.acquire`` takes an OS lock on ``<home>/daemon.lock`` for the
life of the process (the OS drops it on exit, crash included) and ``serve``
refuses to start without it, naming the copy that holds it.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import iron_jarvis.daemon.cli as cli_mod
from iron_jarvis.core import home_lock

_HOLDER = r"""
import sys, time
from iron_jarvis.core.home_lock import acquire
acquire(sys.argv[1], port=int(sys.argv[2]), wait_s=0)
print("held", flush=True)
time.sleep(60)
"""


def _hold(home: Path, port: int = 8799) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(home), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline().strip()
    assert line == "held", proc.stderr.read()
    return proc


def _stop(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait(timeout=10)


def _try_in_child(home: Path) -> subprocess.CompletedProcess:
    code = (
        "import sys\n"
        "from iron_jarvis.core.home_lock import acquire, HomeLockHeld\n"
        "try:\n"
        "    acquire(sys.argv[1], wait_s=0)\n"
        "    print('acquired')\n"
        "except HomeLockHeld as e:\n"
        "    print('refused:', e)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(home)], capture_output=True, text=True, timeout=60
    )


def test_a_second_process_is_refused_and_told_who_holds_the_folder(tmp_path):
    home = tmp_path / "home"
    holder = _hold(home, port=8799)
    try:
        out = _try_in_child(home).stdout
        assert out.startswith("refused:"), out
        # (The venv's python.exe is a launcher, so the holder's own pid is
        # the one it recorded, not Popen's.)
        owner = home_lock.read_owner(home)
        assert owner and f"pid {owner['pid']}" in out and "port 8799" in out
        # The desktop's startup classifier keys on these words (main.js
        # classifyStartupFailure → "db-locked").
        assert re.search(r"database is locked", out)
    finally:
        _stop(holder)


def test_the_lock_dies_with_its_process_even_a_killed_one(tmp_path):
    home = tmp_path / "home"
    holder = _hold(home)
    _stop(holder)  # a hard kill, like a crash
    deadline = time.monotonic() + 10
    out = ""
    while time.monotonic() < deadline:
        out = _try_in_child(home).stdout
        if out.startswith("acquired"):
            break
        time.sleep(0.2)
    assert out.startswith("acquired"), out


def test_acquire_waits_for_a_dying_holder(tmp_path):
    """The desktop respawns a crashed daemon at once; the old process can
    still hold the lock for a moment."""
    home = tmp_path / "home"
    holder = _hold(home)
    import threading

    threading.Timer(0.6, lambda: _stop(holder)).start()
    t0 = time.monotonic()
    code = (
        "import sys\nfrom iron_jarvis.core.home_lock import acquire\n"
        "acquire(sys.argv[1], wait_s=8)\nprint('acquired')\n"
    )
    res = subprocess.run([sys.executable, "-c", code, str(home)],
                         capture_output=True, text=True, timeout=60)
    assert res.stdout.strip() == "acquired", res.stderr
    assert time.monotonic() - t0 >= 0.5


def test_serve_refuses_a_home_another_daemon_holds(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("IRONJARVIS_HOME", str(home))
    monkeypatch.setattr(cli_mod, "_port_in_use", lambda h, p: False)
    monkeypatch.setattr(home_lock, "DEFAULT_WAIT_S", 0.0)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("served anyway"))
    holder = _hold(home)
    try:
        res = CliRunner().invoke(cli_mod.app, ["serve", "--port", "18798", "--root", str(tmp_path)])
    finally:
        _stop(holder)
    assert res.exit_code == 1, res.output
    try:
        err = res.stderr
    except ValueError:  # an older Click mixes stderr into output
        err = ""
    assert "database is locked" in res.output + err


def test_only_serve_takes_the_lock():
    """The one-shot CLI commands read a home the daemon is running in on
    purpose (doctor, sessions, backup); only `serve` may take it."""
    import inspect

    src = inspect.getsource(cli_mod)
    assert src.count("_acquire_home(") == 1
    assert "_acquire_home(" in inspect.getsource(cli_mod.serve)
