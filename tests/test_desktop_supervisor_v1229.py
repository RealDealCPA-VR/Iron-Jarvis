"""v1.229.0 — the desktop supervisor stops restarting a broken child, and says
what it did (``desktop/main.js``; audit Wave 3, findings D1/D3/D4/D5).

House idiom (``test_desktop_reliability_v1226.py``): the shipped function text
is pulled out of ``main.js`` VERBATIM and driven under node against stubs, so a
revert of the fix turns these red. This file uses the audit's brace-matching
extractor (``_EXTRACT``) rather than the column-0 slice, because the ladder's
helpers are interleaved with functions the harness must stub.

D1  The crash ladder restarted a child forever, every 60 s. Now: three deaths
    within 3 s of spawn verify the install (damaged → the Repair dialog); a
    clean install caps at 10 restarts / 15 min, toasts once more, and the tray
    gains "Restart Iron Jarvis" (resets the counters, respawns). The dashboard
    reload wait loops while the window sits on the error page and is re-armed
    by every dashboard spawn.
    Review: a tray Restart must cancel a PENDING backoff timer, or two
    dashboards race for :8788 and the ladder supervises the loser.
D3  A child dying every six minutes ran "healthy" by the 5-minute rule and was
    restarted forever with no toast. A second window (deaths in 24 h) toasts
    at the third death regardless of uptime.
D4  The tray tooltip said "restarting repeatedly" forever: it returns to
    "Iron Jarvis — running" in the healthy-reset branch and on the watchdog's
    next healthy /health.
D5  A crash and a kill looked alike in the child's log: every output chunk is
    ISO-stamped and killChild writes "[main] <iso> killed pid=… reason=…".
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

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src() -> str:
    return _MAIN.read_text(encoding="utf-8")


def _run(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    env = {**os.environ, "IJ_MAIN": str(_MAIN)}
    proc = subprocess.run(
        ["node", str(f), *argv], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# The audit's extractor (scratchpad desktop_audit/_extract.js, landed here):
# fnSource(name) is the exact text of a top-level function, decl(name) a
# top-level const/let as `var` so a direct eval lands it in the harness scope.
_EXTRACT = r"""
const fs = require("fs");
const src = fs.readFileSync(process.env.IJ_MAIN, "utf8");
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
const LADDER_DECLS = ["RESTART_BACKOFF_MS", "FAST_DEATH_MS", "FAST_DEATHS_TO_VERIFY", "RESTART_CAP_MAX",
  "RESTART_CAP_WINDOW_MS", "DEATHS_DAY_MS", "DEATHS_DAY_TOAST_AT", "_services", "_trayDegraded"];
"""

# --------------------------------------------------------------------------
# D1 — the ladder has a ceiling (scratchpad h1_ladder.js)
# --------------------------------------------------------------------------

_LADDER = """
__EXTRACT__
const path = require("path");
const { EventEmitter } = require("events");
const scenario = process.argv[2];
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null, userDataDir = "X";
const notes = []; const logs = []; const calls = { integrity: 0, corrupt: 0, refreshTray: 0, killed: [] };
let tray = { setToolTip(t) { notes.push("tooltip:" + t); } };
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
function fileLogger() { return (s) => logs.push(String(s).trim()); }
class Notification { constructor(o) { this.o = o; } show() { notes.push("toast:" + this.o.body); } }
function adoptOrReplaceExistingDaemon() { throw new Error("not exercised"); }
function verifyInstallIntegrity() { calls.integrity += 1; return scenario === "damaged" ? { ok: false, checked: 5, missing: ["a.dll"], mismatched: [] } : { ok: true, checked: 5, missing: [], mismatched: [] }; }
function handleCorruptInstall(r) { calls.corrupt += 1; isQuitting = true; }
function refreshTrayMenu() { calls.refreshTray += 1; }
function armDashboardReload() {}
function killChild(child, label, reason) { calls.killed.push(`${label}:${reason}`); child.exitCode = 1; process.nextTick(() => child.emit("close", 1)); }
// tray-menu stubs
const Menu = { buildFromTemplate: (t) => t };
const hotkeyState = { window: "CommandOrControl+Shift+J", spotlight: null };
function accelLabel(a) { return a; }
const IS_PACKAGED = true; let keepRunningPref = null, hwAccelDisabled = false, pendingUpdateInfo = null;
function showMainWindow() {} function toggleSpotlight() {} function reloadUI() {} function openLogsFolder() {}
function setKeepRunningPref() {} function setHwAccelPref() {} function getStartAtLogin() { return false; } function setStartAtLogin() {}
const app = { quit() {} };
// fake timers: restart timers fire at once, delays recorded
const delays = []; const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => { delays.push(ms); fn(); return { fake: true }; };
global.clearTimeout = () => {};
let spawns = 0; const MAX = 40; let keepAlive = false;
function fakeChild() { const c = new EventEmitter(); c.pid = 1000 + spawns; c.exitCode = null; c.signalCode = null; return c; }
for (const d of LADDER_DECLS) eval(decl(d));
for (const f of ["markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop", "restartServicesFromTray", "buildTrayContextMenu"]) eval(fnSource(f));

