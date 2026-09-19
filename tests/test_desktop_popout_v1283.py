"""v1.283.0 — a module pops out into its own window, on another screen when there is one.

The desktop shell had ONE dashboard window. A person with two or three
screens could not put Chat on one and Build on another. Now `desktop/main.js`
keeps one BrowserWindow per dashboard route (`openPopout`), placed by
`windowState.popoutPlacement` — the saved rectangle when it is still on a
connected display, else CENTRED ON THE OTHER SCREEN when the desk has one,
else cascaded off the main window — and remembered per route in
`popout-windows.json`. The preload hands the renderer `ironjarvis.popout`
(`isPopout`, `path`, `open/list/focus/close`), and the IPC handlers are
sender-checked like every privileged one.

House idiom (``test_desktop_reliability_v1249.py``): shipped source lifted
verbatim and run under node; what cannot execute here is pinned against the
source. `windowState.js` has no electron in it and is required directly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_PRELOAD = _ROOT / "desktop" / "preload.js"
_WINDOW_STATE = _ROOT / "desktop" / "windowState.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src(path: Path = _MAIN) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str) -> str:
    src = _src()
    start = src.index(start_marker)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _run(script: str, tmp_path: Path, name: str) -> list:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ}
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _ws_require() -> str:
    return f"const ws = require({json.dumps(str(_WINDOW_STATE))});"


# --------------------------------------------------------------------------- #
# 1. Placement: the other screen when there is one, else a cascade
# --------------------------------------------------------------------------- #

_TWO = [
    {"workArea": {"x": 0, "y": 0, "width": 2560, "height": 1400}},
    {"workArea": {"x": 2560, "y": 0, "width": 1920, "height": 1040}},
]
_ONE = [_TWO[0]]
_MAIN_ON_FIRST = {"x": 200, "y": 100, "width": 1440, "height": 900}
_MAIN_ON_SECOND = {"x": 2700, "y": 50, "width": 1440, "height": 900}


@requires_node
def test_a_first_popout_lands_centred_on_the_other_screen(tmp_path):
    script = _ws_require() + f"""
const out = {{
  fromFirst: ws.popoutPlacement(null, {json.dumps(_TWO)}, {json.dumps(_MAIN_ON_FIRST)}, 0),
  fromSecond: ws.popoutPlacement(null, {json.dumps(_TWO)}, {json.dumps(_MAIN_ON_SECOND)}, 0),
  second: ws.popoutPlacement(null, {json.dumps(_TWO)}, {json.dumps(_MAIN_ON_FIRST)}, 1),
}};
console.log(JSON.stringify(out));
"""
    out = _run(script, tmp_path, "placement_two")
    a = out["fromFirst"]
    # Centred on the SECOND screen, the default size, flagged secondary.
    assert a["secondary"] is True
    assert (a["width"], a["height"]) == (1200, 820)
    assert a["x"] == 2560 + (1920 - 1200) // 2 and a["y"] == (1040 - 820) // 2
    # The main window on the second screen sends the pop-out to the first.
    b = out["fromSecond"]
    assert b["secondary"] is True and b["x"] == (2560 - 1200) // 2 and b["y"] == (1400 - 820) // 2
    # A second pop-out steps down and right so it never covers the first.
    c = out["second"]
    assert c["x"] == a["x"] + 48 and c["y"] == a["y"] + 48


@requires_node
def test_on_one_screen_a_popout_cascades_off_the_main_window_and_stays_on_screen(tmp_path):
    near_edge = {"x": 1800, "y": 900, "width": 1440, "height": 900}
    script = _ws_require() + f"""
