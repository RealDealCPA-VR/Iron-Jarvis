"""v1.312.0 — the desktop "Daemon offline" banner says the truth when the shell
has STOPPED restarting the daemon, and offers a way forward from the window
(``desktop/main.js`` + ``desktop/preload.js``; wave 4 finding
desktop-offline-banner-lies-when-capped).

Before: after RESTART_CAP_MAX restarts in RESTART_CAP_WINDOW_MS the supervisor
set ``rec.capped`` and stopped, but ``shellState()`` exposed only hotkeys, so the
page kept saying "restarting its local service…" with a Retry that only
re-probes. The restart function existed (``restartServicesFromTray``) but the
page could not reach it — and it must not: it also kills and respawns a LIVE
dashboard, which reloads the very page the user pressed the button on.

Contract pinned here (the implementer follows these names):

* ``shellState().services.daemon`` = ``{capped, restarts, lastExit, damaged}``
  read from the supervisor record ``_services.daemon``. ``restarts`` is the
  ladder counter (``rec.restarts``), ``lastExit`` the exit code of the last
  unexpected death (``null`` before any), ``damaged`` true once the integrity
  check found a damaged install. No record yet -> ``{capped: false,
  restarts: 0, lastExit: null, damaged: false}``.
* ``restartDaemonFromShell()`` — a top-level main.js function that resets the
  DAEMON's ladder only, cancels its pending backoff, respawns it (a capped or
  dead daemon) and never touches the dashboard child. Returns ``true`` when it
  started a (re)spawn.
* ``ipcMain.handle("shell:restartDaemon", …)`` — sender-checked with
  ``isTrustedDashboardSender`` as its first statement, resolves ``null`` when
  refused, calls ``restartDaemonFromShell`` (never ``restartServicesFromTray``).
* preload: ``shell.restartDaemon: () => ipcRenderer.invoke("shell:restartDaemon")``.

House idiom (test_desktop_supervisor_v1229.py): the shipped function text is
lifted out of main.js verbatim with the brace-matching extractor and driven
under node against stubs.
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

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _read(p: Path) -> str:
    # CRLF-tolerant at the reader: this checkout is autocrlf.
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


# Same extractor as test_desktop_supervisor_v1229.py (kept local so this file
# stands alone). fnSource(name) = exact text of a top-level function; decl(name)
# = a top-level const/let as `var` so a direct eval lands it in harness scope.
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
const LADDER_DECLS = ["RESTART_BACKOFF_MS", "FAST_DEATH_MS", "FAST_DEATHS_TO_VERIFY", "RESTART_CAP_MAX",
  "RESTART_CAP_WINDOW_MS", "DEATHS_DAY_MS", "DEATHS_DAY_TOAST_AT", "_services", "_trayDegraded"];
for (const d of ["WINDOWS_SHUTDOWN_EXIT_CODES", "WINDOWS_SHUTDOWN_GRACE_MS", "windowsSessionEnding"]) eval(decl(d));
eval(fnSource("isWindowsShutdownExit"));
"""

# Shared stubs for the supervisor + shellState + tray menu.
_STUBS = r"""
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
function handleCorruptInstall(r) { calls.corrupt += 1; }
function refreshTrayMenu() { calls.refreshTray += 1; }
function armDashboardReload() {}
function killChild(child, label, reason) { calls.killed.push(`${label}:${reason}`); child.exitCode = 1; process.nextTick(() => child.emit("close", 1)); }
const Menu = { buildFromTemplate: (t) => t };
const hotkeyState = { window: "CommandOrControl+Shift+J", spotlight: null };
const HOTKEY = "CommandOrControl+Shift+J", SPOTLIGHT_HOTKEY = "CommandOrControl+Shift+Space";
const IS_PACKAGED = true; let keepRunningPref = null, hwAccelDisabled = false, pendingUpdateInfo = null;
function showMainWindow() {} function toggleSpotlight() {} function reloadUI() {} function openLogsFolder() {}
function setKeepRunningPref() {} function setHwAccelPref() {} function getStartAtLogin() { return false; } function setStartAtLogin() {}
const app = { quit() {} };
const realSetTimeout = setTimeout;
for (const d of LADDER_DECLS) eval(decl(d));
// Review round: every name serviceState / the restart can reach is declared,
// so a reordered `&&` can never turn into a ReferenceError instead of a real
// assertion (STARTUP_TIMEOUT_MS was read only past a short-circuit before),
// and the watchdog's breaker state the restart clears is the shipped decl.
for (const d of ["STARTUP_TIMEOUT_MS", "_dwKills", "_dwBreakerTripped"]) eval(decl(d));
for (const f of ["markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop", "restartServicesFromTray",
                 "buildTrayContextMenu", "accelLabel", "shellState"]) eval(fnSource(f));
// shellState may read the record inline or through ONE optional helper,
// `serviceState(label)` (the only helper name this harness lifts).
try { eval(fnSource("serviceState")); } catch (e) { if (!/has no function/.test(String(e))) throw e; }
function lift(name) { try { eval(fnSource(name)); return eval(name); } catch (e) { return null; } }
function fakeChild(pid) { const c = new EventEmitter(); c.pid = pid; c.exitCode = null; c.signalCode = null; return c; }
"""


