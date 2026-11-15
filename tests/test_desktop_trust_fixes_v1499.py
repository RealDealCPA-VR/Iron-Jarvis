"""v1.499.x review — three desktop trust fixes in ``desktop/main.js``.

1. The bearer token is added to a daemon request ONLY when the dashboard made
   it. Chat › Code's Browser pane shows any local dev page in an iframe in the
   same Electron session, and the daemon's CORS trusts every loopback origin,
   so injecting the header on every daemon-bound request gave a previewed
   page's script the whole daemon. ``shouldInjectAuth`` decides.
2. The orphan sweep ends only THIS app's own daemon: the processes named
   ironjarvis.exe whose executable sits in the app's own daemon folder, each
   by pid — never ``/IM ironjarvis.exe`` (a dev daemon, another install). A
   listing that fails falls back to the daemon this app started, never to
   everything.
3. A renderer console line (a failed event socket prints
   ``ws://127.0.0.1:8787/events?token=<hex>``) and a desktop log line are
   redacted before they reach logs/renderer.log or logs/desktop.log.

House idiom (``test_code_host_desktop_v14220.py``): the shipped functions are
LIFTED out of main.js verbatim and run under node against stubs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

# A made-up value: hex-shaped like the install token, but obviously not one.
FAKE_TOKEN = "ab" * 32


def _src() -> str:
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str) -> str:
    src = _src()
    start = src.index(start_marker)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _const(name: str) -> str:
    src = _src()
    start = src.index(f"const {name} =")
    return src[start:src.index(";\n", start) + 2]


def _node(script: str, tmp_path: Path):
    f = tmp_path / "lifted.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- 1. the token goes only with the dashboard's own requests ---------------

_DASH = 'const DASHBOARD_ORIGINS = new Set(["http://localhost:8788", "http://127.0.0.1:8788"]);'


def _inject_cases(tmp_path: Path) -> dict:
    cases = {
        "dash_origin_localhost": {"url": "http://127.0.0.1:8787/sessions",
                                  "requestHeaders": {"Origin": "http://localhost:8788"}},
        "dash_origin_127": {"url": "http://localhost:8787/sessions",
                            "requestHeaders": {"origin": "http://127.0.0.1:8788"}},
        "preview_origin": {"url": "http://127.0.0.1:8787/terminals",
                           "requestHeaders": {"Origin": "http://localhost:5173"}},
        "preview_origin_with_dash_referrer": {"url": "http://127.0.0.1:8787/terminals",
                                              "requestHeaders": {"Origin": "http://localhost:5173"},
                                              "referrer": "http://localhost:8788/"},
        "null_origin": {"url": "http://127.0.0.1:8787/x", "requestHeaders": {"Origin": "null"}},
        "other_local_port": {"url": "http://127.0.0.1:5173/api",
                             "requestHeaders": {"Origin": "http://localhost:8788"}},
        "dashboard_port_itself": {"url": "http://localhost:8788/_next/x.js",
                                  "requestHeaders": {}, "referrer": "http://localhost:8788/"},
        "no_origin_dash_referrer": {"url": "http://127.0.0.1:8787/documents/file?path=a",
                                    "requestHeaders": {}, "referrer": "http://localhost:8788/chat"},
        "no_origin_foreign_referrer": {"url": "http://127.0.0.1:8787/documents/file?path=a",
                                       "requestHeaders": {}, "referrer": "http://localhost:5173/"},
        "no_origin_dash_referrer_foreign_frame": {"url": "http://127.0.0.1:8787/x", "requestHeaders": {},
                                                  "referrer": "http://localhost:8788/",
                                                  "frameUrl": "http://localhost:5173/"},
        "no_origin_dash_referrer_blank_frame": {"url": "http://127.0.0.1:8787/x", "requestHeaders": {},
                                                "referrer": "http://localhost:8788/",
                                                "frameUrl": "about:blank"},
        "no_origin_no_referrer_dash_frame": {"url": "http://127.0.0.1:8787/x", "requestHeaders": {},
                                             "referrer": "", "frameUrl": "http://localhost:8788/chat"},
        "no_origin_no_referrer_no_frame": {"url": "http://127.0.0.1:8787/x", "requestHeaders": {}},
        "userinfo_trick": {"url": "http://127.0.0.1:8787/x",
                           "requestHeaders": {"Origin": "http://localhost:8788@evil.example"}},
        "already_has_auth": {"url": "http://127.0.0.1:8787/x",
                             "requestHeaders": {"Origin": "http://localhost:8788", "authorization": "Bearer x"}},
        "daemon_lookalike_host": {"url": "http://127.0.0.1.evil.example:8787/x",
                                  "requestHeaders": {"Origin": "http://localhost:8788"}},
    }
    script = "\n".join([
        _DASH,
        _lift("function shouldInjectAuth("),
        f"const cases = {json.dumps(cases)};",
        "const out = {}; for (const k of Object.keys(cases)) out[k] = shouldInjectAuth(cases[k], DASHBOARD_ORIGINS, 8787);",
        "console.log(JSON.stringify(out));",
    ])
    return _node(script, tmp_path)


@requires_node
def test_the_token_goes_only_with_requests_the_dashboard_made(tmp_path):
    got = _inject_cases(tmp_path)
    yes = {"dash_origin_localhost", "dash_origin_127", "no_origin_dash_referrer",
           "no_origin_dash_referrer_blank_frame", "no_origin_no_referrer_dash_frame"}
    for name, verdict in got.items():
        assert verdict is (name in yes), f"{name}: {verdict}"


def test_the_header_hook_asks_shouldInjectAuth_and_nothing_else():
    body = _lift("function installAuthHeaderInjection(")
    assert "shouldInjectAuth(" in body and "DASHBOARD_ORIGINS" in body
    # the old rule: the URL alone decided, whatever page asked
    assert "isDaemon &&" not in body


# --- 2. the sweep ends only this app's own daemon --------------------------

def _sweep_lifted() -> str:
    return "\n".join([
        'const path = require("path");',
        _lift("function parseProcessRows("),
        _lift("function pickOwnDaemonPids("),
        _lift("function sweepArgs("),
    ])


@requires_node
def test_the_sweep_kills_by_pid_never_by_image_name(tmp_path):
    out = _node(_sweep_lifted() + "\nconsole.log(JSON.stringify([sweepArgs(4242), sweepArgs(7)]));", tmp_path)
    assert out == [["/F", "/T", "/PID", "4242"], ["/F", "/T", "/PID", "7"]]
    assert '"/IM"' not in _src(), "a taskkill by image name is back in main.js"


@requires_node
def test_only_processes_in_the_apps_own_daemon_folder_are_picked(tmp_path):
    own = r"C:\Users\x\AppData\Local\Programs\Iron Jarvis\resources\daemon"
    rows = [
        {"ProcessId": 10, "ExecutablePath": own + r"\ironjarvis.exe"},
        {"ProcessId": 11, "ExecutablePath": own.upper() + r"\IRONJARVIS.EXE"},  # case
        {"ProcessId": 12, "ExecutablePath": own.replace("\\", "/") + "/ironjarvis.exe"},  # slashes
        {"ProcessId": 20, "ExecutablePath": r"C:\Users\x\Projects\Iron-Jarvis\.venv\Scripts\ironjarvis.exe"},
        {"ProcessId": 21, "ExecutablePath": r"D:\Other Install\resources\daemon\ironjarvis.exe"},
        {"ProcessId": 22, "ExecutablePath": own + r"-old\ironjarvis.exe"},  # a sibling folder, not inside
        {"ProcessId": 23, "ExecutablePath": None},  # another user's / elevated: unknown, left alone
        {"ProcessId": 24, "ExecutablePath": own + r"\..\..\evil\ironjarvis.exe"},
        {"ProcessId": 4242, "ExecutablePath": own + r"\ironjarvis.exe"},  # the spared code host
    ]
    script = _sweep_lifted() + "\n" + "\n".join([
        f"const rows = parseProcessRows({json.dumps(json.dumps(rows))});",
        f"const one = parseProcessRows({json.dumps(json.dumps(rows[0]))});",
        f"const own = {json.dumps(own)};",
        "console.log(JSON.stringify({",
        "  spared: pickOwnDaemonPids(rows, [own], 4242),",
        "  all: pickOwnDaemonPids(rows, [own], 0),",
        "  single: pickOwnDaemonPids(one, [own], 0),",
        "  none: pickOwnDaemonPids(rows, [], 0),",
        "  empty: parseProcessRows(''), blank: parseProcessRows('  \\r\\n'),",
        "  garbage: parseProcessRows('Get-CimInstance : Access denied'),",
        "}));",
    ])
    out = _node(script, tmp_path)
    assert out["spared"] == [10, 11, 12]
    assert out["all"] == [10, 11, 12, 4242]
    assert out["single"] == [10], "PowerShell prints ONE process as an object, not a list"
    assert out["none"] == []
    assert out["empty"] == [] and out["blank"] == []
    assert out["garbage"] is None, "an unreadable listing is a FAILED listing, not an empty one"


@requires_node
def test_the_sweep_lists_kills_own_pids_and_falls_back_to_its_own_child(tmp_path):
    def run(listing, opts=""):
        base = "\n".join([
            'const path = require("path");',
            'Object.defineProperty(process, "platform", { value: "win32" });',
            "const calls = []; const logs = [];",
            f"const LISTING = {json.dumps(listing)};",
            "function spawnSync(cmd, args, o) { calls.push({cmd, args, timeout: (o && o.timeout) || 0, hide: !!(o && o.windowsHide)});",
            "  if (cmd === 'taskkill') return { status: 0 };",
            "  if (LISTING.throws) throw new Error('spawn EPERM');",
            "  return { status: LISTING.status, stdout: LISTING.stdout, error: LISTING.error ? new Error('ETIMEDOUT') : undefined }; }",
            "function desktopLog(...a) { logs.push(a.join(' ')); }",
            "function codeHostHome() { return 'home'; }",
            "function codeHostRecord() { return { pid: 4242, port: 1, token: 't' }; }",
            "function ownDaemonDirs() { return ['C:\\\\App\\\\resources\\\\daemon']; }",
            "let daemonProc = { pid: 777, exitCode: null, signalCode: null };",
            _const("SWEEP_LIST_TIMEOUT_MS"),
            _const("SWEEP_LIST_PS"),
            _lift("function parseProcessRows("),
            _lift("function pickOwnDaemonPids("),
            _lift("function sweepArgs("),
            _lift("function knownDaemonPids("),
            _lift("function listDaemonProcesses("),
            _lift("function sweepOrphanDaemons("),
            f"sweepOrphanDaemons({opts});",
            "console.log(JSON.stringify({calls, logs}));",
        ])
        return _node(base, tmp_path)

    rows = [
        {"ProcessId": 10, "ExecutablePath": r"C:\App\resources\daemon\ironjarvis.exe"},
        {"ProcessId": 20, "ExecutablePath": r"C:\dev\.venv\Scripts\ironjarvis.exe"},
        {"ProcessId": 4242, "ExecutablePath": r"C:\App\resources\daemon\ironjarvis.exe"},
    ]
    ok = run({"status": 0, "stdout": json.dumps(rows)})
    listing, *kills = ok["calls"]
    assert listing["cmd"].lower().startswith("powershell")
    assert listing["hide"] and 0 < listing["timeout"] <= 15000, "the listing is bounded and hidden"
    assert [k["args"] for k in kills] == [["/F", "/T", "/PID", "10"], ["/F", "/T", "/PID", "4242"]]

    spared = run({"status": 0, "stdout": json.dumps(rows)}, "{ spareCodeHost: true }")
    assert [k["args"] for k in spared["calls"][1:]] == [["/F", "/T", "/PID", "10"]]

    for failed in ({"status": 1, "stdout": ""}, {"error": True, "status": None, "stdout": ""},
                   {"throws": True}, {"status": 0, "stdout": "garbage"}):
        fb = run(failed)
        kills = [c["args"] for c in fb["calls"] if c["cmd"] == "taskkill"]
        assert kills == [["/F", "/T", "/PID", "777"]], (failed, kills)
        assert any("could not list" in line for line in fb["logs"]), fb["logs"]

    gone = run({"status": 0, "stdout": "[]"})
    assert [c for c in gone["calls"] if c["cmd"] == "taskkill"] == [], "nothing of ours is running: kill nothing"


def test_the_apps_own_daemon_folder_is_the_resources_daemon_folder():
    body = _lift("function ownDaemonDirs(")
    assert "path.dirname(DAEMON_EXE)" in body
    # a dev run's own daemon is its own checkout's venv; the packaged app never adds it
    assert "IS_PACKAGED" in body and '".venv"' in body


@requires_node
@pytest.mark.skipif(sys.platform != "win32", reason="the listing is Windows' own")
def test_the_listing_runs_for_real_and_reads_back(tmp_path):
    """READ-ONLY: runs the shipped listing (Get-CimInstance) on this machine and
    parses it; nothing is killed."""
    script = "\n".join([
        'const path = require("path"); const { spawnSync } = require("child_process");',
        _const("SWEEP_LIST_TIMEOUT_MS"),
        _const("SWEEP_LIST_PS"),
        _lift("function parseProcessRows("),
        _lift("function listDaemonProcesses("),
        "const rows = listDaemonProcesses();",
        "console.log(JSON.stringify({ok: Array.isArray(rows), shape: (rows || []).every(r => 'ProcessId' in r)}));",
    ])
    out = _node(script, tmp_path)
    assert out == {"ok": True, "shape": True}


# --- 3. logs never carry the token ------------------------------------------

@requires_node
def test_log_lines_are_redacted(tmp_path):
    lines = {
        "ws": f"WebSocket connection to 'ws://127.0.0.1:8787/events?token={FAKE_TOKEN}' failed: ",
        "amp": f"GET http://127.0.0.1:8787/documents/file?path=a.pdf&TOKEN={FAKE_TOKEN} 401",
        "bearer": f"headers: Authorization: Bearer {FAKE_TOKEN}, next",
        "raw": f"something printed the token {FAKE_TOKEN} bare",
        "plain": "nothing secret here: a token of thanks ?tokens=1",
    }
    script = "\n".join([
        _lift("function redactSecrets("),
        f"const lines = {json.dumps(lines)};",
        "const out = {}; for (const k of Object.keys(lines)) out[k] = redactSecrets(lines[k], "
        f"[{json.dumps(FAKE_TOKEN)}, '', null, 'short']);",
        "out.noSecrets = redactSecrets(lines.ws);",
        "console.log(JSON.stringify(out));",
    ])
    out = _node(script, tmp_path)
    for k, v in out.items():
        assert FAKE_TOKEN not in v, (k, v)
    assert out["ws"] == "WebSocket connection to 'ws://127.0.0.1:8787/events?token=***' failed: "
    assert out["amp"].endswith("&TOKEN=*** 401")
    assert out["bearer"] == "headers: Authorization: Bearer ***, next"
    assert out["plain"] == lines["plain"]
    assert out["noSecrets"] == out["ws"]


def test_both_log_writers_redact():
    for fn in ("function rendererLog(", "function desktopLog("):
        body = _lift(fn)
        assert "redactSecrets(" in body, fn
        assert re.search(r"redactSecrets\([^)]*authToken", body), f"{fn} does not pass the live token"