const out = {{
  cascade: ws.popoutPlacement(null, {json.dumps(_ONE)}, {json.dumps(_MAIN_ON_FIRST)}, 0),
  clamped: ws.popoutPlacement(null, {json.dumps(_ONE)}, {json.dumps(near_edge)}, 0),
  noMain: ws.popoutPlacement(null, {json.dumps(_ONE)}, null, 0),
  noDisplays: ws.popoutPlacement(null, [], {json.dumps(_MAIN_ON_FIRST)}, 0),
}};
console.log(JSON.stringify(out));
"""
    out = _run(script, tmp_path, "placement_one")
    c = out["cascade"]
    assert c["secondary"] is False and c["x"] == 200 + 48 and c["y"] == 100 + 48
    k = out["clamped"]
    assert k["x"] + k["width"] <= 2560 and k["y"] + k["height"] <= 1400, k
    n = out["noMain"]
    assert n["secondary"] is False and n["x"] == (2560 - 1200) // 2  # centred on the first display
    d = out["noDisplays"]
    assert d == {"width": 1200, "height": 820, "secondary": False}  # size only — the caller centres


@requires_node
def test_a_saved_rectangle_is_kept_while_its_screen_exists_and_resized_when_not(tmp_path):
    saved = {"x": 2800, "y": 120, "width": 1000, "height": 700}
    script = _ws_require() + f"""
const out = {{
  kept: ws.popoutPlacement({json.dumps(saved)}, {json.dumps(_TWO)}, {json.dumps(_MAIN_ON_FIRST)}, 0),
  unplugged: ws.popoutPlacement({json.dumps(saved)}, {json.dumps(_ONE)}, {json.dumps(_MAIN_ON_FIRST)}, 0),
  tooBig: ws.popoutPlacement({{"width": 5000, "height": 4000}}, {json.dumps(_ONE)}, null, 0),
}};
console.log(JSON.stringify(out));
"""
    out = _run(script, tmp_path, "placement_saved")
    assert out["kept"] == {**saved, "secondary": False}
    u = out["unplugged"]
    # The monitor is gone: the SIZE survives, the position is re-derived on screen.
    assert (u["width"], u["height"]) == (1000, 700) and u["x"] + u["width"] <= 2560
    t = out["tooBig"]
    assert t["width"] == 2560 - 40 and t["height"] == 1400 - 40


@requires_node
def test_popout_bounds_are_remembered_per_route_and_a_corrupt_file_is_null(tmp_path):
    script = _ws_require() + f"""