# --------------------------------------------------------------------------
# shellState().services.daemon — the page can read what the supervisor knows
# --------------------------------------------------------------------------

_STATE = r"""
__EXTRACT__
__STUBS__
// Timers: "capped"/"damaged" fire backoffs at once (run the ladder to its end);
// "restarting" never fires them (the ladder is mid-way, a backoff pending).
const fireTimers = scenario !== "restarting";
global.setTimeout = (fn, ms) => { if (fireTimers) fn(); return { fake: true }; };
global.clearTimeout = () => {};
const out = { scenario };
out.before = (shellState().services || {}).daemon ?? "MISSING";
let spawns = 0;
const spawnFn = () => {
  spawns += 1; const c = fakeChild(1000 + spawns);
  if (spawns <= 40) process.nextTick(() => { c.exitCode = 3; c.emit("close", 3); });
  return c;
};
if (scenario !== "none") startService("daemon", spawnFn);
realSetTimeout(() => {
  const s = shellState();
  out.state = s;
  out.daemon = (s.services || {}).daemon ?? "MISSING";
  out.rec = _services.daemon ? { restarts: _services.daemon.restarts, capped: !!_services.daemon.capped } : null;
  out.capMax = RESTART_CAP_MAX; out.spawns = spawns; out.corrupt = calls.corrupt;
  process.stdout.write(JSON.stringify(out) + "\n");
}, 60);
"""


def _state(scenario: str, tmp_path: Path) -> dict:
    script = _STATE.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    return _run(script, tmp_path, f"state_{scenario}", scenario)


@requires_node
def test_shell_state_reports_a_daemon_that_is_still_being_restarted(tmp_path):
    """Mid-ladder: one death, a backoff pending. The page must be able to say
    "restarting (attempt 1)" — and the exit code it died with."""
    out = _state("restarting", tmp_path)
    # Anti-vacuity: the harness really drove the ladder.
    assert out["rec"] == {"restarts": 1, "capped": False}, out
    assert out["spawns"] == 1, out
    d = out["daemon"]
    assert d != "MISSING", f"shellState() has no services.daemon: {out['state']}"
    assert d["capped"] is False and d["restarts"] == 1, d
    assert d["lastExit"] == 3, f"lastExit must be the exit code of the last death: {d}"
    assert d["damaged"] is False, d
    # A backoff is pending: this restart really is under way (review round).
    assert d["restarting"] is True, d
    # What was already there stays (PowerTips reads it).
    assert out["state"]["hotkeys"]["window"] == "Ctrl+Shift+J", out["state"]


@requires_node
def test_shell_state_reports_a_daemon_the_supervisor_gave_up_on(tmp_path):
    """The bug: after the cap the supervisor stops, but the window kept saying
    "restarting". shellState must say capped, with the crash count."""
    out = _state("capped", tmp_path)
    assert out["rec"]["capped"] is True, f"harness did not reach the cap: {out}"
    d = out["daemon"]
    assert d != "MISSING", f"shellState() has no services.daemon: {out['state']}"
    assert d["capped"] is True, d
    assert d["restarts"] == out["rec"]["restarts"] and d["restarts"] >= out["capMax"], d
    assert d["lastExit"] == 3 and d["damaged"] is False, d


