"""v1.422.0 — the desktop and the CODE HOST (``iron_jarvis/codehost.py``).

The host is never in the daemon's tree, so ``killChild``'s ``taskkill /T`` (a
restart, the watchdog) leaves it and Build › Code's tools running for the next
daemon. A QUIT and an update must still end it:

* the daemon's tidy stop tells it (``POST /shutdown`` with no body is a quit
  — every older desktop's too);
* ``quitCodeHost`` covers a daemon that could not (wedged at Quit, killed
  before the update): the desktop reads the host's record and says ``quit``
  itself — pinned here by running the SHIPPED function under node against a
  REAL host;
* the update path and Quit call it; the installer's own ``taskkill /F /T /IM
  ironjarvis.exe`` (installer.nsh) ends a frozen host that is left;
* a RESTART-type sweep (a wedged adopted daemon) spares the host's pid.

House idiom (``test_update_channel_v1260``): the shipped source is LIFTED out
of main.js verbatim and run under node.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis import codehost as CH
from iron_jarvis.hosted import hostlink as HL
from tests._code_host_helpers_v14220 import alive, host_session

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


@pytest.fixture
def hosts(monkeypatch):
    yield from host_session(monkeypatch)


def _src() -> str:
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str) -> str:
    src = _src()
    start = src.index(start_marker)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _decl(name: str) -> str:
    import re

    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _node(script: str, tmp_path: Path) -> dict:
    f = tmp_path / "lifted.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _lifted() -> str:
    return "\n".join([
        'const fs = require("fs"); const path = require("path"); const net = require("net");',
        _decl("CODE_HOST_RECORD"),
        _lift("function codeHostRecord("),
        _lift("function codeHostQuitFrames("),
        _lift("function quitCodeHost("),
        _lift("function sweepArgs("),
    ])


@requires_node
def test_the_sweep_spares_only_the_code_hosts_pid(tmp_path):
    out = _node(_lifted() + "\nconsole.log(JSON.stringify({a: sweepArgs(0), b: sweepArgs(4242), "
                "c: sweepArgs(undefined), d: sweepArgs(-1)}));", tmp_path)
    plain = ["/F", "/T", "/IM", "ironjarvis.exe"]
    assert out["a"] == plain and out["c"] == plain and out["d"] == plain
    assert out["b"] == plain + ["/FI", "PID ne 4242"]


@requires_node
def test_the_desktop_reads_the_hosts_record_and_refuses_a_broken_one(tmp_path):
    home = tmp_path / "home"
    (home / "hosted").mkdir(parents=True)
    rec = home / "hosted" / "codehost.json"
    cases = {
        "good": {"pid": 12, "port": 34, "token": "t" * 64, "version": "x"},
        "no_token": {"pid": 12, "port": 34},
        "bad_port": {"pid": 12, "port": "x", "token": "t"},
    }
    got = {}
    for name, body in cases.items():
        rec.write_text(json.dumps(body), encoding="utf-8")
        got[name] = _node(_lifted() + f"\nconsole.log(JSON.stringify(codeHostRecord({json.dumps(str(home))})));",
                          tmp_path)
    assert got["good"] == {"pid": 12, "port": 34, "token": "t" * 64}
    assert got["no_token"] is None and got["bad_port"] is None


@requires_node
@pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows")
@pytest.mark.timeout(120)
def test_quit_from_the_desktop_stops_a_real_host_and_its_tool(hosts, tmp_path):
    import asyncio

    home = hosts.home(tmp_path / "home")
    tool_py = tmp_path / "tool.py"
    tool_py.write_text("import sys\nfor line in sys.stdin:\n    pass\n", encoding="utf-8")
    proc = HL.RemoteProcess([sys.executable, str(tool_py)], env=dict(os.environ), cwd=str(tmp_path), name="t",
                            home=home, sid="s1")
    proc.start(asyncio.new_event_loop(), lambda o: None, lambda c: None)
    hosts.note()
    hosts.pids.add(proc.pid)
    host = CH.read_record(home)["pid"]
    out = _node(_lifted() + f"\nquitCodeHost({json.dumps(str(home))}, 15000).then((v) => "
                "console.log(JSON.stringify({answered: v})));", tmp_path)
    assert out == {"answered": True}
    end = time.monotonic() + 20
    while time.monotonic() < end and (alive(host) or alive(proc.pid)):
        time.sleep(0.2)
    assert not alive(host) and not alive(proc.pid), "Quit leaves nothing of Build › Code running"
    said = CH.log_path(home).read_text(encoding="utf-8")
    assert "told to quit" in said and "stopped (graceful)" in said


@requires_node
def test_quit_with_no_host_answers_at_once(tmp_path):
    t0 = time.monotonic()
    out = _node(_lifted() + f"\nquitCodeHost({json.dumps(str(tmp_path))}, 15000).then((v) => "
                "console.log(JSON.stringify({answered: v})));", tmp_path)
    assert out == {"answered": False} and time.monotonic() - t0 < 10


def test_quit_and_the_update_tell_the_host_and_only_the_watchdog_sweep_spares_it():
    src = _src()
    quit_ = src[src.index('app.on("before-quit"'):src.index('app.on("will-quit"')]
    assert "quitCodeHost(codeHostHome()" in quit_, "Quit tells the code host"
    update = src[src.index("async function applyPendingUpdate()"):]
    update = update[:update.index("\n}\n")]
    assert "await quitCodeHost(codeHostHome()" in update, "an update tells the code host first"
    assert update.index("await quitCodeHost") < update.index("quitAndInstall"), "before the installer starts"
    # v1.433.0 review (moved on purpose): two RESTART-type sweeps spare the
    # host — the watchdog's for a wedged adopted daemon, and Settings' tidy
    # restart of an adopted daemon that keeps its port. Never Quit's or an
    # update's.
    spare = "sweepOrphanDaemons({ spareCodeHost: true })"
    assert src.count(spare) == 2, "only the restart-type sweeps spare it"
    assert spare not in quit_ and spare not in update, "a Quit or an update spared the code host"
    for fn in ("function daemonWatchdogTick(", "function restartAdoptedDaemonTidy("):
        body = src[src.index(fn):]
        assert spare in body[: body.index("\n}\n")], fn
    nsh = (_ROOT / "desktop" / "installer.nsh").read_text(encoding="utf-8")
    assert "taskkill /F /T /IM ironjarvis.exe" in nsh, "the installer still ends a frozen host left behind"


def test_the_desktops_hello_can_never_take_the_daemons_place():
    """The desktop says protocol 0: a host takes only ``quit`` from it and
    never drops the daemon's own connection for it."""
    assert '"protocol": 0' not in _src()  # (it is JS: protocol: 0)
    assert "protocol: 0" in _lift("function codeHostQuitFrames(")
    assert CH.PROTOCOL != 0
