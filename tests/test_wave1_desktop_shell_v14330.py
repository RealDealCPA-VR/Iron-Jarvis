"""v1.433.0 (Wave 1, group D) — the desktop shell's own fixes, driven under node.

House idiom (test_desktop_offline_banner_v1312.py): the shipped function text
is lifted out of ``desktop/main.js`` with a brace-matching extractor and run
against stubs, so these pins prove behaviour, not the presence of a string.

* A8 — Settings › Maintenance's "Restart Iron Jarvis" used to post
  ``/shutdown`` with no body (the daemon's QUIT) and the supervisor counted the
  exit as a crash. ``restartDaemonTidy`` marks the exit as asked for FIRST,
  then posts ``{"restart": true}``; the exit then respawns at once and counts
  nothing. ``{stopping: true}`` (a restore stops the daemon itself) posts
  nothing. A daemon that does not stop is stopped by force after
  ``TIDY_RESTART_KILL_MS``, still as a restart. The IPC handler is
  sender-checked.
* A9 — ``abortUpdateInstall`` turns install-on-quit OFF, or the next Quit
  still ran the installer that just failed.
* D3 — ``_emitUpdateState`` reaches every pop-out window, not just the main one.
* D5 — ``clipboard:write``, ``clipboard:writeHtml`` and ``update:check`` answer
  the dashboard only.
* D6 — a link may hand only http, https and mailto addresses to the system.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
_PRELOAD = _ROOT / "desktop" / "preload.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _run(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    env = {**os.environ, "IJ_MAIN": str(_MAIN)}
    proc = subprocess.run(
        ["node", str(f), *argv], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_EXTRACT = r"""
const fs = require("fs");
const src = fs.readFileSync(process.env.IJ_MAIN, "utf8").replace(/\r\n/g, "\n");
function matchBrace(start) {
  const stack = []; let depth = 0; let i = start; let mode = "code";
  const push = (m) => { stack.push({ mode, depth }); mode = m; depth = 0; };
  const pop = () => { const f = stack.pop(); mode = f.mode; depth = f.depth; };
  for (; i < src.length; i++) {
    const c = src[i], n = src[i + 1];
    if (mode === "code" || mode === "expr") {
      if (c === "/" && n === "/") { i = src.indexOf("\n", i); continue; }
      if (c === "/" && n === "*") { i = src.indexOf("*/", i) + 1; continue; }
      if (c === '"' || c === "'") { const q = c; i++; while (src[i] !== q) { if (src[i] === "\\") i++; i++; } continue; }
      if (c === "`") { push("tpl"); continue; }
      if (c === "{") { depth++; continue; }
      if (c === "}") {
        if (depth === 0 && mode === "expr") { pop(); continue; }
        depth--;
        if (depth === 0 && mode === "code" && stack.length === 0) return i;
        continue;
      }
    } else if (mode === "tpl") {
      if (c === "\\") { i++; continue; }
      if (c === "`") { pop(); continue; }
      if (c === "$" && n === "{") { i++; push("expr"); continue; }
    }
  }
  throw new Error("unbalanced braces from " + start);
}
function fnSource(name) {
  const m = new RegExp(`^(?:async )?function ${name}\\(`, "m").exec(src);
  if (!m) throw new Error("main.js has no function " + name);
  const open = src.indexOf("{", m.index);
  return src.slice(m.index, matchBrace(open) + 1);
}
function decl(name) {
  const m = new RegExp(`^(?:const|let) ${name} = [^\\n]*;`, "m").exec(src);
  if (!m) throw new Error("main.js has no top-level decl " + name);
  return m[0].replace(/^(?:const|let) /, "var ");
}
// A multi-line top-level `const X = new Set([...]);`.
function setDecl(name) {
  const i = src.indexOf(`const ${name} = new Set([`);
  if (i < 0) throw new Error("main.js has no Set decl " + name);
  return src.slice(i, src.indexOf("]);", i) + 3).replace(/^const /, "var ");
}
// The text of `ipcMain.handle("<channel>", …);` up to its closing `  });`.
function handlerSource(channel) {
  const at = src.indexOf(`ipcMain.handle("${channel}",`);
  if (at < 0) throw new Error("main.js has no handler for " + channel);
  const end = src.indexOf("\n  });", at);
  return src.slice(at, end + "\n  });".length);
}
"""


# --------------------------------------------------------------------------
# A8 — the tidy restart
# --------------------------------------------------------------------------

_TIDY = r"""
__EXTRACT__
const { EventEmitter } = require("events");
const path = require("path");
const scenario = process.argv[2];
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null, userDataDir = "X";
let authToken = "t";
const notes = []; const logs = []; const killed = []; const posts = [];
let tray = { setToolTip(t) { notes.push("tooltip:" + t); } };
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
function fileLogger() { return (s) => logs.push(String(s).trim()); }
class Notification { constructor(o) { this.o = o; } show() { notes.push("toast:" + this.o.body); } }
function adoptOrReplaceExistingDaemon() { throw new Error("not exercised"); }
function verifyInstallIntegrity() { return { ok: true, checked: 1, missing: [], mismatched: [] }; }
function handleCorruptInstall() {}
function refreshTrayMenu() {}
function armDashboardReload() {}
// The real kill's effect: the child dies, the supervisor's close hook runs.
function killChild(child, label, reason) {
  killed.push(`${label}:${reason}`);
  child.exitCode = 1;
  process.nextTick(() => child.emit("close", 1));
}
const http = {
  request(opts, cb) {
    const req = new EventEmitter();
    req.setTimeout = () => {};
    req.end = (data) => {
      posts.push({
        path: opts.path, method: opts.method, headers: opts.headers, body: data === undefined ? null : String(data),
        manualAtPost: !!(_services.daemon && _services.daemon.manualRestart),
      });
      // The daemon answers, then stops by itself (exit 3, as routes/system.py's
      // graceful stop does) — unless this scenario's daemon never stops.
      if (scenario !== "nostop") setImmediate(() => { daemonProc.exitCode = 3; daemonProc.emit("close", 3); });
    };
    return req;
  },
};
for (const d of ["WINDOWS_SHUTDOWN_EXIT_CODES", "WINDOWS_SHUTDOWN_GRACE_MS", "windowsSessionEnding",
                 "RESTART_BACKOFF_MS", "FAST_DEATH_MS", "FAST_DEATHS_TO_VERIFY", "RESTART_CAP_MAX",
                 "RESTART_CAP_WINDOW_MS", "DEATHS_DAY_MS", "DEATHS_DAY_TOAST_AT", "_services", "_trayDegraded",
                 "DAEMON_PORT", "_dwKills", "_dwBreakerTripped", "TIDY_RESTART_KILL_MS"]) eval(decl(d));