const dir = {json.dumps(str(tmp_path))};
const out = {{}};
out.before = ws.loadPopoutBounds(dir, "/chat");
ws.savePopoutBounds(dir, "/chat", {{ x: 10, y: 20, width: 900, height: 600 }});
ws.savePopoutBounds(dir, "/terminals", {{ x: 30, y: 40, width: 1100, height: 700 }});
out.chat = ws.loadPopoutBounds(dir, "/chat");
out.build = ws.loadPopoutBounds(dir, "/terminals");
out.other = ws.loadPopoutBounds(dir, "/agents");
ws.savePopoutBounds(dir, "/chat", {{ width: 100, height: 100 }}); // below the floors
out.tiny = ws.loadPopoutBounds(dir, "/chat");
require("fs").writeFileSync(require("path").join(dir, "popout-windows.json"), "{{not json", "utf8");
out.corrupt = ws.loadPopoutBounds(dir, "/terminals");
console.log(JSON.stringify(out));
"""
    out = _run(script, tmp_path, "bounds_store")
    assert out["before"] is None
    assert out["chat"] == {"x": 10, "y": 20, "width": 900, "height": 600}
    assert out["build"] == {"x": 30, "y": 40, "width": 1100, "height": 700}
    assert out["other"] is None
    assert out["tiny"] is None
    assert out["corrupt"] is None


# --------------------------------------------------------------------------- #
# 2. The path a pop-out may open — lifted from main.js
# --------------------------------------------------------------------------- #


@requires_node
def test_normalize_popout_path_takes_dashboard_paths_only(tmp_path):
    script = (
        _decl("PROTOCOL_PATH_RX") + "\n" + _lift("function normalizePopoutPath(") + "\n"
        + "const cases = ['/chat', 'chat', '/chat?project=p1', '/sessions/abc#x', '/', '', "
        + "'https://evil.example/chat', '/../secrets', '//chat', 'chat/../x', '/a'.padEnd(300, 'b'), '/terminals'];\n"
        + "console.log(JSON.stringify(cases.map((c) => normalizePopoutPath(c))));\n"
    )
    out = _run(script, tmp_path, "normalize_path")
    # Leading slashes collapse exactly as the ironjarvis:// path does ("//chat"
    # is "/chat"); a host, a dotted segment, an inner "//" and an over-long path
    # are refused; the query and the fragment are dropped; "/" is nobody's.
    assert out == [
        "/chat", "/chat", "/chat", "/sessions/abc", "", "",
        "", "", "/chat", "", "", "/terminals",
    ], out


# --------------------------------------------------------------------------- #
# 3. The shell: one window per route, sender-checked IPC, the same chrome
# --------------------------------------------------------------------------- #


def test_main_keeps_one_window_per_route_with_the_main_windows_chrome_and_guards():
    src = _src()
    body = _lift("function openPopout(")
    assert "const existing = livePopout(route);" in body and "focusPopout(route);" in body, "an open route is focused, not duplicated"
    assert 'titleBarStyle: "hidden",' in body and "titleBarOverlay:" in body, "the same frameless chrome as the main window"
    assert 'preload: path.join(__dirname, "preload.js"),' in body
    assert "`--ij-popout=${route}`" in body, "the renderer learns it is a pop-out from the preload"
    assert "contextIsolation: true," in body and "nodeIntegration: false," in body
    assert "installSpellcheckMenu(win);" in body
    assert 'win.webContents.setWindowOpenHandler(' in body and 'win.webContents.on("will-navigate"' in body
    assert 'win.webContents.on("did-fail-load"' in body and "waitForDashboard(60000, 500)" in body
    assert "windowState.savePopoutBounds(userDataDir, route, win.getBounds());" in body
    assert 'win.on("closed", () => {' in body and "popouts.delete(route)" in body
    # Never the tray window: no close interception, no keep-running prompt.
    assert 'win.on("close"' not in body and "hideToTray" not in body
    # Placement comes from the shared rule, with the main window and the count.
    placing = _lift("function initialPopoutBounds(")
    assert "windowState.popoutPlacement(saved, screen.getAllDisplays(), main, popouts.size)" in placing
    # The IPC: every handler sender-checked.
    for chan in ("popout:open", "popout:list", "popout:focus", "popout:close"):
        at = src.index(f'ipcMain.handle("{chan}"')
        assert "isTrustedDashboardSender(event)" in src[at: at + 220], chan
    # The tray behaviour is untouched: window-all-closed stays a no-op.
    assert 'app.on("window-all-closed", () => {\n    // Intentionally empty: stay resident in the tray.' in src


def test_preload_exposes_the_bridge_and_reads_its_path_from_the_argument():
    pre = _src(_PRELOAD)
    assert 'const POPOUT_PATH = readArg("--ij-popout=");' in pre
    at = pre.index("popout: {")
    block = pre[at: pre.index("},", at)]
    for line in (
        "isPopout: POPOUT_PATH !== \"\",",
        "path: POPOUT_PATH,",
        'open: (path) => ipcRenderer.invoke("popout:open", String(path ?? "")),',
        'list: () => ipcRenderer.invoke("popout:list"),',
        'focus: (path) => ipcRenderer.invoke("popout:focus", String(path ?? "")),',
        'close: (path) => ipcRenderer.invoke("popout:close", String(path ?? "")),',
    ):
        assert line in block, line
    # The token argument reader was generalised, not duplicated.
    assert 'return readArg("--ij-token=");' in pre


@requires_node
def test_the_desktop_files_still_parse():
    for f in (_MAIN, _PRELOAD, _WINDOW_STATE):
        proc = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"{f.name}: {proc.stderr}"
