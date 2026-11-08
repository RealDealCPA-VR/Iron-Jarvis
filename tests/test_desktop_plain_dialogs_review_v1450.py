"""AN review of Wave D lane D3, item 39 (v1.450.0): the desktop dialogs.

Driven under node with the REAL lifted functions:

* the startup dialog's raw gate error is in the desktop log BEFORE the dialog
  opens, on BOTH branches (the update-failed branch logged nothing), and the
  dialog itself never carries it;
* Spotlight keeps only a SENTENCE Iron Jarvis wrote for the user: a 500's
  "internal error [err_…]: KeyError: …", a 422's detail list (it arrives as
  "[object Object]"), a stack or JSON is the plain line.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.test_desktop_idle_v1333 import _decl, _lift, _run, requires_node

_START = r"""
const logs = [];
const DAEMON_PORT = 8787;
const STARTUP_TIMEOUT_MS = 90000;
const shown = [];
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
function readLogTail() { return ""; }
function openLogsFolder() {}
function shutdown() {}
let isQuitting = false;
const app = { quit() {}, relaunch() {}, exit() {} };
const shell = { openExternal() {} };
const dialog = {
  showMessageBoxSync(o) { shown.push({ logsSoFar: logs.slice(), detail: o.detail, message: o.message }); return 2; },
};
__DECLS__
__CLASSIFY__
__HANDLE__
const raw = "connect ECONNREFUSED 127.0.0.1:8787 RAW-GATE-ERROR";
handleStartupFailure("Iron Jarvis could not start", "m", null, { label: "daemon", gateError: raw });
logs.length = 0;
handleStartupFailure("Iron Jarvis could not start", "m", { attempts: 2, version: "9.9.9" }, { label: "daemon", gateError: raw });
process.stdout.write(JSON.stringify({ shown }) + "\n");
"""


@requires_node
def test_the_raw_startup_error_is_logged_before_either_dialog_and_never_shown(tmp_path):
    decls = "\n".join(
        _decl(n)
        for n in ("RELEASES_PAGE", "RELEASES_PREVIOUS_HOW", "RELEASES_LATEST_HOW", "DATA_STAYS", "STARTUP_LOG_LINE")
    )
    script = (
        _START.replace("__DECLS__", decls)
        .replace("__CLASSIFY__", _lift("function classifyStartupFailure("))
        .replace("__HANDLE__", _lift("function handleStartupFailure("))
    )
    out = _run(script, tmp_path, "start", )["shown"]
    assert len(out) == 2, out
    for d in out:
        assert any("RAW-GATE-ERROR" in line for line in d["logsSoFar"]), (
            f"the raw error was not in the log when the dialog opened: {d}"
        )
        assert "RAW-GATE-ERROR" not in d["detail"] and "ECONNREFUSED" not in d["detail"], d
        assert "RAW-GATE-ERROR" not in d["message"], d


_SPOT = r"""
__LIFTED__
const raws = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(raws.map((r) => spotlightErrorWords(r))) + "\n");
"""

PLAIN = "The task didn't start. Open the main window and try again."


@requires_node
def test_spotlight_never_passes_developer_text_through(tmp_path):
    raws = [
        "internal error [err_1a2b3c4d]: KeyError: 'agent_type'",
        "[object Object],[object Object]",
        '{"detail": "boom"}',
        "TypeError: Cannot read properties of undefined (reading 'id')",
        "Traceback (most recent call last):\n  File \"x.py\"",
        "x" * 400 + ".",
        "value is not a valid enumeration member",
        # kept: Iron Jarvis's own refusal, in full
        "Emergency stop is on, so work that starts on its own (schedules, goals, rules, phone "
        "tasks, queued tasks, tasks from another computer) cannot start. Turn it off on the "
        "Autonomy page to let it run again.",
    ]
    lifted = _lift("function spotlightErrorWords(")
    f = tmp_path / "spot.js"
    f.write_text(_SPOT.replace("__LIFTED__", lifted), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), json.dumps(raws)], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    words = json.loads(proc.stdout.strip().splitlines()[-1])
    for raw, w in zip(raws[:-1], words[:-1]):
        assert w == PLAIN, (raw, w)
    assert words[-1] == raws[-1]