for (const f of ["isWindowsShutdownExit", "markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop",
                 "restartDaemonFromShell", "postDaemonShutdown", "restartDaemonTidy"]) eval(fnSource(f));
// Fake timers: the 20 s fallback must fire without waiting 20 s.
const timers = [];
const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => { const t = { fn, ms, fired: false }; timers.push(t); return t; };
global.clearTimeout = (t) => { if (t) t.fired = true; };
function fireTimers(maxMs) { for (const t of timers) if (!t.fired && t.ms <= maxMs) { t.fired = true; t.fn(); } }
let spawns = 0;
const spawnFn = () => {
  spawns += 1; const c = new EventEmitter(); c.pid = 100 + spawns; c.exitCode = null; c.signalCode = null; return c;
};
startService("daemon", spawnFn);
const out = { scenario };
if (scenario === "control") {
  // Anti-vacuity: an exit nobody asked for IS counted — the harness can see a crash.
  daemonProc.exitCode = 3; daemonProc.emit("close", 3);
} else {
  out.ret = restartDaemonTidy(scenario === "stopping" ? { stopping: true } : undefined);
  out.manualAfterCall = !!_services.daemon.manualRestart;
  if (scenario === "stopping") setImmediate(() => { daemonProc.exitCode = 3; daemonProc.emit("close", 3); });
}
realSetTimeout(() => {
  if (scenario === "nostop") fireTimers(TIDY_RESTART_KILL_MS);
  realSetTimeout(() => {
    const rec = _services.daemon;
    Object.assign(out, {
      posts, killed, spawns, killMs: TIDY_RESTART_KILL_MS,
      rec: { restarts: rec.restarts, lastExit: rec.lastExit === undefined ? null : rec.lastExit,
             restartTimes: (rec.restartTimes || []).length, deaths: (rec.deaths || []).length,
             manualRestart: !!rec.manualRestart },
      toasts: notes.filter((n) => n.startsWith("toast:")),
      logs,
    });
    process.stdout.write(JSON.stringify(out) + "\n");
  }, 30);
}, 30);
"""


def _tidy(scenario: str, tmp_path: Path) -> dict:
    return _run(_TIDY.replace("__EXTRACT__", _EXTRACT), tmp_path, f"tidy_{scenario}", scenario)


@requires_node
def test_control_an_unasked_exit_is_counted_as_a_crash(tmp_path):
    """Anti-vacuity for the next pins: the harness sees the ladder count an
    exit nobody asked for (that is what Settings' old restart looked like)."""
    out = _tidy("control", tmp_path)
    assert out["rec"]["restarts"] == 1 and out["rec"]["lastExit"] == 3, out["rec"]
    assert out["rec"]["deaths"] == 1, out["rec"]


@requires_node
def test_the_tidy_restart_marks_the_exit_first_then_asks_for_a_restart(tmp_path):
    out = _tidy("restart", tmp_path)
    assert out["ret"] is True, out
    assert out["manualAfterCall"] is True, "the exit was not marked as asked for"
    assert len(out["posts"]) == 1, out["posts"]
    post = out["posts"][0]
    assert post["path"] == "/shutdown" and post["method"] == "POST", post
    # The daemon's own word for "coming straight back": the code host and
    # Build › Code's coding tools keep running (routes/system.py).
    assert json.loads(post["body"]) == {"restart": True}, post
    assert post["headers"]["Content-Type"] == "application/json", post["headers"]
    assert post["headers"]["Authorization"] == "Bearer t", post["headers"]
    # Marked BEFORE the request went: the exit can follow at once.
    assert post["manualAtPost"] is True, "manualRestart was set only after the post"
    # The exit respawned at once and counted nothing.
    assert out["spawns"] == 2, out
    assert out["rec"] == {
        "restarts": 0, "lastExit": None, "restartTimes": 0, "deaths": 0, "manualRestart": False,
    }, out["rec"]
    assert out["toasts"] == [], out["toasts"]
    assert out["killed"] == [], "a daemon that stopped by itself was killed anyway"


@requires_node
def test_a_restore_that_stops_the_daemon_itself_posts_nothing_and_counts_nothing(tmp_path):
    out = _tidy("stopping", tmp_path)
    assert out["ret"] is True and out["manualAfterCall"] is True, out
    assert out["posts"] == [], "the restore already stops it — no second shutdown request"
    assert out["spawns"] == 2, out
    assert out["rec"]["restarts"] == 0 and out["rec"]["lastExit"] is None, out["rec"]


@requires_node
def test_a_daemon_that_will_not_stop_is_stopped_by_force_still_as_a_restart(tmp_path):
    out = _tidy("nostop", tmp_path)
    assert out["killMs"] >= 5000, "the fallback must give a tidy stop real time"
    assert out["killed"] == ["daemon:restart"], out["killed"]
    assert out["spawns"] == 2, out
    assert out["rec"]["restarts"] == 0 and out["rec"]["lastExit"] is None, out["rec"]


# --------------------------------------------------------------------------
# A8 / D5 — IPC handlers answer the dashboard only (run, not grepped)
# --------------------------------------------------------------------------

_IPC = r"""
__EXTRACT__
const handlers = {};
const ipcMain = { handle(ch, fn) { handlers[ch] = fn; } };
const calls = [];
var DASHBOARD_PORT = 8788;
eval(setDecl("DASHBOARD_ORIGINS"));
for (const f of ["isDashboardUrl", "isTrustedDashboardSender"]) eval(fnSource(f));
function restartDaemonTidy(opts) { calls.push(["tidy", opts]); return true; }
const clipboard = {
  writeText(t) { calls.push(["writeText", t]); },
  write(o) { calls.push(["write", o]); },
};
let _updateState = { status: "idle" };
let _updateSource = "manifest";
function initUpdater() { calls.push(["initUpdater"]); return null; }
function _emitUpdateState(p) { calls.push(["emit", p.status]); _updateState = { ..._updateState, ...p }; }
async function runUpdateCheck() { calls.push(["check"]); }
function friendlyStableError(m) { return m; } function friendlyUpdateError(m) { return m; }
for (const ch of ["shell:restartDaemonTidy", "clipboard:write", "clipboard:writeHtml", "update:check"]) eval(handlerSource(ch));
const ours = { senderFrame: { url: "http://localhost:8788/settings" } };
const evil = { senderFrame: { url: "https://evil.example/x" } };
const gone = {};
(async () => {
  const out = {};
  out.tidyEvil = await handlers["shell:restartDaemonTidy"](evil, { stopping: true });
  out.tidyGone = await handlers["shell:restartDaemonTidy"](gone);
  out.callsAfterEvilTidy = calls.length;
  out.tidyOurs = await handlers["shell:restartDaemonTidy"](ours);
  out.tidyStopping = await handlers["shell:restartDaemonTidy"](ours, { stopping: true });
  out.tidyJunk = await handlers["shell:restartDaemonTidy"](ours, { stopping: "yes" });
  const before = calls.length;
  out.writeEvil = await handlers["clipboard:write"](evil, "rm -rf /");
  out.writeHtmlEvil = await handlers["clipboard:writeHtml"](evil, "<b>x</b>", "x");
  out.checkEvil = await handlers["update:check"](evil);
  out.callsByEvil = calls.slice(before);
  out.writeOurs = await handlers["clipboard:write"](ours, "hello");
  out.writeHtmlOurs = await handlers["clipboard:writeHtml"](ours, "<b>x</b>", "x");
  out.checkOurs = await handlers["update:check"](ours);
  out.calls = calls;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


@requires_node
def test_the_tidy_restart_and_the_clipboard_writes_and_the_update_check_refuse_other_senders(tmp_path):
    out = _run(_IPC.replace("__EXTRACT__", _EXTRACT), tmp_path, "ipc")
    # A8: refused = null, and nothing restarted.
    assert out["tidyEvil"] is None and out["tidyGone"] is None, out
    assert out["callsAfterEvilTidy"] == 0, out["calls"]
    assert out["tidyOurs"] is True
    tidy = [c for c in out["calls"] if c[0] == "tidy"]
    # The flag is read strictly (a sender cannot smuggle anything else in).
    assert [c[1] for c in tidy] == [{"stopping": False}, {"stopping": True}, {"stopping": False}], tidy
    # D5: another frame writes nothing and starts no check.
    assert out["writeEvil"] is False and out["writeHtmlEvil"] is False, out
    assert out["checkEvil"] is None, out
    assert out["callsByEvil"] == [], out["callsByEvil"]
    # The dashboard keeps all three.
    assert out["writeOurs"] is True and out["writeHtmlOurs"] is True, out
    assert ["writeText", "hello"] in out["calls"], out["calls"]
    assert ["write", {"text": "x", "html": "<b>x</b>"}] in out["calls"], out["calls"]
    assert ["initUpdater"] in out["calls"] and out["checkOurs"]["status"] == "unsupported", out


def test_the_preload_exposes_the_tidy_restart_on_the_shell_bridge():
    pre = _read(_PRELOAD)
    at = pre.index("restartDaemonTidy:")
    block = pre[at : at + 300]
    assert 'ipcRenderer.invoke("shell:restartDaemonTidy"' in block, block
    assert "stopping: !!(opts && opts.stopping === true)" in block, block


# --------------------------------------------------------------------------
# A9 — a failed install does not install on the next Quit
# --------------------------------------------------------------------------

_ABORT = r"""
__EXTRACT__
const calls = [];
let updateInstallInFlight = true, shuttingDown = false, isQuitting = true;
let pendingUpdateInfo = { version: "9.9.9" };
const _autoUpdater = { autoInstallOnAppQuit: true };
let tray = { setToolTip() {} };
function desktopLog() {} function clearUpdatePending() { calls.push("clearUpdatePending"); }
function refreshTrayMenu() {} function friendlyUpdateError(m) { return m; }
function _emitUpdateState(p) { calls.push("emit:" + p.status); }
const dialog = { showMessageBoxSync() { calls.push("dialog"); } };
const app = { quit() { calls.push("quit"); } };
eval(fnSource("abortUpdateInstall"));
abortUpdateInstall(new Error("No update filepath provided"));
process.stdout.write(JSON.stringify({
  autoInstallOnAppQuit: _autoUpdater.autoInstallOnAppQuit, pendingUpdateInfo, isQuitting, calls,
}) + "\n");
"""


@requires_node
def test_a_failed_install_turns_install_on_quit_off(tmp_path):
    out = _run(_ABORT.replace("__EXTRACT__", _EXTRACT), tmp_path, "abort")
    assert out["pendingUpdateInfo"] is None, out
    assert out["autoInstallOnAppQuit"] is False, (
        "the next Quit would still run the installer that just failed, skipping the recovery marker"
    )
    # The rest of the abort path is unchanged.
    assert out["isQuitting"] is False and "dialog" in out["calls"] and "emit:error" in out["calls"], out


# --------------------------------------------------------------------------
# D3 — update progress reaches a popped-out window
# --------------------------------------------------------------------------

_EMIT = r"""
__EXTRACT__
const sent = [];
function win(name, destroyed) {
  return { isDestroyed: () => destroyed, webContents: { send: (ch, s) => sent.push([name, ch, s.status]) } };
}
let mainWin = win("main", false);
const popouts = new Map([["/settings", win("settings", false)], ["/chat", win("closing", true)],
                         ["/boom", { isDestroyed: () => false, webContents: { send() { throw new Error("gone"); } } }],
                         ["/agents", win("agents", false)]]);
let _updateState = { status: "idle", current: "1.0.0" };
function safeAppVersion() { return "1.0.0"; }
eval(fnSource("_emitUpdateState"));
_emitUpdateState({ status: "downloading", percent: 40 });
mainWin = null; // the main window was closed to the tray
_emitUpdateState({ status: "downloaded" });
process.stdout.write(JSON.stringify({ sent }) + "\n");
"""


@requires_node
def test_update_state_reaches_every_live_popout_window(tmp_path):
    out = _run(_EMIT.replace("__EXTRACT__", _EXTRACT), tmp_path, "emit")
    sent = [tuple(s) for s in out["sent"]]
    assert ("main", "update:state", "downloading") in sent
    for name in ("settings", "agents"):
        assert (name, "update:state", "downloading") in sent, sent
        # A pop-out keeps hearing with the main window gone (closed to the tray),
        # and one window that throws never stops the others.
        assert (name, "update:state", "downloaded") in sent, sent
    assert not any(s[0] == "closing" for s in sent), "a destroyed window was sent to"


# --------------------------------------------------------------------------
# D6 — only web and mail addresses leave for the system
# --------------------------------------------------------------------------

_LINKS = r"""
__EXTRACT__
const opened = []; const logs = [];
const shell = { openExternal(u) { opened.push(u); return Promise.reject(new Error("no handler")); } };
function desktopLog(level, msg) { logs.push(msg); }
eval(decl("EXTERNAL_SCHEMES"));
for (const f of ["isSafeExternalUrl", "openExternalIfSafe"]) eval(fnSource(f));
const cases = JSON.parse(process.argv[2]);
const verdicts = cases.map((u) => openExternalIfSafe(u));
setImmediate(() => process.stdout.write(JSON.stringify({ verdicts, opened, logs }) + "\n"));
"""

_LINK_CASES: list[tuple[str, bool]] = [
    ("https://example.com/report?x=1", True),
    ("http://example.com/", True),
    ("mailto:client@example.com?subject=Hi", True),
    ("HTTPS://EXAMPLE.COM/", True),
    ("file:///C:/Windows/System32/calc.exe", False),
    ("ms-settings:privacy", False),
    ("search-ms:query=secret", False),
    ("javascript:alert(1)", False),
    ("vbscript:msgbox", False),
    ("ftp://example.com/x", False),
    ("\\\\host\\share\\x.exe", False),
    ("C:\\Windows\\notepad.exe", False),
    ("not a url", False),
    ("", False),
]


@requires_node
def test_a_link_opens_outside_only_when_it_is_a_web_or_mail_address(tmp_path):
    script = _LINKS.replace("__EXTRACT__", _EXTRACT)
    f = tmp_path / "links.js"
    f.write_text(script, encoding="utf-8")
    env = {**os.environ, "IJ_MAIN": str(_MAIN)}
    proc = subprocess.run(
        ["node", str(f), json.dumps([c[0] for c in _LINK_CASES])],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr  # a rejected openExternal never escapes
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    wrong = [(u, want, got) for (u, want), got in zip(_LINK_CASES, out["verdicts"]) if want != got]
    assert not wrong, wrong
    assert out["opened"] == [u for u, ok in _LINK_CASES if ok], out["opened"]
    # Refused quietly: a log line by scheme, never the address itself.
    assert out["logs"] and not any("calc.exe" in l or "secret" in l for l in out["logs"]), out["logs"]


def test_every_window_link_seam_goes_through_the_scheme_filter():
    """Both windows (main and pop-out) × the three seams. A bare
    ``shell.openExternal(url)`` with the page's own address would bypass it."""
    src = _read(_MAIN)
    assert "shell.openExternal(url)" not in src, "a link seam still hands any scheme to the system"
    assert src.count("openExternalIfSafe(url);") == 6, src.count("openExternalIfSafe(url);")
    assert src.count("setWindowOpenHandler(({ url }) => {\n    openExternalIfSafe(url);") == 2
