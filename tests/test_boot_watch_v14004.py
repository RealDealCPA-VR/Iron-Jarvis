"""v1.400.4 — a start that does not finish says where, and the app retries it once.

Twice on 2026-10-27 the first daemon start after an update hung inside its own
startup (uvicorn's "Waiting for application startup", then nothing), the
desktop's 90 s gate showed "did not answer … Press Retry", and the user's
Retry got a fresh start that was ready in 2 s. The log could not say which
step hung: the steps log only when they FAIL.

* ``daemon/boot_watch.BootWatch`` reports, at 30/60/120 s, the step the boot is
  in and where the boot thread waits (``module:function:line``, never a path),
  and goes quiet the moment boot finishes.
* ``desktop/main.js`` restarts a daemon that is STILL RUNNING when the gate
  runs out ONCE by itself (``shouldRetryHungDaemon``) before any dialog; a
  daemon that exited keeps going straight to the dialog with its own cause.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import boot_watch as bw

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "desktop" / "main.js"
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _wait_until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# --------------------------------------------------------------------------- #
# The watch
# --------------------------------------------------------------------------- #


def test_a_stuck_start_names_its_step_and_where_the_boot_thread_waits():
    said: list[str] = []
    release = threading.Event()

    def stuck_step_body():
        release.wait(10)

    def boot():
        watch = bw.BootWatch(stall_after_s=(0.2, 0.4), report=said.append).start()
        watch.step("rehydrate_terminals")
        stuck_step_body()
        watch.done()

    t = threading.Thread(target=boot, name="lifespan")
    t.start()
    try:
        assert _wait_until(lambda: len(said) >= 2), said
    finally:
        release.set()
        t.join(10)
    first = said[0]
    assert first.startswith("startup has not finished after 0 s") or "after" in first
    assert "still in step rehydrate_terminals" in first
    # Where the boot thread waits, by module and function.
    assert "boot thread:" in first and "stuck_step_body" in first
    assert f"{__name__}:stuck_step_body:" in first


def test_a_start_that_finishes_says_nothing():
    said: list[str] = []
    watch = bw.BootWatch(stall_after_s=(0.3,), report=said.append).start()
    watch.step("platform")
    watch.done()
    time.sleep(0.6)
    assert said == [] and watch.reports == 0


def test_the_report_names_no_file_path():
    said: list[str] = []
    release = threading.Event()

    def boot():
        watch = bw.BootWatch(stall_after_s=(0.1,), report=said.append).start()
        watch.step("scheduler")
        release.wait(10)
        watch.done()

    t = threading.Thread(target=boot)
    t.start()
    try:
        assert _wait_until(lambda: said), said
    finally:
        release.set()
        t.join(10)
    text = said[0]
    # A module NAME may hold ".python" (`_pytest.python`); a file name ends ".py".
    assert "\\" not in text and not re.search(r"\.pyw?\b", text) and str(ROOT) not in text, text
    assert not re.search(r"[A-Za-z]:/", text), text


def test_a_report_that_raises_never_breaks_the_boot():
    def boom(_text):
        raise RuntimeError("logging broke")

    release = threading.Event()
    done = threading.Event()

    def boot():
        watch = bw.BootWatch(stall_after_s=(0.1, 0.2), report=boom).start()
        watch.step("x")
        release.wait(0.5)
        watch.done()
        done.set()

    t = threading.Thread(target=boot)
    t.start()
    t.join(10)
    assert done.is_set()


# --------------------------------------------------------------------------- #
# Wired into the daemon's real startup
# --------------------------------------------------------------------------- #


def test_the_daemons_startup_reports_the_step_that_is_slow(tmp_path, monkeypatch):
    """A step held past the threshold is named by the REAL lifespan's watch,
    and the watch stops when boot finishes (one report, not more)."""
    from iron_jarvis.daemon.app import create_app

    said: list[str] = []
    monkeypatch.setitem(bw.BootWatch.__init__.__kwdefaults__, "stall_after_s", (0.3, 1.5))
    monkeypatch.setitem(bw.BootWatch.__init__.__kwdefaults__, "report", said.append)
    app = create_app(str(tmp_path))
    terminals = app.state.d.platform.terminals
    real = terminals.rehydrate

    def slow_rehydrate(*a, **kw):
        time.sleep(0.8)
        return real(*a, **kw)

    monkeypatch.setattr(terminals, "rehydrate", slow_rehydrate)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        # Past the second threshold: a watch that boot did not stop would
        # report again here.
        time.sleep(1.8)
    assert len(said) == 1, said
    assert "still in step rehydrate_terminals" in said[0]
    assert "slow_rehydrate" in said[0]


# --------------------------------------------------------------------------- #
# The desktop: one automatic restart for a daemon that is running but silent
# --------------------------------------------------------------------------- #


def _main_src() -> str:
    return MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fn(src: str, name: str) -> str:
    m = re.search(rf"^function {name}\(", src, re.M)
    assert m, f"main.js has no function {name}"
    depth = 0
    for i in range(src.index("{", m.start()), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start(): i + 1]
    raise AssertionError("unbalanced")


@requires_node
def test_only_a_daemon_still_running_is_retried_and_only_once(tmp_path):
    fn = _fn(_main_src(), "shouldRetryHungDaemon")
    cases = [
        ["running", {"exitCode": None, "signalCode": None}, False, True],
        ["running, already retried", {"exitCode": None, "signalCode": None}, True, False],
        ["exited with an error", {"exitCode": 1, "signalCode": None}, False, False],
        ["attached to another copy", {"exitCode": 75, "signalCode": None}, False, False],
        ["killed by a signal", {"exitCode": None, "signalCode": "SIGTERM"}, False, False],
        ["no process at all", None, False, False],
    ]
    script = fn + "\nconst cases = " + json.dumps(cases) + ";\n" + (
        "console.log(JSON.stringify(cases.map(([w, p, r]) => shouldRetryHungDaemon(p, r))));\n"
    )
    f = tmp_path / "retry.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8",
                          timeout=120, stdin=subprocess.DEVNULL, env={**os.environ})
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got == [want for *_rest, want in cases], list(zip([c[0] for c in cases], got))


def test_the_boot_restarts_a_hung_daemon_before_the_dialog_and_gates_again():
    src = _main_src()
    gate = src.index("waitForDaemon(STARTUP_TIMEOUT_MS, GATE_POLL_MS),")
    retry = src.index("shouldRetryHungDaemon(daemonProc, bootDaemonRetried)", gate)
    restart = src.index("restartDaemonFromShell(", retry)
    regate = src.index("daemonGate = await waitForDaemon(STARTUP_TIMEOUT_MS, GATE_POLL_MS)", restart)
    dialog = src.index('handleStartupFailure(\n      "Iron Jarvis did not start"', gate)
    assert gate < retry < restart < regate < dialog
    # The retry runs only when the daemon's gate FAILED, and only for a hung one.
    assert 'if (daemonGate.status === "rejected" && shouldRetryHungDaemon(daemonProc, bootDaemonRetried)) {' in src
    # Once per launch: the flag is set before the restart.
    assert src.index("bootDaemonRetried = true;", retry) < restart
    # The restart says why in the daemon's own log; the window's button keeps its words.
    body = _fn(src, "restartDaemonFromShell")
    assert '${why || "on request from the window"}' in body
