"""v1.264.0 — the sidebar's first minute: three things the user hit in a row.

THE REPORT, after loading the add-on into Edge:
  1. "did not reach iron jarvis" — the add-on's socket said "could not reach
     Iron Jarvis on 127.0.0.1:8787" and, in the offline state, offered NO
     Connect button: the bridge retries on a backoff that reaches 30 s, and the
     user had just (re)started the app.
  2. Pressing Open Jarvis in the sidebar opened the dashboard in a BROWSER TAB —
     which has no token — so every card was empty under "Daemon rejected your
     token", and the Browser card misread its own 401 as "This daemon does not
     have the Browser surface yet — restart Iron Jarvis".
  3. Enter in the sidebar's composer did nothing; only the button sent.

WHAT THIS FILE PINS:
  * desktop/main.js owns `ironjarvis://` and turns a link into ONE dashboard
    path — lifted and run under node for the parser, source-pinned for the
    wiring (registration, `open-url`, `second-instance` argv, the cold-start
    window opening at the pending path);
  * the add-on: Enter sends / Steer while running / Shift+Enter newline
    (`keyToPress`), offline offers Connect, and the offline words say what to do.

House idiom (``test_desktop_reliability_v1249.py``): shipped source lifted
verbatim and run under node; seams that cannot execute here are pinned against
the source.
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
_SOCKET = _ROOT / "extensions" / "chrome" / "src" / "bridge" / "socket.ts"
_PANEL_TS = _ROOT / "extensions" / "chrome" / "src" / "sidepanel" / "sidepanel.ts"
_PANEL_HTML = _ROOT / "extensions" / "chrome" / "src" / "sidepanel" / "sidepanel.html"
_BACKGROUND = _ROOT / "extensions" / "chrome" / "src" / "background" / "index.ts"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(src: str, start_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _decl(src: str, name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", src, re.M)
    assert m, f"no top-level decl {name}"
    return m.group(0)


def _run(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), *argv], capture_output=True, text=True, encoding="utf-8",
        timeout=60, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ==========================================================================
# 1. ironjarvis:// → one dashboard path, and nothing else
# ==========================================================================

_PARSER_HARNESS = """
__DECLS__
__PARSER__
__ARGV__
const cases = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify({
  parsed: cases.map((c) => dashboardPathFromProtocolUrl(c)),
  fromArgv: protocolUrlIn(["C:\\\\Iron Jarvis\\\\Iron Jarvis.exe", "--hidden", "ironjarvis://chat"]),
  noneInArgv: protocolUrlIn(["C:\\\\Iron Jarvis\\\\Iron Jarvis.exe", "--hidden"]),
}) + "\\n");
"""


@requires_node
def test_the_link_names_a_dashboard_path_and_refuses_everything_else(tmp_path):
    src = _src(_MAIN)
    script = (
        _PARSER_HARNESS.replace("__DECLS__", "\n".join(_decl(src, n) for n in ("APP_PROTOCOL", "PROTOCOL_PATH_RX")))
        .replace("__PARSER__", _lift(src, "function dashboardPathFromProtocolUrl"))
        .replace("__ARGV__", _lift(src, "function protocolUrlIn"))
    )
    cases = [
        "ironjarvis://computeruse",
        "ironjarvis:///computeruse",
        "IronJarvis://chat",
        "ironjarvis://",
        "ironjarvis://sessions/abc-123?tab=review",
        "ironjarvis://../../etc",
        "ironjarvis://evil.example.com/",
        "ironjarvis://chat//x",
        "ironjarvis://chat<script>",
        "https://127.0.0.1:8788/chat",
        "",
        "ironjarvis://" + "a" * 300,
    ]
    out = _run(script, tmp_path, "parser", json.dumps(cases))
    assert out["parsed"] == [
        "/computeruse", "/computeruse", "/chat", "/", "/sessions/abc-123?tab=review",
        "", "", "", "", "", "", "",
    ], out["parsed"]
    assert out["fromArgv"] == "ironjarvis://chat" and out["noneInArgv"] == ""


def test_the_desktop_owns_the_scheme_and_every_launch_shape_lands_on_the_page():
    src = _src(_MAIN)
    assert 'app.setAsDefaultProtocolClient(APP_PROTOCOL)' in src, "packaged registration missing"
    assert "app.setAsDefaultProtocolClient(APP_PROTOCOL, process.execPath" in src, "dev registration missing"
    assert 'app.on("open-url"' in src and "openDashboardPath(dashboardPathFromProtocolUrl(url))" in src
    link = src.index('app.on("second-instance", (_event, argv)')
    assert "openDashboardPath(dashboardPathFromProtocolUrl(protocolUrlIn(argv)))" in src[link:link + 300]
    # Registered AFTER the plain second-launch handler, which stays verbatim
    # (test_desktop_lifecycle_v1192 lifts the FIRST such block).
    plain = src.index('app.on("second-instance", () => {')
    assert plain < link
    # The cold start: the URL is in this process's argv, and the first window
    # opens there — AFTER the root load that test_desktop_reliability_v1226 pins.
    assert "pendingProtocolPath = dashboardPathFromProtocolUrl(protocolUrlIn(process.argv))" in src
    tail = src[src.index("mainWin.loadURL(DASHBOARD_URL);"):]
    assert "mainWin.loadURL(`${DASHBOARD_URL}${pendingProtocolPath}`);" in tail[:400]
    opener = _lift(src, "function openDashboardPath")
    assert "mainWin.loadURL(`${DASHBOARD_URL}${path}`)" in opener and "showMainWindow()" in opener
    assert "pendingProtocolPath = path" in opener, "a launch with no window yet must be remembered"


# ==========================================================================
# 2. the add-on: Enter sends, offline offers Connect, the words say what to do
# ==========================================================================

_KEYS_HARNESS = """
__KEYS__
process.stdout.write(JSON.stringify([
  keyToPress("Enter", false, false), keyToPress("Enter", false, true),
  keyToPress("Enter", true, false), keyToPress("Enter", true, true),
  keyToPress("a", false, false), keyToPress("Escape", false, true),
]) + "\\n");
"""


@requires_node
def test_enter_sends_steers_while_running_and_shift_enter_does_not(tmp_path):
    src = _src(_PANEL_TS)
    fn = _lift(src, "export function keyToPress").replace("export function", "function")
    # The lift is TypeScript; node runs JavaScript. Only the annotations go —
    # the body is byte-identical to what the add-on ships.
    fn = re.sub(r": (?:string|boolean)\b", "", fn).replace(': "send" | "steer" | null', "")
    out = _run(_KEYS_HARNESS.replace("__KEYS__", fn), tmp_path, "keys")
    assert out == ["send", "steer", None, None, None, None], out
    # And the handler is wired to the composer, reading the ONE running flag.
    assert 'el.ask?.addEventListener("keydown"' in src
    assert 'document.body.dataset["turn"] === "running"' in src
    assert "event.preventDefault()" in src
    assert "Enter sends" in _src(_PANEL_HTML) and "Shift+Enter" in _src(_PANEL_HTML)


def test_offline_offers_connect_and_a_press_reconnects_now():
    src = _src(_SOCKET)
    toggle = _lift(src, "export function toggleAction")
    offline = toggle[toggle.index('case "offline":'):]
    assert re.search(r'case "offline":.*?return "connect";', offline, re.S), "offline offers no Connect"
    # The press runs `resume`, which resets the backoff and connects at once.
    assert 'if (action === "connect") {\n          await socket.resume();' in _src(_BACKGROUND)
    resume = _lift(src, "  async resume()")
    assert "this.backoffMs = BACKOFF_MIN_MS;" in resume and "this.connect();" in resume


def test_the_offline_words_name_the_cause_and_the_next_press():
    src = _src(_SOCKET)
    assert "Iron Jarvis did not answer at 127.0.0.1:8787" in src
    assert "then press Connect" in src
    assert "could not reach Iron Jarvis on 127.0.0.1:8787" not in src