@requires_node
def test_shell_state_reports_a_damaged_install(tmp_path):
    out = _state("damaged", tmp_path)
    assert out["corrupt"] == 1, f"harness did not reach the damaged verdict: {out}"
    d = out["daemon"]
    assert d != "MISSING", f"shellState() has no services.daemon: {out['state']}"
    assert d["damaged"] is True, f"the integrity verdict is not reported: {d}"


@requires_node
def test_shell_state_has_safe_defaults_before_the_daemon_was_ever_started(tmp_path):
    out = _state("none", tmp_path)
    assert out["before"] != "MISSING", "shellState() has no services.daemon"
    # Review round (v1.312.0): the record also carries `restarting` and the
    # capped numbers; with no record they are all "nothing happening".
    assert out["daemon"] == {
        "capped": False, "restarts": 0, "lastExit": None, "damaged": False,
        "restarting": False, "cappedCrashes": None, "cappedWindowMin": None,
        # Second review round: the watchdog's breaker (`stalled`).
        "stalled": False,
    }, out["daemon"]


# --------------------------------------------------------------------------
# restartDaemonFromShell — respawns ONLY the daemon
# --------------------------------------------------------------------------

_RESTART = r"""
__EXTRACT__
__STUBS__
global.setTimeout = (fn, ms) => { fn(); return { fake: true }; };
global.clearTimeout = () => {};
const which = process.argv[3]; // "daemon-only" | "tray" (control)
let dSpawns = 0, bSpawns = 0;
let daemonDies = true;
const daemonSpawn = () => {
  dSpawns += 1; const c = fakeChild(2000 + dSpawns);
  if (daemonDies && dSpawns <= 40) process.nextTick(() => { c.exitCode = 3; c.emit("close", 3); });
  return c;
};
const dashSpawn = () => { bSpawns += 1; return fakeChild(3000 + bSpawns); }; // stays alive
startService("dashboard", dashSpawn);
startService("daemon", daemonSpawn);
realSetTimeout(() => {
  const out = { which, cappedBefore: !!_services.daemon.capped, dSpawnsBefore: dSpawns, bSpawnsBefore: bSpawns,
                menuBefore: buildTrayContextMenu().map((i) => i.label) };
  daemonDies = false; // the fresh daemon stays up
  const fn = which === "tray" ? restartServicesFromTray : lift("restartDaemonFromShell");
  out.lifted = typeof fn === "function";
  if (out.lifted) {
    out.ret = fn() ?? null;
  }
  realSetTimeout(() => {
    const rec = _services.daemon;
    Object.assign(out, {
      dSpawns, bSpawns, killed: calls.killed,
      daemon: { restarts: rec.restarts, capped: !!rec.capped, restartTimes: (rec.restartTimes || []).length },
      menuAfter: buildTrayContextMenu().map((i) => i.label),
      lastTooltip: notes.filter((n) => n.startsWith("tooltip:")).pop() || null,
    });
    process.stdout.write(JSON.stringify(out) + "\n");
  }, 30);
}, 60);
"""


def _restart(which: str, tmp_path: Path) -> dict:
    script = _RESTART.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    return _run(script, tmp_path, f"restart_{which}", "clean", which)


@requires_node
def test_control_the_tray_restart_kills_the_live_dashboard(tmp_path):
    """Anti-vacuity control for the next test: the harness DOES see a live
    dashboard being killed and respawned — which is exactly why the page's
    button must not reuse restartServicesFromTray (it would reload the page
    the user pressed it on)."""
    out = _restart("tray", tmp_path)
    assert out["cappedBefore"] is True, out
    assert "dashboard:restart" in out["killed"], out
    assert out["bSpawns"] == out["bSpawnsBefore"] + 1, out


@requires_node
def test_restart_from_the_window_respawns_only_the_capped_daemon(tmp_path):
    out = _restart("daemon-only", tmp_path)
    assert out["cappedBefore"] is True and "Restart Iron Jarvis" in out["menuBefore"], f"harness setup: {out}"
    assert out["lifted"], "main.js has no top-level function restartDaemonFromShell"
    assert out["ret"] is True, f"restartDaemonFromShell must return true when it respawned: {out['ret']!r}"
    assert out["dSpawns"] == out["dSpawnsBefore"] + 1, f"the capped daemon was not respawned exactly once: {out}"
    # The live dashboard is never touched.
    assert not any(k.startswith("dashboard:") for k in out["killed"]), out["killed"]
    assert out["bSpawns"] == out["bSpawnsBefore"] == 1, out
    # The daemon's ladder starts over and the tray stops offering a Restart.
    assert out["daemon"] == {"restarts": 0, "capped": False, "restartTimes": 0}, out["daemon"]
    assert "Restart Iron Jarvis" not in out["menuAfter"], out["menuAfter"]
    assert out["lastTooltip"] == "tooltip:Iron Jarvis — running", out["lastTooltip"]


