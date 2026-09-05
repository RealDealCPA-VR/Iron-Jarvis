"""v1.229.0 (audit Wave 3, D8/OBS5) — "Open logs folder" in ``desktop/main.js``.

The logs path used to appear only inside a crash toast. Now ``openLogsFolder``
opens ``userData/logs`` in the OS file manager, reachable from the tray and
from Settings → Maintenance through the sender-checked IPC ``shell:openLogs``
that ``preload.js`` exposes as ``window.ironjarvis.shell.openLogs``.

The function and the handler are LIFTED VERBATIM from main.js and run under
node against stubs (the clipboard:readImage pattern), so this goes red if the
shipped code regresses. The rest pin the call sites: a handler the renderer
cannot reach is not a bridge, a function the tray does not call is not a menu
item.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[1] / "desktop"
_MAIN = _DESKTOP / "main.js"
_PRELOAD = _DESKTOP / "preload.js"
_CHANNEL = "shell:openLogs"


def _function_source() -> str:
    src = _MAIN.read_text(encoding="utf-8")
    start = src.index("function openLogsFolder()")
    end = src.index("\n}", start) + 2
    return src[start:end]


def _handler_source() -> str:
    src = _MAIN.read_text(encoding="utf-8")
    start = src.index(f'ipcMain.handle("{_CHANNEL}"')
    end = src.index("\n  });", start) + len("\n  });")
    return src[start:end]


_HARNESS = """
const path = require("node:path");
const registered = {};
const ipcMain = { handle: (ch, fn) => { registered[ch] = fn; } };
const mkdirs = [];
const fs = { mkdirSync: (p, o) => { mkdirs.push([p, o && o.recursive]); } };
let _openResult = () => Promise.resolve("");
const opened = [];
const shell = { openPath: (p) => { opened.push(p); return _openResult(p); } };
let userDataDir = "C:\\\\ud\\\\Iron Jarvis";
let _trusted = true;
function isTrustedDashboardSender(event) { return _trusted && !!event; }

%(fn)s

%(handler)s

const handler = registered["%(channel)s"];
if (typeof handler !== "function") throw new Error("handler not registered");
const sender = { senderFrame: { url: "http://localhost:8788/settings" } };

(async () => {
  const out = {};
  // 1. an off-origin frame gets nothing and opens nothing.
  _trusted = false;
  out.untrusted = await handler(sender);
  out.opened_while_untrusted = opened.length;
  _trusted = true;
  // 2. the real path: folder created, opened, path reported.
  out.ok = await handler(sender);
  out.mkdir = mkdirs[mkdirs.length - 1];
  // 3. openPath resolves an error STRING (Electron's contract) -> reported.
  _openResult = () => Promise.resolve("No application is associated");
  out.err_string = await handler(sender);
  // 4. openPath rejects -> reported, never thrown.
  _openResult = () => Promise.reject(new Error("exploded"));
  out.rejects = await handler(sender);
  // 5. before app-ready userDataDir is null: still a relative "logs", no throw.
  userDataDir = null;
  _openResult = () => Promise.resolve("");
  out.no_userdata = await handler(sender);
  out.opened = opened;
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack); process.exit(2); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_open_logs_handler_behaviour(tmp_path):
    harness = _HARNESS % {
        "fn": _function_source(),
        "handler": _handler_source(),
        "channel": _CHANNEL,
    }
    f = tmp_path / "harness.js"
    f.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    assert got["untrusted"] is None, "an off-origin frame launched the file manager"
    assert got["opened_while_untrusted"] == 0

    logs = "C:\\ud\\Iron Jarvis\\logs"
    assert got["ok"] == {"ok": True, "path": logs}
    assert got["mkdir"] == [logs, True]
    assert got["err_string"] == {"ok": False, "path": logs, "error": "No application is associated"}
    assert got["rejects"] == {"ok": False, "path": logs, "error": "exploded"}
    assert got["no_userdata"]["ok"] is True and got["no_userdata"]["path"] == "logs"
    assert got["opened"] == [logs, logs, logs, "logs"]


def test_open_logs_is_sender_checked_first():
    src = _MAIN.read_text(encoding="utf-8")
    m = re.search(
        r'ipcMain\.handle\("' + _CHANNEL + r'", \((\w+)\) => \{(.*?)\n  \}\);', src, re.S
    )
    assert m, f"no handler for {_CHANNEL}"
    arg, body = m.group(1), m.group(2)
    assert arg != "_e", f"{_CHANNEL} ignores its event"
    assert f"isTrustedDashboardSender({arg})" in body
    assert body.index("isTrustedDashboardSender") < body.index("openLogsFolder()")


def test_tray_has_an_open_logs_item_that_calls_the_same_function():
    src = _MAIN.read_text(encoding="utf-8")
    start = src.index("function buildTrayContextMenu()")
    end = src.index("\n}", start)
    tray = src[start:end]
    assert re.search(r'label: "Open logs folder", click: \(\) => openLogsFolder\(\)', tray), tray


def test_preload_exposes_open_logs_under_shell():
    src = _PRELOAD.read_text(encoding="utf-8")
    start = src.index("shell: {")
    end = src.index("\n  },", start)  # the object's closing brace, not a "}," in a comment
    assert f'openLogs: () => ipcRenderer.invoke("{_CHANNEL}")' in src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_main_and_preload_still_parse():
    for f in (_MAIN, _PRELOAD):
        proc = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
