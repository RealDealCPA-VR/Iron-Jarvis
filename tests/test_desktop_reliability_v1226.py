"""v1.226.0 — desktop supervisor reliability (``desktop/main.js``).

House idiom (``test_desktop_lifecycle_v1192.py``): LIFT the real function text
out of ``main.js`` and run it under node against stubs and a fake clock, so the
test exercises the shipped implementation; source-pin only the seams that
cannot be executed (they live inside ``startup()`` / ``before-quit``).

The findings (audit dimension E, with B where they coincide):

F-E-1  A daemon that is ALIVE but not serving was never detected: the crash
       supervisor only sees a child that dies. Now a watchdog probes /health
       every 30s after boot; three misses with the child alive kills it so the
       exit ladder restarts it; a breaker stops a non-converging kill loop.
F-E-2  Contract C2: ``ironjarvis serve`` exits 75 when an Iron Jarvis daemon
       already owns the port. The old handler treated any exit as a crash and
       respawned forever (13 spawns in 10 min, a false "keeps crashing" toast).
       Now: adopt it when its version is ours; else sweep our image name and
       restart ONCE. Quit also POSTs /shutdown to an adopted/stale daemon and
       sweeps unconditionally after the graceful attempt.
F-E-3  A restart whose spawn FAILS (ENOENT — AV quarantined the exe) emits
       error + close, never exit; the ladder was hooked on exit and silently
       disarmed. Now hooked on close, with a double-count guard; the boot gate
       names a spawn failure instead of blaming the port.
F-E-4  "Restart to update" taskkilled the daemon with agent sessions and
       workflow runs mid-flight. Now GET /system/activity (contract C6) first
       and ask when busy; a daemon that cannot answer never blocks the install.
F-E-5  The Quit path's graceful-stop budget is 5s (was 2s — never enough with
       a stream open).
F-E-6  Main-process errors had no file sink in the packaged app.
F-E-7  A failed dashboard load stranded the window on Chromium's error page.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parents[1] / "desktop" / "main.js"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def _src() -> str:
    return _MAIN.read_text(encoding="utf-8")


def _lift(start_marker: str, end_after: str) -> str:
    """Slice main.js from ``start_marker`` to the column-0 ``}`` closing the
    function that begins at ``end_after``. Lifted, never copied."""
    src = _src()
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _run(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), *argv], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# A fake clock that ALSO drains microtasks between timers: the probes are
# promise-based, so each tick's outcome must land before the next tick fires.
_CLOCK = """
const { EventEmitter } = require("events");
const path = require("path");
const realSetImmediate = setImmediate;
let now = 0;
const timers = [];
global.setTimeout = (fn, ms) => { const h = { at: now + (ms || 0), fn, cancelled: false }; timers.push(h); return h; };
global.clearTimeout = (h) => { if (h) h.cancelled = true; };
global.setInterval = (fn, ms) => {
  const h = { cancelled: false };
  const tick = () => { if (h.cancelled) return; fn(); timers.push({ at: now + ms, fn: tick, cancelled: false }); };
  timers.push({ at: now + ms, fn: tick, cancelled: false });
  return h;
};
global.clearInterval = (h) => { if (h) h.cancelled = true; };
Date.now = () => now;
const drain = () => new Promise((r) => realSetImmediate(r));
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    await drain();
    timers.sort((a, b) => a.at - b.at);
    const i = timers.findIndex((x) => x.at <= end);
    if (i < 0) break;
    const t = timers.splice(i, 1)[0];
    now = t.at;
    if (!t.cancelled) t.fn();
    await drain();
  }
  now = end;
  await drain();
}
console.error = () => {}; console.warn = () => {}; console.log = () => {};
"""

# Node http stub: /health and /system/activity answer per `modes`; POST /shutdown
# is recorded. "hang" never answers (the request's own setTimeout fires),
# "down" refuses, "ok" is our daemon's healthy shape, an object is served as-is.
_HTTP = """
const calls = [];
const modes = { health: "ok", activity: null, healthVersion: "1.226.0", owner: "ours" };
// owner: "ours" = 200 to our bearer, 401 to any other; "notoken" = 200 to everyone (a dev daemon);
// "foreign" = 401 to everyone; "down" = connection refused.
function fakeReq() {
  const req = new EventEmitter();
  req.done = false;
  req.destroy = (err) => { if (req.done) return; req.done = true; req.emit("error", err || new Error("destroyed")); };
  req.setTimeout = (ms, cb) => { setTimeout(() => { if (!req.done) cb(); }, ms); };
  req.end = () => {}; req.write = () => {};
  return req;
}
function answer(req, cb, body, status) {
  setTimeout(() => {
    if (req.done) return;
    req.done = true;
    const res = new EventEmitter();
    res.statusCode = status || 200; res.setEncoding = () => {}; res.resume = () => {};
    cb(res);
    res.emit("data", JSON.stringify(body));
    res.emit("end");
  }, 1);
}
const http = {
  get: (url, opts, cb) => {
    const req = fakeReq();
    const u = String(url).replace(/^http:\\/\\/127\\.0\\.0\\.1:\\d+/, "");
    calls.push(["GET", u]);
    if (u.startsWith("/diagnostics/reliability")) {
      const auth = (opts && opts.headers && opts.headers.Authorization) || "";
      const o = typeof modes.owner === "function" ? modes.owner() : modes.owner;
      if (o === "hang") return req;
      if (o === "down") { setTimeout(() => { if (!req.done) { req.done = true; req.emit("error", new Error("ECONNREFUSED")); } }, 1); return req; }
      const status = o === "notoken" ? 200 : o === "ours" && auth === "Bearer t" ? 200 : 401;
      answer(req, cb, { detail: status === 200 ? "ok" : "missing or invalid token" }, status);
      return req;
    }
    const plan = u.startsWith("/health") ? modes.health : modes.activity;
    const v = typeof plan === "function" ? plan() : plan;
    if (v === "hang") return req;
    if (v == null || v === "down") {
      setTimeout(() => { if (!req.done) { req.done = true; req.emit("error", new Error("ECONNREFUSED")); } }, 1);
      return req;
    }
    if (v === "ok") { answer(req, cb, { status: "ok", version: modes.healthVersion }); return req; }
    answer(req, cb, v);
    return req;
  },
  request: (opts, cb) => { calls.push([`${opts.method} ${opts.path}`]); return fakeReq(); },
};
"""

# Stubs for the main.js globals the supervisor/watchdog/shutdown code touches.
_SUPERVISOR_STUBS = """
let shuttingDown = false, isQuitting = false, updateInstallInFlight = false;
let daemonProc = null, dashboardProc = null, userDataDir = "X", authToken = "t";
const notes = [], logs = [], kills = [];
let tray = { setToolTip: (s) => calls.push(["tooltip", s]) };
class Notification { constructor(o) { this.o = o; } show() { notes.push(this.o.body); } }
const fileLogger = (label) => (line) => logs.push(`${label}: ${String(line).trim()}`);
function desktopLog(level, ...args) { logs.push(`desktop[${level}]: ${args.join(" ")}`); }
function reportIncident(kind, detail) { calls.push(["incident", kind, String(detail)]); }
function sweepOrphanDaemons() { calls.push(["sweep"]); }
const app = { getVersion: () => "1.226.0" };
const DAEMON_PORT = 8787;
const STARTUP_TIMEOUT_MS = 90000; // packaged boot gate
let spawns = 0; const children = [];
function makeChild() {
  const ch = new EventEmitter();
  ch.exitCode = null; ch.signalCode = null; ch.pid = 100 + spawns++;
  ch.on("error", () => {}); // spawnChild() attaches one too
  calls.push(["spawn", ch.pid]);
  children.push(ch);
  return ch;
}
function endChild(ch, code) { if (ch.exitCode !== null) return; ch.exitCode = code; ch.emit("exit", code, null); ch.emit("close", code, null); }
// taskkill: the child dies shortly after; exit + close follow, as in real node.
function killChild(child, label) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  kills.push({ at: now, pid: child.pid, label });
  setTimeout(() => endChild(child, 1), 10);
}
// plan(i) -> null (runs forever) | {code, afterMs} | {enoent: true}
function spawnDaemon(plan) {
  return startService("daemon", () => {
    const i = spawns; const ch = makeChild(); const p = plan(i);
    if (p && p.enoent) {
      setTimeout(() => { ch.exitCode = -4058; ch.emit("error", new Error("spawn ENOENT")); ch.emit("close", -4058, null); }, 5);
    } else if (p) {
      setTimeout(() => endChild(ch, p.code), p.afterMs);
    }
    return ch;
  });
}
"""

_SUPERVISOR_SCENARIOS = """
(async () => {
  const scenario = process.argv[2];
  const out = { scenario };
  let probeN = 0;
  switch (scenario) {
    // ---- F-E-1 watchdog
    case "wd_healthy": { spawnDaemon(() => null); installDaemonWatchdog(); await advance(20 * 60 * 1000); break; }
    case "wd_wedged": {
      spawnDaemon(() => null); modes.health = "hang"; installDaemonWatchdog();
      await advance(94000); out.killsBefore = kills.length;
      await advance(3000); out.killsAfter = kills.length; // the kill lands at 95s; the ladder respawns ~1s later
      break;
    }
    case "wd_recovers": {
      spawnDaemon(() => null); modes.health = () => (++probeN % 3 === 0 ? "ok" : "hang");
      installDaemonWatchdog(); await advance(20 * 60 * 1000); break;
    }
    case "wd_breaker": {
      spawnDaemon(() => null); modes.health = "hang"; installDaemonWatchdog();
      await advance(15 * 60 * 1000 - 1); break;
    }
    case "wd_paused": {
      spawnDaemon(() => null); modes.health = "hang"; installDaemonWatchdog();
      shuttingDown = true; await advance(10 * 60 * 1000); break;
    }
    case "wd_update_paused": {
      spawnDaemon(() => null); modes.health = "hang"; installDaemonWatchdog();
      updateInstallInFlight = true; await advance(10 * 60 * 1000); break;
    }
    case "wd_dead_child": {
      spawnDaemon(() => ({ code: 1, afterMs: 100 })); shuttingDown = true; // park the ladder
      await advance(200); shuttingDown = false; modes.health = "hang"; installDaemonWatchdog();
      await advance(10 * 60 * 1000); break;
    }
    case "wd_adopted_gone": {
      spawnDaemon((i) => (i === 0 ? { code: 75, afterMs: 300 } : null));
      await advance(2000); out.adoptedFirst = !!_services.daemon.adopted;
      modes.health = "down"; installDaemonWatchdog();
      await advance(3 * 30000 + 1000); break;
    }
    // ---- F-E-2 exit 75
    case "x75_adopt": { spawnDaemon(() => ({ code: 75, afterMs: 300 })); await advance(10 * 60 * 1000); break; }
    case "x75_mismatch": {
      modes.healthVersion = "1.200.0";
      spawnDaemon((i) => (i === 0 ? { code: 75, afterMs: 300 } : null));
      await advance(10 * 60 * 1000); break;
    }
    case "x75_mismatch_twice": {
      modes.healthVersion = "1.200.0";
      spawnDaemon(() => ({ code: 75, afterMs: 300 }));
      await advance(10 * 60 * 1000); break;
    }
    // ---- F-E-3 ladder on close
    case "enoent_restart": {
      spawnDaemon((i) => (i === 0 ? { code: 1, afterMs: 1000 } : { enoent: true }));
      await advance(60 * 60 * 1000); break;
    }
    case "double_count": {
      spawnDaemon((i) => (i === 0 ? { code: 1, afterMs: 1000 } : null));
      await advance(1500);
      children[0].emit("error", new Error("late spurious error")); // exit + close + error on ONE death
      await advance(5000); break;
    }
    case "plain_crash": {
      spawnDaemon(() => ({ code: 1, afterMs: 2000 }));
      await advance(60 * 60 * 1000); break;
    }
    // ---- F-E-2 Quit with our child gone
    case "quit_stale": {
      spawnDaemon(() => ({ code: 75, afterMs: 300 })); await advance(2000); calls.length = 0;
      modes.health = () => (++probeN <= 2 ? "ok" : "down");
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt; break;
    }
    case "quit_stale_ignores": {
      spawnDaemon(() => ({ code: 75, afterMs: 300 })); await advance(2000); calls.length = 0;
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt; break;
    }
    case "quit_gone_nohealth": {
      daemonProc = null; modes.health = "down"; calls.length = 0;
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt; break;
    }
    case "quit_alive": {
      spawnDaemon(() => null); calls.length = 0;
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(500); endChild(children[0], 0);
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt; break;
    }
    // ---- review R2: the ownership probe decides, not the version
    case "x75_notoken": {
      modes.owner = "notoken";
      spawnDaemon(() => ({ code: 75, afterMs: 300 }));
      await advance(10 * 60 * 1000); break;
    }
    case "x75_notoken_mismatch": {
      modes.owner = "notoken"; modes.healthVersion = "1.200.0";
      spawnDaemon(() => ({ code: 75, afterMs: 300 }));
      await advance(10 * 60 * 1000); break;
    }
    // ---- review R1: Quit sweeps only what is ours
    case "quit_sweep_nothing": {
      daemonProc = null; modes.health = "down"; modes.owner = "down";
      let resolved; sweepOwnDaemonOnQuit().then((v) => { resolved = v; });
      await advance(5000); out.resolved = resolved; break;
    }
    case "quit_sweep_notours": {
      daemonProc = null; modes.owner = "notoken";
      let resolved; sweepOwnDaemonOnQuit().then((v) => { resolved = v; });
      await advance(5000); out.resolved = resolved; break;
    }
    case "quit_sweep_ours": {
      daemonProc = null; modes.owner = "ours";
      let resolved; sweepOwnDaemonOnQuit().then((v) => { resolved = v; });
      await advance(5000); out.resolved = resolved; break;
    }
    case "quit_sweep_claimed": {
      spawnDaemon(() => ({ code: 75, afterMs: 300 })); await advance(2000);
      out.adoptedFirst = !!_services.daemon.adopted; calls.length = 0;
      modes.health = "down"; modes.owner = "down"; // even with nothing answering: we claimed it this session
      let resolved; sweepOwnDaemonOnQuit().then((v) => { resolved = v; });
      await advance(5000); out.resolved = resolved; break;
    }
    // ---- review R4: a restarted child gets the boot gate's grace
    case "wd_restart_grace": {
      // The reviewer's timeline: crash at 28.9s, respawn ~29.9s, the new child
      // binds and answers only 85s later (~114.9s). Zero kills expected.
      spawnDaemon((i) => (i === 0 ? { code: 1, afterMs: 28900 } : null));
      modes.health = () => (now < 28900 ? "ok" : now < 29900 + 85000 ? "hang" : "ok");
      installDaemonWatchdog();
      await advance(10 * 60 * 1000); break;
    }
    case "wd_restart_grace_ends": {
      // Same, but the restarted child NEVER answers: misses count once
      // STARTUP_TIMEOUT_MS has passed since it was spawned (29.9s + 90s).
      spawnDaemon((i) => (i === 0 ? { code: 1, afterMs: 28900 } : null));
      modes.health = () => (now < 28900 ? "ok" : "hang");
      installDaemonWatchdog();
      await advance(180000); out.killsBefore = kills.length;
      await advance(10000); out.killsAfter = kills.length; break;
    }
    // ---- review S3: unknown is not foreign
    case "x75_unknown_then_ours": {
      let ownerCalls = 0;
      modes.owner = () => (++ownerCalls <= 2 ? "hang" : "ours"); // the first probe PAIR times out
      spawnDaemon(() => ({ code: 75, afterMs: 300 }));
      await advance(10 * 60 * 1000); break;
    }
    case "x75_unknown_forever": {
      modes.owner = "hang";
      spawnDaemon(() => ({ code: 75, afterMs: 300 }));
      await advance(10 * 60 * 1000); out.foreignNotified = !!_services.daemon.foreignNotified; break;
    }
    case "x75_port_went_quiet": {
      modes.owner = "down"; modes.health = "down";
      spawnDaemon((i) => (i === 0 ? { code: 75, afterMs: 300 } : null));
      await advance(10 * 60 * 1000); break;
    }
    // ---- review S2: Quit never posts /shutdown to a daemon that is not ours
    case "quit_post_foreign": {
      daemonProc = null; modes.owner = "notoken"; calls.length = 0;
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt; break;
    }
    case "quit_post_unknown": {
      daemonProc = null; modes.owner = "hang"; calls.length = 0;
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt; break;
    }
    case "quit_post_ours_unclaimed": {
      daemonProc = null; modes.owner = "ours"; calls.length = 0;
      let probeN2 = 0; modes.health = () => (++probeN2 <= 2 ? "ok" : "down");
      let resolved, resolvedAt; const startedAt = now;
      requestDaemonShutdown(5000).then((v) => { resolved = v; resolvedAt = now - startedAt; });
      await advance(7000); out.resolved = resolved; out.resolvedAt = resolvedAt;
      out.postsDuringQuit = calls.filter((c) => c[0] === "POST /shutdown").length;
      // and the quit sweep reuses the verdict instead of probing again
      calls.length = 0; let swept; sweepOwnDaemonOnQuit().then((v) => { swept = v; });
      await advance(3000); out.swept = swept; out.sweepProbes = calls.filter((c) => c[0] === "GET").length; break;
    }
    default: throw new Error("unknown scenario " + scenario);
  }
  const rec = _services.daemon;
  out.spawns = spawns;
  out.kills = kills.map((k) => k.at);
  out.notes = notes;
  out.tooltips = calls.filter((c) => c[0] === "tooltip").map((c) => c[1]);
  out.incidents = calls.filter((c) => c[0] === "incident").map((c) => c[1]);
  out.sweeps = calls.filter((c) => c[0] === "sweep").length;
  out.order = calls.filter((c) => c[0] === "sweep" || c[0] === "spawn").map((c) => c[0]);
  out.posts = calls.filter((c) => c[0] === "POST /shutdown").length;
  out.rec = rec ? { restarts: rec.restarts, adopted: !!rec.adopted, swept: !!rec.swept } : null;
  out.logs = logs;
  out.timersArmed = timers.some((t) => !t.cancelled);
  out.daemonProcExitCode = daemonProc ? daemonProc.exitCode : null;
  process.stdout.write(JSON.stringify(out) + "\\n"); // console.log is stubbed above
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _supervisor_harness() -> str:
    supervisor = _lift("const RESTART_BACKOFF_MS", "function notifyForeignDaemon")
    watchdog = _lift("const DAEMON_WATCHDOG_MS", "function installDaemonWatchdog")
    shutdown = _lift("function postDaemonShutdown", "function requestDaemonShutdown(timeoutMs)")
    for needle, block in (
        ("function startService", supervisor),
        ("function adoptOrReplaceExistingDaemon", supervisor),
        ("function probeDaemonOwnership", supervisor),
        ("function sweepOwnDaemonOnQuit", shutdown),
        ("function probeDaemonHealth", watchdog),
        ("function daemonWatchdogTick", watchdog),
        ("function waitForDaemonPortClosed", shutdown),
    ):
        assert needle in block, f"{needle} not lifted"
    return _CLOCK + _HTTP + _SUPERVISOR_STUBS + supervisor + watchdog + shutdown + _SUPERVISOR_SCENARIOS


def _sup(scenario: str, tmp_path: Path) -> dict:
    return _run(_supervisor_harness(), tmp_path, "supervisor", scenario)


# --------------------------------------------------------------------------
# F-E-1 — the daemon liveness watchdog
# --------------------------------------------------------------------------


@requires_node
def test_watchdog_never_kills_a_daemon_that_answers(tmp_path):
    out = _sup("wd_healthy", tmp_path)
    assert out["kills"] == [], "a healthy daemon was killed"
    assert out["spawns"] == 1
    assert out["incidents"] == []
    assert out["notes"] == []


@requires_node
def test_watchdog_kills_after_three_consecutive_misses_with_the_child_alive(tmp_path):
    """Ticks at 30/60/90s, each probe times out 5s later: the third miss lands
    at 95s. Not one tick earlier."""
    out = _sup("wd_wedged", tmp_path)
    assert out["killsBefore"] == 0, "killed before the third miss"
    assert out["killsAfter"] == 1, f"expected exactly one kill at the third miss, got {out['kills']}"
    assert 90000 <= out["kills"][0] <= 96000, out["kills"]
    assert "daemon-wedged" in out["incidents"], "no incident recorded for the daemon's event log"
    assert any("watchdog" in l and "/health missed 3x" in l for l in out["logs"]), out["logs"]
    # The kill feeds the EXISTING exit ladder: the daemon was respawned.
    assert out["spawns"] >= 2, "the watchdog killed but nothing restarted the daemon"


@requires_node
def test_a_successful_probe_resets_the_miss_count(tmp_path):
    """Two misses, a hit, two misses, a hit ... is a flaky daemon, not a wedged one."""
    out = _sup("wd_recovers", tmp_path)
    assert out["kills"] == [], f"killed on non-consecutive misses: {out['kills']}"
    assert out["spawns"] == 1


@requires_node
def test_watchdog_breaker_stops_a_non_converging_kill_loop_and_says_so_once(tmp_path):
    """A daemon wedged for good: kills at ~95s, ~191s, ~287s — and then STOP.
    Without the breaker there would be ~9 kills in 15 minutes and no message."""
    out = _sup("wd_breaker", tmp_path)
    assert len(out["kills"]) == 3, f"breaker did not hold at 3 kills / 15 min: {out['kills']}"
    exhausted = [n for n in out["notes"] if "stopped answering repeatedly" in n]
    assert len(exhausted) == 1, f"the user must be told exactly once: {out['notes']}"
    assert any("breaker open" in l for l in out["logs"]), out["logs"]


@requires_node
@pytest.mark.parametrize("scenario", ["wd_paused", "wd_update_paused"])
def test_watchdog_is_paused_during_teardown_and_update_install(scenario, tmp_path):
    out = _sup(scenario, tmp_path)
    assert out["kills"] == [], f"the watchdog killed a daemon during {scenario}"
    assert out["incidents"] == []


@requires_node
def test_watchdog_leaves_a_dead_child_to_the_exit_ladder(tmp_path):
    """Nothing of ours is alive: killing is the ladder's business, not the watchdog's."""
    out = _sup("wd_dead_child", tmp_path)
    assert out["kills"] == []
    assert out["spawns"] == 1, "the watchdog must not spawn for a child the ladder owns"


@requires_node
def test_watchdog_replaces_an_adopted_daemon_that_went_away(tmp_path):
    """An adopted daemon (exit 75) has no child and no ladder. If IT dies, the
    app would be dead forever — so the watchdog starts our own."""
    out = _sup("wd_adopted_gone", tmp_path)
    assert out["adoptedFirst"] is True, "precondition: the existing daemon was adopted"
    assert out["spawns"] == 2, f"the adopted daemon vanished and nothing replaced it: {out}"
    assert out["kills"] == []
    assert out["rec"]["adopted"] is False
    # Review R3: a wedged adopted daemon may still HOLD the port; without a
    # sweep first, serve's preflight exits 1 and the ladder loops (13 spawns in
    # 10 min measured). Adoption proved ownership, so the sweep is safe.
    assert out["sweeps"] == 1, "the wedged adopted daemon was not swept before ours started"
    assert out["order"] == ["spawn", "sweep", "spawn"], f"the sweep must precede the replacement spawn: {out['order']}"


@requires_node
def test_a_restarted_child_gets_the_boot_gates_grace(tmp_path):
    """Review R4, the reviewer's timeline: crash at 28.9s, respawn, the new
    child binds at +85s. The old 60-90s tick-phase grace killed it three times,
    raised three incidents, tripped the breaker and left the app down ~5 min."""
    out = _sup("wd_restart_grace", tmp_path)
    assert out["kills"] == [], f"a BOOTING daemon was killed: {out['kills']}"
    assert out["incidents"] == []
    assert out["notes"] == []
    assert out["spawns"] == 2


@requires_node
def test_the_boot_grace_ends_at_the_startup_timeout(tmp_path):
    """A restarted child that never answers is still caught: misses count from
    STARTUP_TIMEOUT_MS after its spawn (29.9s + 90s), kill at the third."""
    out = _sup("wd_restart_grace_ends", tmp_path)
    assert out["killsBefore"] == 0, f"killed inside the grace: {out['kills']}"
    assert out["killsAfter"] == 1, f"a child that never answered was never killed: {out['kills']}"
    assert 180000 <= out["kills"][0] <= 190000, out["kills"]


# --------------------------------------------------------------------------
# F-E-2 — exit 75: adopt or replace, never loop
# --------------------------------------------------------------------------


@requires_node
def test_exit_75_with_our_version_adopts_the_existing_daemon(tmp_path):
    """The audit's staleDaemon scenario: 13 spawns in 10 min + a false crash toast."""
    out = _sup("x75_adopt", tmp_path)
    assert out["spawns"] == 1, f"the ladder still restarts on exit 75: {out['spawns']} spawns in 10 min"
    assert out["notes"] == [], "a false 'keeps crashing' notification"
    assert out["rec"]["adopted"] is True
    assert out["rec"]["restarts"] == 0
    assert out["sweeps"] == 0, "a same-version daemon must not be swept"
    assert any("adopted an existing daemon" in l for l in out["logs"]), out["logs"]
    assert "Iron Jarvis — running" in out["tooltips"]


@requires_node
def test_exit_75_with_another_version_sweeps_and_restarts_once(tmp_path):
    out = _sup("x75_mismatch", tmp_path)
    assert out["sweeps"] == 1, "a stale daemon of another version was not swept"
    assert out["spawns"] == 2, f"expected exactly ONE restart after the sweep, got {out['spawns']}"
    assert out["rec"]["adopted"] is False
    assert out["notes"] == []


@requires_node
def test_a_second_exit_75_after_the_sweep_gives_up_honestly(tmp_path):
    """Something we cannot kill (a dev daemon) owns the port: say so, do not loop."""
    out = _sup("x75_mismatch_twice", tmp_path)
    assert out["spawns"] == 2, f"looping on an unkillable foreign daemon: {out['spawns']} spawns"
    assert out["sweeps"] == 1
    foreign = [n for n in out["notes"] if "could not be replaced" in n]
    assert len(foreign) == 1, out["notes"]
    assert any("holds port 8787" in t and "v1.200.0" in t for t in out["tooltips"]), out["tooltips"]


@requires_node
def test_a_token_less_daemon_of_our_version_is_not_ours_and_is_left_alone(tmp_path):
    """Review R2 (contract C2 as amended): version match alone adopted a dev
    daemon — the wrong HOME shown silently, then killed by our Quit. The
    ownership probe (200 to our bearer AND 401/403 to a wrong one) decides."""
    out = _sup("x75_notoken", tmp_path)
    assert out["rec"]["adopted"] is False, "a token-less (dev) daemon was adopted"
    assert out["spawns"] == 1, f"restarted against a daemon that is not ours: {out['spawns']} spawns"
    assert out["sweeps"] == 0, "swept a daemon that is not ours"
    assert len(out["notes"]) == 1, f"the user must be told exactly once: {out['notes']}"
    assert "8787" in out["notes"][0] and "v1.226.0" in out["notes"][0], out["notes"]
    assert any("not ours" in l for l in out["logs"]), out["logs"]


@requires_node
def test_a_foreign_daemon_of_another_version_gets_no_sweep_and_no_restart(tmp_path):
    out = _sup("x75_notoken_mismatch", tmp_path)
    assert out["spawns"] == 1 and out["sweeps"] == 0
    assert out["rec"]["adopted"] is False
    assert len(out["notes"]) == 1 and "v1.200.0" in out["notes"][0], out["notes"]


@requires_node
def test_an_ownership_probe_timeout_is_not_a_foreign_verdict(tmp_path):
    """Review S3: one 5s stall of a localhost request used to leave the app
    dead with a wrong 'another daemon' toast. Unknown → re-run the decision."""
    out = _sup("x75_unknown_then_ours", tmp_path)
    assert out["rec"]["adopted"] is True, f"a transient probe timeout was read as foreign: {out['logs']}"
    assert out["notes"] == [], out["notes"]
    assert out["spawns"] == 1 and out["sweeps"] == 0
    assert any("retrying (1/3)" in l for l in out["logs"]), out["logs"]


@requires_node
def test_unverifiable_ownership_never_latches_the_foreign_flag(tmp_path):
    out = _sup("x75_unknown_forever", tmp_path)
    assert out["foreignNotified"] is False, "unknown was latched as foreign"
    assert out["notes"] == [], "a wrong 'another daemon' toast"
    assert out["rec"]["adopted"] is False and out["sweeps"] == 0
    assert any("after 3 tries" in l for l in out["logs"]), out["logs"]


@requires_node
def test_a_port_that_went_quiet_after_exit_75_gets_our_own_daemon(tmp_path):
    """Nothing answers any more: whoever held the port is gone — start ours, no sweep."""
    out = _sup("x75_port_went_quiet", tmp_path)
    assert out["spawns"] == 2, out
    assert out["sweeps"] == 0 and out["notes"] == []


@requires_node
def test_quit_never_posts_shutdown_to_a_daemon_that_is_not_ours(tmp_path):
    """Review S2: a token-less dev daemon accepts POST /shutdown from anyone."""
    out = _sup("quit_post_foreign", tmp_path)
    assert out["posts"] == 0, "Quit stopped the user's dev daemon by HTTP"
    assert out["resolved"] is True
    assert out["resolvedAt"] <= 3100, f"Quit must stay bounded: {out['resolvedAt']}ms"


@requires_node
def test_quit_does_not_post_on_an_unknown_verdict_and_stays_bounded(tmp_path):
    out = _sup("quit_post_unknown", tmp_path)
    assert out["posts"] == 0
    assert out["resolved"] is True
    assert out["resolvedAt"] <= 3100, f"one 1500ms probe at most: {out['resolvedAt']}ms"


@requires_node
def test_quit_posts_to_an_unclaimed_daemon_proven_ours_and_the_sweep_reuses_the_verdict(tmp_path):
    out = _sup("quit_post_ours_unclaimed", tmp_path)
    assert out["postsDuringQuit"] == 1 and out["resolved"] is True
    assert out["swept"] is True
    assert out["sweepProbes"] == 0, "the quit sweep probed again instead of reusing the verdict"


@requires_node
def test_quit_sweep_never_runs_blind(tmp_path):
    """Review R1: nothing adopted, no /health answer → no taskkill at all."""
    out = _sup("quit_sweep_nothing", tmp_path)
    assert out["sweeps"] == 0, "Quit swept with nothing of ours on the port"
    assert out["resolved"] is False


@requires_node
def test_quit_sweep_spares_a_daemon_that_is_not_ours(tmp_path):
    """/health answers but the ownership probe fails (a dev daemon): no taskkill."""
    out = _sup("quit_sweep_notours", tmp_path)
    assert out["sweeps"] == 0, "Quit killed a daemon that is not ours"
    assert out["resolved"] is False


@requires_node
def test_quit_sweep_runs_for_a_daemon_proven_ours(tmp_path):
    out = _sup("quit_sweep_ours", tmp_path)
    assert out["sweeps"] == 1 and out["resolved"] is True


@requires_node
def test_quit_sweep_runs_when_this_session_claimed_the_daemon(tmp_path):
    """Adopted (or swept) this session: it is ours even if it no longer answers."""
    out = _sup("quit_sweep_claimed", tmp_path)
    assert out["adoptedFirst"] is True
    assert out["sweeps"] == 1 and out["resolved"] is True


# --------------------------------------------------------------------------
# F-E-3 — the ladder survives a failed spawn
# --------------------------------------------------------------------------


@requires_node
def test_a_spawn_that_fails_with_enoent_keeps_the_ladder_armed(tmp_path):
    """The audit's exeMissingOnRestart: 2 spawns in an hour, 0 notifications,
    supervisor disarmed. error + close, never exit."""
    out = _sup("enoent_restart", tmp_path)
    assert out["spawns"] > 10, f"the ladder disarmed after the failed spawn: {out['spawns']} spawns in 1h"
    assert out["timersArmed"] is True, "the supervisor gave up"
    assert len(out["notes"]) == 1, f"the crash-loop notification at #3 was lost: {out['notes']}"
    assert out["daemonProcExitCode"] == -4058


@requires_node
def test_one_death_is_counted_once_even_with_error_and_close(tmp_path):
    out = _sup("double_count", tmp_path)
    assert out["rec"]["restarts"] == 1, f"a single death was counted {out['rec']['restarts']} times"
    assert out["spawns"] == 2


@requires_node
def test_the_plain_crash_ladder_is_unchanged(tmp_path):
    """The behaviour the audit called solid: steady 60s retries, one toast at #3."""
    out = _sup("plain_crash", tmp_path)
    assert out["spawns"] > 40, out["spawns"]
    assert len(out["notes"]) == 1
    assert out["timersArmed"] is True


# --------------------------------------------------------------------------
# F-E-2 — Quit reaches a daemon our child no longer owns
# --------------------------------------------------------------------------


@requires_node
def test_quit_shuts_down_an_adopted_daemon_and_waits_for_the_port_to_close(tmp_path):
    """The audit's quitWithStaleDaemon: resolved true immediately, no POST sent."""
    out = _sup("quit_stale", tmp_path)
    assert out["posts"] == 1, "POST /shutdown was never sent to the adopted daemon"
    assert out["resolved"] is True
    assert 0 < out["resolvedAt"] < 5000, f"must resolve when the port closes, not at the deadline: {out['resolvedAt']}"


@requires_node
def test_quit_waits_no_longer_than_the_budget_for_a_daemon_that_ignores_us(tmp_path):
    out = _sup("quit_stale_ignores", tmp_path)
    assert out["posts"] == 1
    assert out["resolved"] is False, "an unresponsive daemon must hand off to the force-kill + sweep"
    assert 5000 <= out["resolvedAt"] <= 6000, f"elapsed {out['resolvedAt']}ms is not the 5000ms budget"


@requires_node
def test_quit_with_nothing_on_the_port_resolves_without_a_request(tmp_path):
    out = _sup("quit_gone_nohealth", tmp_path)
    assert out["posts"] == 0
    assert out["resolved"] is True
    assert out["resolvedAt"] < 2000


@requires_node
def test_quit_with_our_own_live_child_is_unchanged(tmp_path):
    out = _sup("quit_alive", tmp_path)
    assert out["posts"] == 1
    assert out["resolved"] is True
    assert out["resolvedAt"] < 1000


# --------------------------------------------------------------------------
# F-E-3 / F-B-2 — the boot gate names the real cause
# --------------------------------------------------------------------------

_GATE_HARNESS = """
__CLOCK__
__HTTP__
let daemonProc = null, authToken = "t";
const DAEMON_PORT = 8787;
const app = { getVersion: () => "1.226.0" };
const _services = {};
__LIFTED__
(async () => {
  const s = JSON.parse(process.argv[2]);
  if (s.exitCode !== undefined) daemonProc = { exitCode: s.exitCode, signalCode: null };
  if (s.healthVersion) modes.healthVersion = s.healthVersion;
  if (s.health) modes.health = s.health;
  if (s.owner) modes.owner = s.owner;
  if (s.foreignNotified) _services.daemon = { foreignNotified: true };
  let result = null;
  waitForDaemon(3000, 500).then(() => { result = { ok: true, at: now }; }, (e) => { result = { ok: false, at: now, message: e.message }; });
  await advance(8000); // an in-flight ownership probe (2.5s) may overrun the 3s deadline by one round
  process.stdout.write(JSON.stringify({ result, probes: calls.filter((c) => c[0] === "GET").length }) + "\\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _gate(state: dict, tmp_path: Path) -> dict:
    lifted = _lift("function httpStatus", "function probeDaemonOwnership") + _lift(
        "function waitForDaemon(timeoutMs, intervalMs)", "function waitForDaemon(timeoutMs, intervalMs)"
    )
    script = (
        _GATE_HARNESS.replace("__CLOCK__", _CLOCK).replace("__HTTP__", _HTTP).replace("__LIFTED__", lifted)
    )
    return _run(script, tmp_path, "gate", json.dumps(state))


@requires_node
def test_gate_names_a_spawn_failure_instead_of_blaming_the_port(tmp_path):
    out = _gate({"exitCode": -4058}, tmp_path)
    assert out["result"]["ok"] is False
    assert "could not be started" in out["result"]["message"], out["result"]
    assert "port in use" not in out["result"]["message"]
    assert out["result"]["at"] == 0, "must fail fast"


@requires_node
def test_gate_keeps_polling_on_exit_75_and_passes_on_our_daemon(tmp_path):
    out = _gate({"exitCode": 75}, tmp_path)
    assert out["result"]["ok"] is True, out["result"]


@requires_node
def test_gate_still_fails_fast_on_a_foreign_port_holder(tmp_path):
    out = _gate({"exitCode": 1}, tmp_path)
    assert out["result"]["ok"] is False
    assert "port in use?" in out["result"]["message"]
    assert out["result"]["at"] == 0


@requires_node
def test_gate_refuses_a_daemon_of_another_version_and_says_which(tmp_path):
    """F-B-2: the daily driver must not run against a stale/dev daemon's state."""
    out = _gate({"healthVersion": "1.200.0"}, tmp_path)
    assert out["result"]["ok"] is False, "a different version's /health passed the gate"
    assert "different version" in out["result"]["message"], out["result"]
    assert "v1.200.0" in out["result"]["message"] and "v1.226.0" in out["result"]["message"]
    assert out["probes"] > 1, "must keep polling (the supervisor replaces it), not fail fast"


@requires_node
def test_gate_refuses_a_same_version_daemon_that_is_not_ours(tmp_path):
    """Review S1: a token-less dev daemon of OUR version passed the gate at t=1s
    and the packaged app booted against its home while the toast said
    'could not be replaced'. A version match is not ownership."""
    out = _gate({"owner": "notoken"}, tmp_path)
    assert out["result"]["ok"] is False, "the gate passed on a daemon that is not ours"
    msg = out["result"]["message"]
    assert "not ours" in msg and "v1.226.0" in msg and "stop it or change its port" in msg, msg
    assert out["result"]["at"] >= 3000, "must keep retrying until the deadline, not fail fast"


@requires_node
def test_gate_short_circuits_once_the_supervisor_proved_the_holder_foreign(tmp_path):
    out = _gate({"owner": "notoken", "foreignNotified": True}, tmp_path)
    assert out["result"]["ok"] is False
    assert out["result"]["at"] < 100, "already proven foreign: fail now, not at the 90s deadline"
    assert "not ours" in out["result"]["message"]


@requires_node
def test_gate_keeps_retrying_while_ownership_is_unknown(tmp_path):
    out = _gate({"owner": "hang"}, tmp_path)
    assert out["result"]["ok"] is False
    assert "could not verify" in out["result"]["message"], out["result"]
    assert out["probes"] > 1


@requires_node
def test_gate_passes_our_own_daemon(tmp_path):
    out = _gate({"owner": "ours"}, tmp_path)
    assert out["result"]["ok"] is True, out


def test_startup_dialog_shows_the_gate_reason():
    """The mapping is worthless if the dialog still says 'port in use' for everything."""
    src = _src()
    m = re.search(r'"Iron Jarvis — daemon did not start",(.{0,900}?)pendingUpdate\n', src, re.S)
    assert m, "startup()'s daemon-failure branch moved"
    assert "err && err.message" in m.group(1), "the dialog does not show the gate's reason"


# --------------------------------------------------------------------------
# F-E-7 — a failed dashboard load recovers
# --------------------------------------------------------------------------

_RELOAD_HARNESS = """
const { EventEmitter } = require("events");
const calls = [];
const DASHBOARD_URL = "http://localhost:8788";
function isDashboardUrl(u) { try { return new URL(String(u)).origin === DASHBOARD_URL; } catch { return false; } }
let waits = [];
function waitForDashboard(t, i) { calls.push(["waitForDashboard", t, i]); return new Promise((res, rej) => waits.push({ res, rej })); }
function desktopLog() {}
console.error = () => {};
__LIFTED__
(async () => {
  const wc = new EventEmitter();
  wc.isDestroyed = () => false;
  wc.loadURL = (u) => calls.push(["loadURL", u]);
  installDashboardReloadOnFailure({ webContents: wc });
  const drain = () => new Promise((r) => setImmediate(r));
  const events = JSON.parse(process.argv[2]);
  for (const ev of events) {
    wc.emit("did-fail-load", {}, ev.code, "desc", ev.url, ev.main);
    await drain();
  }
  const before = calls.slice();
  for (const w of waits) w.res();
  await drain(); await drain();
  console.log(JSON.stringify({ before, after: calls, pending: _dashboardReloadPending }));
})();
"""


def _reload(events: list, tmp_path: Path) -> dict:
    lifted = _lift("let _dashboardReloadPending = false;", "function installDashboardReloadOnFailure")
    return _run(_RELOAD_HARNESS.replace("__LIFTED__", lifted), tmp_path, "reload", json.dumps(events))


@requires_node
def test_a_failed_main_frame_load_waits_for_the_dashboard_then_loads_it(tmp_path):
    out = _reload([{"code": -105, "url": "http://localhost:8788/sessions", "main": True}], tmp_path)
    assert ["waitForDashboard", 60000, 500] in out["before"], out
    assert not any(c[0] == "loadURL" for c in out["before"]), "loaded before the dashboard answered"
    assert ["loadURL", "http://localhost:8788"] in out["after"], out
    assert out["pending"] is False, "the re-arm flag stuck"


@requires_node
def test_a_second_failure_while_waiting_does_not_stack_waits(tmp_path):
    out = _reload(
        [
            {"code": -105, "url": "http://localhost:8788/", "main": True},
            {"code": -105, "url": "http://localhost:8788/", "main": True},
        ],
        tmp_path,
    )
    assert sum(1 for c in out["after"] if c[0] == "waitForDashboard") == 1
    assert sum(1 for c in out["after"] if c[0] == "loadURL") == 1


@requires_node
@pytest.mark.parametrize(
    "event",
    [
        {"code": -3, "url": "http://localhost:8788/", "main": True},  # ERR_ABORTED
        {"code": -105, "url": "http://localhost:8788/", "main": False},  # a subframe
        {"code": -105, "url": "http://evil.example/", "main": True},  # not ours
    ],
)
def test_aborted_subframe_and_foreign_failures_are_ignored(event, tmp_path):
    out = _reload([event], tmp_path)
    assert out["after"] == [], out


def test_the_reload_guard_is_installed_on_every_window_incarnation():
    src = _src()
    m = re.search(r"installRendererWatchdog\(mainWin\);\n(.{0,200}?)mainWin\.loadURL\(DASHBOARD_URL\);", src, re.S)
    assert m, "createMainWindow's tail moved"
    assert "installDashboardReloadOnFailure(mainWin);" in m.group(1)


# --------------------------------------------------------------------------
# F-E-4 — installing an update asks when work is in flight
# --------------------------------------------------------------------------

_UPDATE_HARNESS = """
__CLOCK__
__HTTP__
let pendingUpdateInfo = { version: "1.226.0" };
let updateInstallInFlight = false;
let authToken = "t";
const DAEMON_PORT = 8787;
let dialogChoice = 1;
const dialog = { showMessageBoxSync: (o) => { calls.push(["dialog", o.message, o.buttons.join("|")]); return dialogChoice; } };
function applyPendingUpdate() { calls.push(["applyPendingUpdate"]); updateInstallInFlight = true; }
function desktopLog(level, ...a) { calls.push(["log", level]); }
__LIFTED__
(async () => {
  const s = JSON.parse(process.argv[2]);
  modes.activity = s.activity;
  dialogChoice = s.choice === undefined ? 1 : s.choice;
  requestUpdateInstall();
  if (s.twice) requestUpdateInstall();
  await advance(4000);
  process.stdout.write(JSON.stringify({ calls, promptInFlight: updatePromptInFlight, installInFlight: updateInstallInFlight }) + "\\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _update(state: dict, tmp_path: Path) -> dict:
    lifted = _lift("const ACTIVITY_PROBE_TIMEOUT_MS", "function requestUpdateInstall")
    assert "function probeDaemonActivity" in lifted
    script = _UPDATE_HARNESS.replace("__CLOCK__", _CLOCK).replace("__HTTP__", _HTTP).replace("__LIFTED__", lifted)
    return _run(script, tmp_path, "update", json.dumps(state))


@requires_node
def test_busy_daemon_and_later_installs_nothing(tmp_path):
    out = _update({"activity": {"active_sessions": 2, "running_workflow_runs": 1, "busy": True}, "choice": 1}, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "dialog" in names, "the user was never asked"
    dlg = next(c for c in out["calls"] if c[0] == "dialog")
    assert dlg[1] == "2 agent sessions / 1 workflow run are still running."
    assert dlg[2] == "Install now|Later"
    assert "applyPendingUpdate" not in names, "'Later' installed anyway"
    assert out["promptInFlight"] is False, "a second click would be locked out forever"


@requires_node
def test_busy_daemon_and_install_now_proceeds(tmp_path):
    out = _update({"activity": {"active_sessions": 1, "running_workflow_runs": 0, "busy": True}, "choice": 0}, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert names.index("dialog") < names.index("applyPendingUpdate")


@requires_node
def test_idle_daemon_installs_without_a_prompt(tmp_path):
    out = _update({"activity": {"active_sessions": 0, "running_workflow_runs": 0, "busy": False}}, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "dialog" not in names
    assert names.count("applyPendingUpdate") == 1


@requires_node
@pytest.mark.parametrize("activity", ["down", "hang"])
def test_a_daemon_that_cannot_answer_never_blocks_the_install(activity, tmp_path):
    """Best-effort by contract: on any error proceed exactly as before."""
    out = _update({"activity": activity}, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "dialog" not in names
    assert names.count("applyPendingUpdate") == 1, out


@requires_node
def test_a_double_click_asks_once_and_installs_once(tmp_path):
    out = _update({"activity": {"active_sessions": 1, "running_workflow_runs": 0, "busy": True}, "choice": 0, "twice": True}, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert names.count("dialog") == 1
    assert names.count("applyPendingUpdate") == 1


def test_every_user_initiated_install_path_goes_through_the_activity_check():
    """Tray item, notification click, Updates page IPC — none may bypass it."""
    src = _src()
    assert "click: () => requestUpdateInstall()," in src, "the tray item still installs blind"
    assert 'note.on("click", () => requestUpdateInstall());' in src, "the notification still installs blind"
    m = re.search(r'ipcMain\.handle\("update:apply", \(event\) => \{(.{0,300}?)\n  \}\);', src, re.S)
    assert m and "requestUpdateInstall()" in m.group(1), "the Updates page still installs blind"
    # applyPendingUpdate itself stays the single synchronous handoff (its own tests).
    direct = re.findall(r"^\s*(?:click: \(\) => |note\.on\(\"click\", \(\) => |if \(pendingUpdateInfo\) )applyPendingUpdate\(\)", src, re.M)
    assert direct == [], f"install entry points that skip the activity check: {direct}"


# --------------------------------------------------------------------------
# F-E-5 / F-E-2 — the Quit path
# --------------------------------------------------------------------------


def test_quit_gives_the_graceful_stop_five_seconds_and_sweeps_only_ours():
    """Review R1: a blind `taskkill /IM ironjarvis.exe` on Quit killed every
    dev `uv run ironjarvis serve` on the machine (.venv/Scripts/ironjarvis.exe)
    on any port. The only unconditional sweep left is the pre-existing
    quit-install one, gated on pendingUpdateInfo as it always was."""
    src = _src()
    m = re.search(r"requestDaemonShutdown\((\d+)\)\.finally\(\(\) => \{(.{0,1600}?)\n    \}\);", src, re.S)
    assert m, "the before-quit teardown moved"
    assert m.group(1) == "5000", f"Quit budget is {m.group(1)}ms — 2s never fit a drain with a stream open"
    body = m.group(2)
    assert "sweepOwnDaemonOnQuit()" in body, "Quit no longer sweeps an adopted/stale daemon of ours"
    assert body.index("shutdown();") < body.index("sweepOwnDaemonOnQuit()"), "sweep before our own child is dead"
    gated = re.search(r"if \(pendingUpdateInfo\) \{(.*?)\}", body, re.S)
    assert gated and "sweepOrphanDaemons();" in gated.group(1) and "markUpdatePending(pendingUpdateInfo.version);" in gated.group(1)
    outside = body.replace(gated.group(0), "")
    assert "sweepOrphanDaemons()" not in outside, "a BLIND sweep is back on the Quit path"


def test_the_ladder_forgets_a_sweep_after_a_healthy_run_and_quit_stops_the_watchdog():
    """Review nits: rec.swept resets beside rec.restarts; _dwTimer cleared on quit."""
    src = _src()
    assert re.search(
        r"if \(uptime > 5 \* 60 \* 1000\) \{\s*rec\.restarts = 0;[^\n]*\n\s*rec\.swept = false;\s*\}", src
    ), "rec.swept is not reset with the ladder after a healthy run"
    assert re.search(
        r'app\.on\("will-quit", \(\) => \{\s*globalShortcut\.unregisterAll\(\);\s*if \(_dwTimer\) clearInterval\(_dwTimer\);\s*_dwTimer = null;',
        src,
    ), "the daemon watchdog timer is not cleared on quit"


def test_the_watchdog_is_armed_once_boot_completes():
    src = _src()
    m = re.search(r"bootComplete = true;(.{0,900}?)\n  checkForUpdates\(\);", src, re.S)
    assert m, "startup()'s post-gate block moved"
    assert "installDaemonWatchdog();" in m.group(1), "the watchdog is never installed"


# --------------------------------------------------------------------------
# F-E-6 — main-process errors reach a file
# --------------------------------------------------------------------------

_LOG_HARNESS = """
const sinks = { file: [], console: [] };
const fileLogger = (label) => (line) => sinks.file.push([label, String(line)]);
console.error = (...a) => sinks.console.push(["error", a.join(" ")]);
console.warn = (...a) => sinks.console.push(["warn", a.join(" ")]);
__LIFTED__
desktopLog("error", "[update] check failed:", "boom");
desktopLog("warn", "[hotkey] taken");
desktopLog("error", "[x] err:", new Error("with a stack"));
console.log(JSON.stringify(sinks));
"""


@requires_node
def test_desktop_log_writes_both_sinks(tmp_path):
    out = _run(_LOG_HARNESS.replace("__LIFTED__", _lift("function desktopLog", "function desktopLog")), tmp_path, "log")
    assert [c[1] for c in out["console"]] == ["[update] check failed: boom", "[hotkey] taken", "[x] err: Error: with a stack"]
    assert [c[0] for c in out["console"]] == ["error", "warn", "error"]
    assert all(label == "desktop" for label, _ in out["file"]), out["file"]
    assert "[error] [update] check failed: boom" in out["file"][0][1]
    assert "[warn] [hotkey] taken" in out["file"][1][1]
    assert "with a stack" in out["file"][2][1]
    assert all(line.endswith("\n") for _, line in out["file"])


def test_no_console_only_error_sites_remain():
    src = _src()
    stray = [ln for ln in src.splitlines() if "console.error(" in ln or "console.warn(" in ln]
    assert stray == [], f"main-process errors still written nowhere in the packaged app: {stray}"
    assert src.count('desktopLog("error",') + src.count('desktopLog("warn",') >= 24