_PENDING = r"""
__EXTRACT__
__STUBS__
// REAL timers: one death leaves a 1000 ms backoff pending; a restart pressed
// in the window must cancel it, or two daemons race for :8787.
let dSpawns = 0;
const daemonSpawn = () => {
  dSpawns += 1; const c = fakeChild(4000 + dSpawns);
  if (dSpawns === 1) process.nextTick(() => { c.exitCode = 3; c.emit("close", 3); });
  return c;
};
startService("daemon", daemonSpawn);
realSetTimeout(() => {
  const out = { pending: !!_services.daemon.restartTimer, dSpawnsBefore: dSpawns };
  const fn = lift("restartDaemonFromShell");
  out.lifted = typeof fn === "function";
  if (out.lifted) fn();
  out.dSpawnsNow = dSpawns;
  realSetTimeout(() => { out.dSpawns = dSpawns; process.stdout.write(JSON.stringify(out) + "\n"); }, 1500);
}, 30);
"""


@requires_node
def test_restart_from_the_window_cancels_a_pending_backoff(tmp_path):
    script = _PENDING.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    out = _run(script, tmp_path, "pending", "clean")
    assert out["pending"] is True and out["dSpawnsBefore"] == 1, f"harness setup: {out}"
    assert out["lifted"], "main.js has no top-level function restartDaemonFromShell"
    assert out["dSpawnsNow"] == 2, f"the restart did not spawn at once: {out}"
    assert out["dSpawns"] == 2, f"the old backoff still fired — two daemons: {out}"


# --------------------------------------------------------------------------
# Source pins: the IPC is sender-checked; preload exposes it
# --------------------------------------------------------------------------


def _handler(src: str, channel: str) -> str | None:
    """The text of `ipcMain.handle("<channel>", …)` up to its closing `});`."""
    m = re.search(r'ipcMain\.handle\(\s*["\']' + re.escape(channel) + r'["\']', src)
    if not m:
        return None
    end = src.find("\n  });", m.end())
    return src[m.start() : end if end != -1 else len(src)]


def _guard_first(body: str) -> bool:
    """The handler's FIRST statement refuses an untrusted sender with null."""
    after_arrow = body.split("=>", 1)[1].lstrip()
    after_brace = after_arrow[1:].lstrip() if after_arrow.startswith("{") else ""
    return bool(
        re.match(r"if\s*\(\s*!\s*isTrustedDashboardSender\(\s*event\s*\)\s*\)\s*return\s+null\s*;", after_brace)
    )


def test_pin_helper_controls():
    """Anti-vacuity: the pin accepts the existing sender-checked openLogs
    handler and rejects a handler with the check missing or after the call."""
    src = _read(_MAIN)
    good = _handler(src, "shell:openLogs")
    assert good and _guard_first(good)
    unguarded = '  ipcMain.handle("shell:restartDaemon", (event) => {\n    return restartDaemonFromShell();\n  });'
    assert not _guard_first(_handler(unguarded + "\n", "shell:restartDaemon"))
    late = (
        '  ipcMain.handle("shell:restartDaemon", (event) => {\n    restartDaemonFromShell();\n'
        "    if (!isTrustedDashboardSender(event)) return null;\n  });"
    )
    assert not _guard_first(_handler(late + "\n", "shell:restartDaemon"))


def test_restart_daemon_ipc_is_sender_checked_and_daemon_only():
    src = _read(_MAIN)
    body = _handler(src, "shell:restartDaemon")
    assert body, 'main.js registers no ipcMain.handle("shell:restartDaemon", …)'
    assert _guard_first(body), f"the handler must refuse an untrusted sender FIRST and resolve null:\n{body}"
    assert "restartDaemonFromShell(" in body, body
    assert "restartServicesFromTray" not in body, "the page's button must never restart the live dashboard"


