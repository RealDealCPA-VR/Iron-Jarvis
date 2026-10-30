"""v1.412.0 — Windows' own folder picker, ``dialog:pickFolder`` in ``desktop/main.js``.

The user: "when i decide i want to choose a folder a popup modal that is the
generic windows file selector should appear allowing me to navigate all the
drives available to me". ``pickFolder`` opens Electron's
``dialog.showOpenDialog`` with ``openDirectory`` only, parented to the window
that ASKED; the IPC handler is sender-checked like every privileged handler
(an off-origin frame gets nothing and no dialog opens). ``preload.js`` exposes
it as ``window.ironjarvis.dialog.pickFolder`` and ``lib/desktopShell`` reads it.

The function and the handler are LIFTED VERBATIM from main.js and run under
node against stubs (the ``shell:openLogs`` pattern), so this goes red when the
shipped code regresses.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_PRELOAD = _ROOT / "desktop" / "preload.js"
_SHELL_TS = _ROOT / "dashboard" / "lib" / "desktopShell.ts"
_CHANNEL = "dialog:pickFolder"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _function_source() -> str:
    src = _read(_MAIN)
    start = src.index("async function pickFolder(owner, raw)")
    end = src.index("\n}", start) + 2
    return src[start:end]


def _handler_source() -> str:
    src = _read(_MAIN)
    start = src.index(f'ipcMain.handle("{_CHANNEL}"')
    end = src.index("\n  });", start) + len("\n  });")
    return src[start:end]


_HARNESS = """
const registered = {};
const ipcMain = { handle: (ch, fn) => { registered[ch] = fn; } };
const calls = [];
let _answer = () => Promise.resolve({ canceled: false, filePaths: ["D:\\\\work\\\\shop"] });
const dialog = {
  showOpenDialog: (...args) => { calls.push(args); return _answer(); },
};
const windows = new Map();
const BrowserWindow = { fromWebContents: (wc) => windows.get(wc) || null };
let _trusted = true;
function isTrustedDashboardSender(event) { return _trusted && !!event; }

%(fn)s

%(handler)s

const handler = registered["%(channel)s"];
if (typeof handler !== "function") throw new Error("handler not registered");
const wc = { id: 7 };
const win = { name: "popout", isDestroyed: () => false };
windows.set(wc, win);
const sender = { sender: wc, senderFrame: { url: "http://127.0.0.1:8788/terminals" } };

(async () => {
  const out = {};
  // 1. an off-origin frame: nothing, and no dialog opens.
  _trusted = false;
  out.untrusted = await handler(sender, { defaultPath: "C:\\\\" });
  out.calls_untrusted = calls.length;
  _trusted = true;
  // 2. a folder picked: its path; the dialog was the SENDER's window's child.
  out.picked = await handler(sender, { defaultPath: "C:\\\\Users\\\\VR" });
  const [owner, opts] = calls[calls.length - 1];
  out.owner = owner && owner.name;
  out.opts = opts;
  // 3. cancelled.
  _answer = () => Promise.resolve({ canceled: true, filePaths: [] });
  out.cancelled = await handler(sender, {});
  // 4. the dialog failed: still an answer, never a throw.
  _answer = () => Promise.reject(new Error("no dialog"));
  out.failed = await handler(sender, null);
  // 5. a window already gone: the dialog opens unparented.
  _answer = () => Promise.resolve({ canceled: false, filePaths: ["E:\\\\"] });
  win.isDestroyed = () => true;
  out.gone = await handler(sender, { defaultPath: 42 });
  out.gone_args = calls[calls.length - 1].length;
  out.gone_opts = calls[calls.length - 1][0];
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack); process.exit(2); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_pick_folder_handler_behaviour(tmp_path):
    harness = _HARNESS % {"fn": _function_source(), "handler": _handler_source(), "channel": _CHANNEL}
    f = tmp_path / "harness.js"
    f.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    assert got["untrusted"] is None, "an off-origin frame got an answer"
    assert got["calls_untrusted"] == 0, "an off-origin frame opened the folder picker"
    assert got["picked"] == {"path": "D:\\work\\shop"}
    assert got["owner"] == "popout", "the picker was not parented to the window that asked"
    assert got["opts"]["properties"] == ["openDirectory"], "the picker may choose more than one folder"
    assert got["opts"]["defaultPath"] == "C:\\Users\\VR"
    assert got["cancelled"] == {"cancelled": True}
    assert got["failed"] == {"cancelled": True}
    assert got["gone"] == {"path": "E:\\"}
    assert got["gone_args"] == 1, "a destroyed window was used as the parent"
    assert "defaultPath" not in got["gone_opts"], "a non-string start folder was passed on"


def test_the_handler_checks_the_sender_first():
    m = re.search(r'ipcMain\.handle\("' + re.escape(_CHANNEL) + r'", \((\w+), \w+\) => \{(.*?)\n  \}\);',
                  _read(_MAIN), re.S)
    assert m, "dialog:pickFolder is not registered"
    first = m.group(2).strip().splitlines()[0]
    assert first == f"if (!isTrustedDashboardSender({m.group(1)})) return null;"


def test_the_preload_and_the_dashboard_reach_it():
    pre = _read(_PRELOAD)
    assert 'ipcRenderer.invoke("dialog:pickFolder"' in pre
    assert re.search(r"dialog:\s*\{\s*pickFolder:", pre)
    ts = _read(_SHELL_TS)
    assert "export function folderPickerBridge()" in ts and "pickFolder" in ts
