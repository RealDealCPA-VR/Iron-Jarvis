"""v1.277.0 — three reaches removed from the sidebar, each verified before it was built.

1. A KEY OPENS THE PANEL. The toolbar icon was the only way to open the side
   panel, and a freshly loaded add-on is not on the toolbar at all. The manifest
   suggests Alt+J; the worker answers the command with ``chrome.sidePanel.open``
   (a command is a user gesture, which that API requires).
2. OPEN JARVIS FOCUSES. Every press was ``chrome.tabs.create`` — one more
   dashboard tab each time. ``background/openpage.ts::focusOrOpen`` is now the
   one opener: a tab matching the site pattern comes to the front (with its
   window), else one is opened. The grant pages (site access, microphone) go
   through the same function.
3. ESC STOPS. ``keyToPress`` answers ``"stop"`` for Escape while a turn runs;
   that pin lives with the v1.264.0 keyboard contract
   (``test_sidebar_first_minute_v1264``), and this file pins the wiring.

House idiom: shipped source lifted verbatim and run under node against a fake
``chrome``; what cannot execute here is pinned against the source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome"
MANIFEST = ADDON / "manifest.json"
WORKER = ADDON / "src" / "background" / "index.ts"
OPENPAGE = ADDON / "src" / "background" / "openpage.ts"
HOSTPERMS = ADDON / "src" / "background" / "hostperms.ts"
PANEL_TS = ADDON / "src" / "sidepanel" / "sidepanel.ts"
PANEL_HTML = ADDON / "src" / "sidepanel" / "sidepanel.html"
README = ADDON / "README.md"
HANDBOOK = ROOT / "docs" / "HANDBOOK.md"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _manifest() -> dict:
    return json.loads(_src(MANIFEST))


# --------------------------------------------------------------------------- #
# 1. Alt+J
# --------------------------------------------------------------------------- #


def test_the_manifest_suggests_one_key_and_the_worker_answers_it():
    commands = _manifest().get("commands")
    assert isinstance(commands, dict) and list(commands) == ["open-panel"], commands
    cmd = commands["open-panel"]
    assert cmd["suggested_key"]["default"] == "Alt+J"
    assert "Iron Jarvis" in cmd["description"]
    # No permission is needed for `commands`; the permission list is pinned
    # elsewhere and must not grow for this.
    assert "commands" not in _manifest()["permissions"]

    worker = _src(WORKER)
    assert 'const PANEL_COMMAND = "open-panel";' in worker
    assert "chrome.commands?.onCommand.addListener(" in worker
    assert "chrome.sidePanel.open({ windowId })" in worker
    # The command's own tab names the window; the last focused window stands in.
    assert "chrome.windows.getLastFocused()" in worker
    # Guarded like the panel-behaviour call above it: a browser without the
    # API must not lose the bridge over a shortcut.
    block = worker[worker.index("const PANEL_COMMAND") : worker.index("chrome.commands?.onCommand")]
    assert "async function openPanelFromCommand" in block


# --------------------------------------------------------------------------- #
# 2. focusOrOpen — lifted and run under node against a fake chrome
# --------------------------------------------------------------------------- #


def _lifted_openpage() -> str:
    src = _src(OPENPAGE)
    start = src.index("export function sitePattern(")
    code = src[start:]
    code = code.replace("export ", "")
    # Types go; bodies stay byte-identical to what the add-on ships.
    code = re.sub(r"\(url: string\): string", "(url)", code)
    code = re.sub(
        r"\(\s*url: string,\s*pattern: string = url,\s*\): Promise<\{ tab_id: number \| null; reused: boolean \}>",
        "(url, pattern = url)",
        code,
    )
    return code


_NODE_HARNESS = """
const calls = [];
function fakeChrome(existing) {
  return {
    tabs: {
      query: async (q) => { calls.push(["query", q]); return existing; },
      update: async (id, u) => { calls.push(["update", id, u]); },
      create: async (c) => { calls.push(["create", c]); return { id: 99 }; },
    },
    windows: { update: async (id, u) => { calls.push(["windows.update", id, u]); } },
  };
}
__CODE__
(async () => {
  const out = {};
  globalThis.chrome = fakeChrome([{ id: 7, windowId: 3 }]);
  out.reused = await focusOrOpen("http://127.0.0.1:8788/computeruse", sitePattern("http://127.0.0.1:8788/computeruse"));
  out.reusedCalls = calls.splice(0);
  globalThis.chrome = fakeChrome([]);
  out.opened = await focusOrOpen("http://127.0.0.1:8788/computeruse");
  out.openedCalls = calls.splice(0);
  out.patterns = [
    sitePattern("http://127.0.0.1:8788/computeruse"),
    sitePattern("chrome-extension://abc/dist/setup.html"),
    sitePattern("not a url"),
  ];
  console.log(JSON.stringify(out));
})();
"""


@requires_node
def test_focus_or_open_focuses_a_matching_tab_and_opens_only_when_there_is_none(tmp_path):
    script = _NODE_HARNESS.replace("__CODE__", _lifted_openpage())
    f = tmp_path / "openpage.mjs"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ}
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    # An open dashboard tab (on ANY of its pages) comes to the front with its window; nothing is created.
    assert out["reused"] == {"tab_id": 7, "reused": True}
    assert out["reusedCalls"] == [
        ["query", {"url": "http://127.0.0.1:8788/*"}],
        ["update", 7, {"active": True}],
        ["windows.update", 3, {"focused": True}],
    ]
    # No such tab: one is opened, active.
    assert out["opened"] == {"tab_id": 99, "reused": False}
    assert out["openedCalls"] == [
        ["query", {"url": "http://127.0.0.1:8788/computeruse"}],
        ["create", {"url": "http://127.0.0.1:8788/computeruse", "active": True}],
    ]
    assert out["patterns"] == [
        "http://127.0.0.1:8788/*",
        "chrome-extension://abc/*",
        "not a url",
    ]


def test_open_jarvis_and_the_grant_pages_share_the_one_opener():
    worker = _src(WORKER)
    branch = worker[worker.index('case "open_jarvis"') : worker.index('case "toggle_connection"')]
    assert "focusOrOpen(JARVIS_URL, sitePattern(JARVIS_URL))" in branch
    assert "chrome.tabs.create" not in branch, "Open Jarvis must not stack tabs again"
    assert 'import { focusOrOpen, sitePattern } from "./openpage";' in worker
    host = _src(HOSTPERMS)
    assert 'import { focusOrOpen } from "./openpage";' in host
    body = host[host.index("async function openAddonPage") :]
    body = body[: body.index("\n}\n")]
    assert "focusOrOpen(chrome.runtime.getURL(page))" in body
    assert "chrome.tabs.create" not in body


# --------------------------------------------------------------------------- #
# 3. Esc stops — the wiring (the key table itself is pinned with v1.264.0)
# --------------------------------------------------------------------------- #


def test_escape_is_wired_to_the_stop_button_and_the_tooltip_says_so():
    ts = _src(PANEL_TS)
    fn = ts[ts.index("export function keyToPress(") :]
    fn = fn[: fn.index("\n}\n")]
    assert 'if (key === "Escape") return running ? "stop" : null;' in fn
    assert '(press === "send" ? el.send : press === "steer" ? el.steer : el.stop)?.click();' in ts
    assert "Esc stops" in _src(PANEL_HTML)


# --------------------------------------------------------------------------- #
# 4. The docs name the key
# --------------------------------------------------------------------------- #


def test_the_readme_and_the_handbook_name_the_key_and_the_shortcuts_page():
    readme = _src(README)
    assert "**Alt+J**" in readme
    assert "chrome://extensions/shortcuts" in readme and "edge://extensions/shortcuts" in readme
    assert "**Esc** stops" in readme
    hb = _src(HANDBOOK)
    assert "**Fewer reaches (v1.277.0).**" in hb
    assert "**Alt+J**" in hb and "**Esc**" in hb