def test_preload_exposes_restart_daemon_on_the_shell_bridge():
    src = _read(_PRELOAD)
    # Control: the existing openLogs entry matches the same shape.
    assert re.search(r'openLogs:\s*\(\)\s*=>\s*ipcRenderer\.invoke\(\s*"shell:openLogs"\s*\)', src)
    assert re.search(
        r'restartDaemon:\s*\(\)\s*=>\s*ipcRenderer\.invoke\(\s*"shell:restartDaemon"\s*\)', src
    ), "preload's shell bridge has no restartDaemon"


# --------------------------------------------------------------------------
# Review round (v1.312.0): the banner's numbers must be true
# --------------------------------------------------------------------------
# A controllable clock, and backoffs that fire on the next turn of the loop
# (not synchronously, so `rec.restartTimer` is cleared by the timer itself, as
# in the app). STARTUP_TIMEOUT_MS is lifted for serviceState's boot allowance.

_CLOCK = r"""
__EXTRACT__
__STUBS__
eval(decl("STARTUP_TIMEOUT_MS"));
let clock = 1.7e12;
Date.now = () => clock;
const realSetImmediate = setImmediate;
global.setTimeout = (fn, ms) => { const h = { fake: true }; realSetImmediate(fn); return h; };
global.clearTimeout = () => {};
"""

_RESET_THEN_CAP = _CLOCK + r"""
// 9 fast deaths, then a run over 5 minutes (the ladder counter resets), then
// deaths until the 15-minute window trips the cap.
let spawns = 0;
const spawnFn = () => {
  spawns += 1; const c = fakeChild(5000 + spawns);
  if (spawns <= 40) process.nextTick(() => {
    clock += spawns === 10 ? 6 * 60 * 1000 : 1000;
    c.exitCode = 3; c.emit("close", 3);
  });
  return c;
};
startService("daemon", spawnFn);
realSetTimeout(() => {
  const rec = _services.daemon;
  const toast = notes.filter((n) => n.startsWith("toast:") && n.includes("no longer being restarted")).pop() || null;
  process.stdout.write(JSON.stringify({ spawns, rec: { restarts: rec.restarts, capped: !!rec.capped },
    daemon: shellState().services.daemon, toast, capMax: RESTART_CAP_MAX,
    windowMin: Math.round(RESTART_CAP_WINDOW_MS / 60000) }) + "\n");
}, 200);
"""


@requires_node
def test_capped_count_is_the_window_count_not_the_reset_ladder_counter(tmp_path):
    """Reviewer's case: the ladder counter reset mid-window, so `restarts` is 2
    at the cap. The window must report the toast's numbers instead."""
    script = _RESET_THEN_CAP.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    out = _run(script, tmp_path, "reset_then_cap", "clean")
    # Anti-vacuity: the harness reached the cap AFTER the counter reset.
    assert out["rec"] == {"restarts": 2, "capped": True}, out
    d = out["daemon"]
    assert d["capped"] is True and d["restarts"] == 2, d
    assert d["cappedCrashes"] == out["capMax"], f"the cap count must be the window's, not rec.restarts: {d}"
    assert d["cappedWindowMin"] == out["windowMin"] == 15, d
    assert d["restarting"] is False, d
    # The window and the tray's toast say the same numbers.
    assert out["toast"] and f"crashed {d['cappedCrashes']} times in {d['cappedWindowMin']} minutes" in out["toast"], out


_STALE = _CLOCK + r"""
// Two deaths, then a daemon that stays up. Read the state while its backoff
// is pending, right after the spawn, and hours later (up but not answering).
let spawns = 0;
const spawnFn = () => {
  spawns += 1; const c = fakeChild(6000 + spawns);
  if (spawns <= 2) process.nextTick(() => { c.exitCode = 3; c.emit("close", 3); });
  return c;
};
const out = {};
startService("daemon", spawnFn);
realSetTimeout(() => {
  out.fresh = shellState().services.daemon;
  clock += 3 * 60 * 60 * 1000;
  out.hoursLater = shellState().services.daemon;
  out.spawns = spawns;
  out.alive = daemonProc && daemonProc.exitCode === null;
  // Restart from the window: the fresh spawn is booting again.
  lift("restartDaemonFromShell")();
  realSetTimeout(() => {
    out.afterRestart = shellState().services.daemon;
    process.stdout.write(JSON.stringify(out) + "\n");
  }, 30);
}, 100);
"""