const spawnFn = () => { spawns += 1; const c = fakeChild(); if (spawns <= MAX && !keepAlive) process.nextTick(() => { c.exitCode = 1; c.emit("close", 1); }); return c; };
startService("dashboard", spawnFn);
realSetTimeout(() => {
  const rec = _services.dashboard;
  const out = {
    scenario, spawns, restarts: rec.restarts, fastDeaths: rec.fastDeaths, capped: !!rec.capped,
    integrityCalls: calls.integrity, corruptCalls: calls.corrupt, refreshTray: calls.refreshTray,
    toasts: notes.filter((n) => n.startsWith("toast:")).map((n) => n.slice(6)),
    tooltips: notes.filter((n) => n.startsWith("tooltip:")).map((n) => n.slice(8)),
    logs, capMax: RESTART_CAP_MAX,
    menuCapped: buildTrayContextMenu().map((i) => i.label),
  };
  if (scenario === "damaged") { process.stdout.write(JSON.stringify(out) + "\\n"); return; }
  // click "Restart Iron Jarvis": counters reset, child respawned and now stays alive
  keepAlive = true;
  restartServicesFromTray();
  out.afterRestart = {
    spawns, restarts: rec.restarts, capped: !!rec.capped, restartTimes: rec.restartTimes.length,
    lastNote: notes[notes.length - 1], menu: buildTrayContextMenu().map((i) => i.label),
  };
  // a live child restarted from the tray is killed with reason=restart and respawned without counting
  restartServicesFromTray();
  realSetTimeout(() => {
    out.afterLiveRestart = { killed: calls.killed, spawns, restarts: rec.restarts, logs: logs.slice(-3) };
    process.stdout.write(JSON.stringify(out) + "\\n");
  }, 20);
}, 50);
"""


def _ladder(scenario: str, tmp_path: Path) -> dict:
    return _run(_LADDER.replace("__EXTRACT__", _EXTRACT), tmp_path, "ladder", scenario)


@requires_node
def test_a_clean_install_caps_the_ladder_toasts_once_more_and_offers_a_tray_restart(tmp_path):
    """The audit's h1 scenario A: a packaged child dies right after every
    spawn. Old code: 40 restarts and counting, one toast at #3, the tooltip
    saying "restarting repeatedly" forever."""
    out = _ladder("clean", tmp_path)
    assert out["integrityCalls"] == 1, "the install must be verified exactly once, at the 3rd fast death"
    assert out["corruptCalls"] == 0, "a clean install must not get the Repair dialog"
    assert out["spawns"] == 1 + out["capMax"] == 11, f"ladder not capped at 10 restarts: {out['spawns']} spawns"
    assert out["capped"] is True
    assert len(out["toasts"]) == 2, f"toast at #3 and once more at the cap: {out['toasts']}"
    assert "no longer being restarted" in out["toasts"][1] and "Restart Iron Jarvis" in out["toasts"][1]
    assert "stopped" in out["tooltips"][-1], out["tooltips"]
    assert any("giving up" in l for l in out["logs"]), "the child's log does not say the supervisor gave up"
    assert any("install verified intact (5 files)" in l for l in out["logs"]), out["logs"]
    assert out["refreshTray"] >= 1 and "Restart Iron Jarvis" in out["menuCapped"], "no tray item while capped"


@requires_node
def test_the_tray_restart_resets_the_counters_respawns_and_hides_itself(tmp_path):
    out = _ladder("clean", tmp_path)
    after = out["afterRestart"]
    assert after["spawns"] == 12, f"tray Restart did not respawn the capped child: {after}"
    assert after["restarts"] == 0 and after["capped"] is False and after["restartTimes"] == 0, after
    assert after["lastNote"] == "tooltip:Iron Jarvis — running", "the tooltip must read running again after Restart"
    assert "Restart Iron Jarvis" not in after["menu"], "the item must disappear once nothing is capped"
    live = out["afterLiveRestart"]
    assert "dashboard:restart" in live["killed"], "a live child restarted from the tray must be killed with reason=restart"
    assert live["spawns"] == 13 and live["restarts"] == 0, f"a manual restart counted as a crash: {live}"
    assert any("restarting on request" in l for l in live["logs"]), live["logs"]


@requires_node
def test_a_damaged_install_gets_the_repair_dialog_instead_of_a_loop(tmp_path):
    out = _ladder("damaged", tmp_path)
    assert out["integrityCalls"] == 1
    assert out["corruptCalls"] == 1, "a damaged install must hand off to handleCorruptInstall (the Repair dialog)"
    assert out["spawns"] == 3, f"no restart after the damaged verdict: {out['spawns']} spawns"
    assert any("install is damaged" in l for l in out["logs"]), out["logs"]


# --------------------------------------------------------------------------
# D1 review — tray Restart with a backoff timer still pending
# (scratchpad r_double_spawn.js; real timers)
# --------------------------------------------------------------------------

_DOUBLE_SPAWN = """
__EXTRACT__
const path = require("path");
const { EventEmitter } = require("events");
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null, userDataDir = "X";
const logs = [];
let tray = { setToolTip() {} };
function desktopLog(l, ...a) { logs.push(a.join(" ")); }
function fileLogger() { return (s) => logs.push(String(s).trim()); }
class Notification { constructor(o) { this.o = o; } show() {} }
function adoptOrReplaceExistingDaemon() { throw new Error("no"); }
function verifyInstallIntegrity() { return { ok: true, checked: 1, missing: [], mismatched: [] }; }
function handleCorruptInstall() {}
function refreshTrayMenu() {}
function armDashboardReload() {}
function killChild(child, label, reason) { child.exitCode = 1; process.nextTick(() => child.emit("close", 1)); }
for (const d of LADDER_DECLS) eval(decl(d));
for (const f of ["markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop", "restartServicesFromTray"]) eval(fnSource(f));
let dashSpawns = 0;
const dashSpawn = () => { dashSpawns += 1; const c = new EventEmitter(); c.pid = 100 + dashSpawns; c.exitCode = null; c.signalCode = null; return c; };
// daemon: capped, dead
_services.daemon = { spawnFn: () => { const c = new EventEmitter(); c.pid = 1; c.exitCode = null; c.signalCode = null; return c; }, restarts: 10, lastStart: Date.now(), capped: true, restartTimes: [] };
daemonProc = { pid: 1, exitCode: 1, signalCode: null };
// dashboard: first spawn dies at once -> restart #1 scheduled in 1000 ms
const first = startService("dashboard", dashSpawn);
first.exitCode = 1; first.emit("close", 1);
const pendingAtClick = !!_services.dashboard.restartTimer;
setTimeout(() => {
  restartServicesFromTray(); // the 1000 ms backoff timer is still pending
  const spawnsAtClick = dashSpawns;
  setTimeout(() => {
    process.stdout.write(JSON.stringify({ pendingAtClick, spawnsAtClick, dashSpawns, timerAfter: !!_services.dashboard.restartTimer, liveIsSupervised: dashboardProc && dashboardProc.pid === 100 + dashSpawns }) + "\\n");
  }, 1300);
}, 100);
"""


@requires_node
def test_a_tray_restart_cancels_a_pending_backoff_so_only_one_child_is_spawned(tmp_path):
    """Reviewer probe: the daemon is capped (item visible) while the dashboard
    sits in its backoff. A click spawned the dashboard now AND the timer
    spawned a second one later — two processes on :8788, the ladder holding
    the dead loser. Expect exactly initial + one restart."""
    out = _run(_DOUBLE_SPAWN.replace("__EXTRACT__", _EXTRACT), tmp_path, "double_spawn")
    assert out["pendingAtClick"] is True, "precondition: a backoff timer was pending on the record"
    assert out["spawnsAtClick"] == 2, "the click must spawn the dead child at once"
    assert out["dashSpawns"] == 2, f"double spawn: {out['dashSpawns']} dashboard spawns (expected 2)"
    assert out["timerAfter"] is False, "the cancelled timer must be cleared from the record"
    assert out["liveIsSupervised"] is True, "dashboardProc must point at the child that is running"


# --------------------------------------------------------------------------
# D3 / D4 — a periodic crash toasts in the 24 h window and the tooltip resets
# (scratchpad h1b_periodic.js)
# --------------------------------------------------------------------------

_PERIODIC = """
__EXTRACT__
const path = require("path");
const { EventEmitter } = require("events");
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null, userDataDir = "X";
const notes = []; const logs = []; const calls = { integrity: 0, corrupt: 0 };
let tray = { setToolTip(t) { notes.push("tooltip:" + t); } };
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
function fileLogger() { return (s) => logs.push(String(s).trim()); }
class Notification { constructor(o) { this.o = o; } show() { notes.push("toast:" + this.o.body); } }
function adoptOrReplaceExistingDaemon() { throw new Error("not exercised"); }
function verifyInstallIntegrity() { calls.integrity += 1; return { ok: true, checked: 5, missing: [], mismatched: [] }; }
function handleCorruptInstall() { calls.corrupt += 1; }
function refreshTrayMenu() {}
function armDashboardReload() {}
function killChild() {}
let now = 1_000_000; Date.now = () => now;
const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => { fn(); return { fake: true }; };
global.clearTimeout = () => {};
let spawns = 0; const MAX = 30;
function fakeChild() { const c = new EventEmitter(); c.pid = 1000 + spawns; c.exitCode = null; c.signalCode = null; return c; }
for (const d of LADDER_DECLS) eval(decl(d));
for (const f of ["markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop"]) eval(fnSource(f));
const spawnFn = () => { spawns += 1; const c = fakeChild(); if (spawns <= MAX) process.nextTick(() => { now += 6 * 60 * 1000; c.exitCode = 1; c.emit("close", 1); }); return c; };
startService("daemon", spawnFn);
realSetTimeout(() => {
  const rec = _services.daemon;
  process.stdout.write(JSON.stringify({
    spawns, max: MAX, restarts: rec.restarts, deaths24h: rec.deaths.length, integrityCalls: calls.integrity, capped: !!rec.capped,
    toasts: notes.filter((n) => n.startsWith("toast:")).map((n) => n.slice(6)),
    tooltips: notes.filter((n) => n.startsWith("tooltip:")).map((n) => n.slice(8)),
  }) + "\\n");
}, 50);
"""


@requires_node
def test_a_child_dying_every_six_minutes_is_restarted_but_the_user_is_told_at_three_in_a_day(tmp_path):
    out = _run(_PERIODIC.replace("__EXTRACT__", _EXTRACT), tmp_path, "periodic")
    assert out["spawns"] == out["max"] + 1, "a periodic crash is not a cap case — it is restarted every time"
    assert out["capped"] is False
    assert out["restarts"] <= 1, "precondition: the 5-minute rule resets the ladder before every increment"
    assert len(out["toasts"]) == 1, f"the 24 h window must toast exactly once: {out['toasts']}"
    assert "3 times in the last 24 hours" in out["toasts"][0], out["toasts"]
    assert out["integrityCalls"] == 0, "slow deaths never trigger the integrity check"


@requires_node
def test_the_tooltip_is_degraded_by_the_toast_and_reset_by_the_next_healthy_run(tmp_path):
    out = _run(_PERIODIC.replace("__EXTRACT__", _EXTRACT), tmp_path, "periodic_tooltip")
    assert out["tooltips"] and "restarting repeatedly" in out["tooltips"][0], out["tooltips"]
    assert out["tooltips"][-1] == "Iron Jarvis — running", f"D4: the healthy-reset branch must put the tooltip back: {out['tooltips']}"


# --------------------------------------------------------------------------
# D5 / D4 — a real child: stamped chunks, the killed line, the watchdog reset
# (scratchpad h2_taskkill_and_cwd.js, parts d + e)
# --------------------------------------------------------------------------

_KILL = """
__EXTRACT__
const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fileLines = {};
const fileLogger = (label) => (s) => (fileLines[label] = fileLines[label] || []).push(String(s));
const desktopLogs = [];
const desktopLog = (l, ...a) => desktopLogs.push(a.join(" "));
console.log = () => {};
eval(fnSource("spawnChild"));
eval(fnSource("killChild"));
(async () => {
  const child = spawnChild("daemon", process.execPath, ["-e", "console.log('hello from child'); setInterval(()=>{},1000)"], process.cwd(), {}, false);
  await new Promise((r) => setTimeout(r, 700));
  const killed = new Promise((r) => child.on("close", r));
  killChild(child, "daemon", "watchdog");
  await killed;
  await new Promise((r) => setTimeout(r, 50));
  // shutdown(reason) forwards the reason; process.on("exit") passes a code, which normalises to quit
  const kills = [];
  let shuttingDown = false, daemonProc = { exitCode: null, signalCode: null, pid: 1 }, dashboardProc = null;
  eval(fnSource("shutdown").replace("killChild(daemonProc", "kills.push([\\"daemon\\", why]); 0 && killChild(daemonProc").replace("killChild(dashboardProc", "kills.push([\\"dashboard\\", why]); 0 && killChild(dashboardProc"));
  shutdown("update"); shuttingDown = false; shutdown(0);
  // D4: the watchdog's healthy branch resets the tooltip after an exhaustion warning
  const notes = [];
  let tray = { setToolTip: (t) => notes.push(t) };
  let isQuitting = false, updateInstallInFlight = false;
  const userDataDir = "X";
  const Notification = class { show() {} };
  eval(decl("_trayDegraded")); eval(decl("_services"));
  for (const d of ["DAEMON_WATCHDOG_MS", "DAEMON_WATCHDOG_PROBE_TIMEOUT_MS", "DAEMON_WATCHDOG_MISS_LIMIT", "DAEMON_WATCHDOG_BREAKER_WINDOW_MS", "DAEMON_WATCHDOG_BREAKER_MAX", "_dwMissed", "_dwPid", "_dwKills", "_dwBreakerTripped", "_dwProbeInFlight", "_dwBooting", "STARTUP_TIMEOUT_MS"]) eval(decl(d).replace("IS_PACKAGED ? 90000 : 30000", "90000"));
  for (const f of ["markTrayDegraded", "markTrayHealthy", "daemonWatchdogPaused", "daemonWatchdogTick", "notifyWatchdogExhausted"]) eval(fnSource(f));
  const probeDaemonHealth = () => Promise.resolve({ version: "x" });
  notifyWatchdogExhausted();
  const afterExhausted = notes[notes.length - 1];
  daemonProc = { pid: 7, exitCode: null, signalCode: null };
  shuttingDown = false;
  daemonWatchdogTick();
  await new Promise((r) => setTimeout(r, 20));
  process.stdout.write(JSON.stringify({ lines: fileLines.daemon || [], kills, afterExhausted, afterHealthy: notes[notes.length - 1] }) + "\\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ")


@requires_node
def test_every_child_output_chunk_is_stamped_and_a_kill_says_pid_and_reason(tmp_path):
    out = _run(_KILL.replace("__EXTRACT__", _EXTRACT), tmp_path, "kill")
    lines = out["lines"]
    chunk = next((l for l in lines if not l.startswith("[main]") and "hello from child" in l), None)
    assert chunk is not None and _ISO.match(chunk), f"child output chunk is not ISO-stamped: {lines}"
    kill_line = next((l for l in lines if re.search(r"killed pid=\d+ reason=watchdog", l)), None)
    assert kill_line is not None, f"killChild wrote no '[main] killed pid=… reason=watchdog' line: {lines}"
    assert re.search(r"\[main\] \d{4}-\d{2}-\d{2}T\S+ killed", kill_line), kill_line
    assert any(re.search(r"\[main\] \d{4}-\d{2}-\d{2}T\S+ exited \(code=", l) for l in lines), lines


@requires_node
def test_shutdown_forwards_its_reason_and_the_exit_hook_normalises_to_quit(tmp_path):
    out = _run(_KILL.replace("__EXTRACT__", _EXTRACT), tmp_path, "kill_reason")
    assert out["kills"][0] == ["daemon", "update"] and out["kills"][1] == ["dashboard", "update"], out["kills"]
    assert out["kills"][2] == ["daemon", "quit"], f"process.on('exit') passes a code, not a reason: {out['kills']}"


@requires_node
def test_the_watchdogs_next_healthy_probe_puts_the_tooltip_back_to_running(tmp_path):
    out = _run(_KILL.replace("__EXTRACT__", _EXTRACT), tmp_path, "kill_tooltip")
    assert "keeps stalling" in out["afterExhausted"], out
    assert out["afterHealthy"] == "Iron Jarvis — running", f"D4: the watchdog's healthy branch did not reset the tooltip: {out}"


def test_every_kill_site_names_its_reason():
    src = _src()
    assert 'killChild(daemonProc, "daemon", "watchdog")' in src, "the watchdog kill does not say reason=watchdog"
    assert 'shutdown("update")' in src, "applyPendingUpdate does not say reason=update"
    assert 'killChild(child, label, "restart")' in src, "the tray Restart does not say reason=restart"
    assert re.search(r'function killChild\(child, label, reason = "quit"\)', src), "killChild lost its reason parameter"


# --------------------------------------------------------------------------
# D1 — the dashboard reload wait loops and is re-armed by every spawn
# (scratchpad h3_reload.js)
# --------------------------------------------------------------------------

_RELOAD = """
__EXTRACT__
const { EventEmitter } = require("events");
const DASHBOARD_URL = "http://localhost:8788";
const DASHBOARD_ORIGINS = new Set(["http://localhost:8788", "http://127.0.0.1:8788"]);
function isDashboardUrl(u) { try { return DASHBOARD_ORIGINS.has(new URL(String(u)).origin); } catch { return false; } }
const logs = []; function desktopLog(l, ...a) { logs.push(a.join(" ")); }
function fileLogger() { return () => {}; }
let waits = 0, loads = 0, dashboardUp = false;
// stand-in for waitForDashboard(60000, 500): rejects after a short budget while the dashboard is down
function waitForDashboard() { waits += 1; return new Promise((res, rej) => setTimeout(() => (dashboardUp ? res() : rej(new Error("dashboard did not answer"))), 5)); }
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null;
for (const d of ["_dashboardReloadPending", "_dashboardLoadFailed", "_dashboardReloadWc", ...LADDER_DECLS]) eval(decl(d));
for (const f of ["armDashboardReload", "installDashboardReloadOnFailure", "startService"]) eval(fnSource(f));
const wc = new EventEmitter(); wc.isDestroyed = () => false; wc.loadURL = () => { loads += 1; };
installDashboardReloadOnFailure({ webContents: wc });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const out = {};
  wc.emit("did-fail-load", null, -102 /* ERR_CONNECTION_REFUSED */, "refused", DASHBOARD_URL + "/chat", true);
  await sleep(60);
  out.down = { waits, loads, pending: _dashboardReloadPending, failed: _dashboardLoadFailed };
  // the ladder gives up on the dashboard: the loop pauses
  _services.dashboard = { capped: true, restarts: 0, lastStart: 0 };
  await sleep(30);
  const w1 = waits; await sleep(30);
  out.capped = { waitsStill: waits === w1, pending: _dashboardReloadPending };
  // tray Restart: counters reset and startService spawns the dashboard -> re-armed
  _services.dashboard.capped = false;
  const fake = new EventEmitter(); fake.pid = 1; fake.exitCode = null; fake.signalCode = null;
  startService("dashboard", () => fake);
  out.respawn = { rearmed: waits === w1 + 1, pending: _dashboardReloadPending };
  // the dashboard answers: the window is loaded, the flag clears, the loop stops
  dashboardUp = true;
  await sleep(30);
  const w2 = waits; await sleep(30);
  out.up = { loads, failed: _dashboardLoadFailed, waitsStill: waits === w2 };
  // a later did-fail-load starts a fresh cycle
  dashboardUp = false;
  wc.emit("did-fail-load", null, -102, "refused", DASHBOARD_URL, true);
  await sleep(30);
  out.again = { newWaits: waits > w2 };
  process.stdout.write(JSON.stringify(out) + "\\n");
  process.exit(0); // the loop is (rightly) still waiting for the dashboard
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


@requires_node
def test_the_reload_wait_loops_on_the_error_page_pauses_when_capped_and_rearms_on_spawn(tmp_path):
    out = _run(_RELOAD.replace("__EXTRACT__", _EXTRACT), tmp_path, "reload")
    assert out["down"]["waits"] > 3, f"the wait must re-arm itself while the window sits on the error page (old code: 1): {out['down']}"
    assert out["down"]["loads"] == 0, "nothing may load while the dashboard is down"
    assert out["capped"] == {"waitsStill": True, "pending": False}, f"the loop must pause while the dashboard is capped: {out['capped']}"
    assert out["respawn"] == {"rearmed": True, "pending": True}, f"a dashboard spawn must re-arm the wait: {out['respawn']}"
    assert out["up"] == {"loads": 1, "failed": False, "waitsStill": True}, f"once the dashboard answers: one loadURL, then quiet: {out['up']}"
    assert out["again"]["newWaits"] is True, "a later failure must start a fresh cycle"


# --------------------------------------------------------------------------
# Docs — the Handbook says what the tray does (the Guide knows what the docs know)
# --------------------------------------------------------------------------


def test_the_handbook_troubleshooting_bullet_names_the_cap_and_the_tray_restart():
    hb = (_ROOT / "docs" / "HANDBOOK.md").read_text(encoding="utf-8")
    m = re.search(r'\*\*"Daemon offline"\*\*(.{0,1600}?)\n- \*\*', hb, re.S)
    assert m, "the 'Daemon offline' troubleshooting bullet moved"
    bullet = m.group(1)
    assert "Restart Iron Jarvis" in bullet, "the tray Restart item is not documented"
    assert "10" in bullet and "15 minutes" in bullet, "the cap is not documented"
    assert "reason=quit|update|watchdog|restart" in bullet, "the killed line is not documented"