@requires_node
def test_a_live_daemon_with_a_stale_counter_is_not_restarting(tmp_path):
    """Reviewer's case: rec.restarts outlives the outage it counted. A daemon
    that is up (past its boot allowance) is NOT on "restart attempt N"."""
    script = _STALE.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    out = _run(script, tmp_path, "stale", "clean")
    assert out["spawns"] == 3 and out["alive"] is True, f"harness setup: {out}"
    # Control: just respawned and still inside the boot allowance.
    assert out["fresh"]["restarts"] == 2 and out["fresh"]["restarting"] is True, out
    # The bug: hours later the counter still says 2, but nothing is restarting.
    assert out["hoursLater"]["restarts"] == 2, out
    assert out["hoursLater"]["restarting"] is False, out
    assert out["afterRestart"]["restarting"] is True and out["afterRestart"]["restarts"] == 0, out


# --------------------------------------------------------------------------
# Second review round (v1.312.0): the watchdog's breaker is the other way the
# supervisor gives up. A daemon that is UP but stopped answering, three kills
# in 15 minutes: the breaker stops killing it. The window used to say
# "restarting" and the tray offered no Restart.
# --------------------------------------------------------------------------

_BREAKER = r"""
__EXTRACT__
__STUBS__
let updateInstallInFlight = false;
for (const d of ["DAEMON_WATCHDOG_MS", "DAEMON_WATCHDOG_PROBE_TIMEOUT_MS", "DAEMON_WATCHDOG_MISS_LIMIT",
                 "DAEMON_WATCHDOG_BREAKER_WINDOW_MS", "DAEMON_WATCHDOG_BREAKER_MAX", "_dwMissed", "_dwPid",
                 "_dwProbeInFlight", "_dwBooting"]) eval(decl(d));
for (const f of ["daemonWatchdogPaused", "daemonWatchdogTick", "notifyWatchdogExhausted"]) eval(fnSource(f));
let healthy = false;
function probeDaemonHealth() { return Promise.resolve(healthy ? { status: "ok", version: "x" } : null); }
function reportIncident() {}
function sweepOrphanDaemons() {}
const ending = process.argv[3]; // "restart" | "recovers" | "dies"
let dSpawns = 0;
startService("daemon", () => { dSpawns += 1; return fakeChild(7000 + dSpawns); }); // stays up
const menu = () => buildTrayContextMenu().map((i) => i.label);
const flush = () => new Promise((r) => realSetTimeout(r, 10));
(async () => {
  const out = { ending, menuBefore: menu(), stateBefore: shellState().services.daemon };
  // Three kills already inside the window, and the third miss lands now.
  const now = Date.now();
  _dwKills = [now - 3000, now - 2000, now - 1000];
  _dwPid = daemonProc.pid; _dwBooting = false; _dwMissed = DAEMON_WATCHDOG_MISS_LIMIT - 1;
  daemonWatchdogTick();
  await flush();
  out.tripped = _dwBreakerTripped;
  out.killedAtTrip = calls.killed.slice();
  out.stalled = shellState().services.daemon;
  out.menuStalled = menu();
  out.toast = notes.filter((n) => n.startsWith("toast:")).pop() || null;
  if (ending === "dies") {
    // The stalled child dies ON ITS OWN (a crash, Task Manager): the exit
    // ladder respawns it. The new child is not the one the breaker gave up on.
    const old = daemonProc;
    old.exitCode = 9;
    old.emit("close", 9);
    for (let i = 0; i < 400 && dSpawns < 2; i++) await flush();
    out.after = shellState().services.daemon;
  } else if (ending === "restart") {
    out.ret = lift("restartDaemonFromShell")();
    await flush();
    out.after = shellState().services.daemon;
    out.breakerAfter = { tripped: _dwBreakerTripped, kills: _dwKills.length };
  } else {
    healthy = true;
    daemonWatchdogTick();
    await flush();
    out.after = shellState().services.daemon;
  }
  out.menuAfter = menu();
  out.killed = calls.killed; out.dSpawns = dSpawns;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _breaker(ending: str, tmp_path: Path) -> dict:
    script = _BREAKER.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    return _run(script, tmp_path, f"breaker_{ending}", "clean", ending)


@requires_node
def test_a_tripped_watchdog_breaker_is_stalled_not_restarting_and_the_tray_offers_restart(tmp_path):
    out = _breaker("restart", tmp_path)
    # Anti-vacuity: the harness really reached the breaker, and it killed nothing.
    assert out["tripped"] is True and out["killedAtTrip"] == [], out
    assert "Restart Iron Jarvis" not in out["menuBefore"], out["menuBefore"]
    assert out["stateBefore"]["stalled"] is False, out["stateBefore"]
    d = out["stalled"]
    # The daemon was spawned a moment ago (inside its boot allowance), so only
    # the stalled verdict keeps this from reading as "restarting".
    assert d["stalled"] is True and d["restarting"] is False, d
    assert d["capped"] is False, d
    assert "Restart Iron Jarvis" in out["menuStalled"], out["menuStalled"]
    # The toast points at that tray item, never at Quit.
    assert out["toast"] and "stopped answering repeatedly" in out["toast"], out
    assert "Restart Iron Jarvis" in out["toast"] and "Quit" not in out["toast"], out["toast"]
    # The window's Restart clears the breaker and restarts the LIVE daemon.
    assert out["ret"] is True, out
    assert out["killed"] == ["daemon:restart"], out["killed"]
    assert out["dSpawns"] == 2, out
    assert out["breakerAfter"] == {"tripped": False, "kills": 0}, out["breakerAfter"]
    assert out["after"]["stalled"] is False, out["after"]
    assert "Restart Iron Jarvis" not in out["menuAfter"], out["menuAfter"]


@requires_node
def test_a_stalled_daemon_that_answers_again_is_no_longer_stalled(tmp_path):
    out = _breaker("recovers", tmp_path)
    assert out["stalled"]["stalled"] is True and "Restart Iron Jarvis" in out["menuStalled"], out
    assert out["after"]["stalled"] is False, out["after"]
    assert "Restart Iron Jarvis" not in out["menuAfter"], out["menuAfter"]
    assert out["killed"] == [], out["killed"]


_ADOPTED_REFUSED = r"""
__EXTRACT__
__STUBS__
global.setTimeout = (fn, ms) => { fn(); return { fake: true }; };
global.clearTimeout = () => {};
let spawns = 0;
startService("daemon", () => { spawns += 1; const c = fakeChild(8000 + spawns);
  if (spawns <= 40) process.nextTick(() => { c.exitCode = 3; c.emit("close", 3); }); return c; });
realSetTimeout(() => {
  const rec = _services.daemon;
  // An adopted daemon has no child of ours: the window's restart can do nothing.
  rec.adopted = true; daemonProc = null;
  const before = { capped: !!rec.capped, menu: buildTrayContextMenu().map((i) => i.label) };
  const ret = lift("restartDaemonFromShell")();
  process.stdout.write(JSON.stringify({ before, ret, capped: !!rec.capped, spawns,
    menu: buildTrayContextMenu().map((i) => i.label) }) + "\n");
}, 60);
"""


@requires_node
def test_a_refused_window_restart_changes_nothing(tmp_path):
    """Review minor: the banner's refusal note names the tray's Restart item —
    so a restart that could not run must not have removed it first."""
    script = _ADOPTED_REFUSED.replace("__EXTRACT__", _EXTRACT).replace("__STUBS__", _STUBS)
    out = _run(script, tmp_path, "adopted_refused", "clean")
    assert out["before"]["capped"] is True and "Restart Iron Jarvis" in out["before"]["menu"], out
    assert out["ret"] is False, out
    assert out["capped"] is True and "Restart Iron Jarvis" in out["menu"], out


@requires_node
def test_a_stalled_daemon_that_dies_and_is_respawned_reads_restarting_not_stalled(tmp_path):
    """Review (v1.312.0): `stalled` survived the exit ladder's respawn, so the
    banner said "automatic restarts are paused" over a daemon that WAS being
    restarted (and its Restart would have killed the booting child)."""
    out = _breaker("dies", tmp_path)
    assert out["stalled"]["stalled"] is True, out["stalled"]  # anti-vacuity
    assert out["dSpawns"] == 2, out
    assert out["after"]["stalled"] is False, out["after"]
    assert out["after"]["restarting"] is True, out["after"]
