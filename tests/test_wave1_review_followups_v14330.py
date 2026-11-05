"""v1.433.0 (Wave 1, group D) — the independent review's follow-ups.

Each pin was red on the change as first committed (89feefdd):

1. A8 — Settings › Maintenance's "Restart Iron Jarvis" with an ADOPTED
   daemon (exit 75: one of ours already held the port, so the shell has no
   child of its own) was refused ("Quit Iron Jarvis from the tray menu…"),
   where the old plain ``/shutdown`` had brought it back through the watchdog.
   The tidy restart now asks the adopted daemon to stop as a restart, waits
   for its port to close, and starts our own.
2. A11 — a loop whose ``enabled()`` probe RAISED was treated as switched off:
   its row was dropped, so a calendar or Slack channel the user turned ON
   that could not start read as nothing at all on the bell and Status. A
   probe that raises is a failure, said with its reason; only a probe that
   answers "off" drops the row.
3. L2 — the terminal-history loop and the REPL idle sweep warned (with a
   traceback) on EVERY failing pass: every 30 s and every minute for as long
   as it kept failing, which floods daemon.log and pushes real problems out
   of ``GET /diagnostics/errors`` (a ring of the last WARNING+ records). The
   first failure of a run warns; the repeats are DEBUG; a recovery re-arms.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.app as app_module
from iron_jarvis.daemon.app import create_app
from tests.test_wave1_desktop_shell_v14330 import _EXTRACT, _run, requires_node


def _until(pred, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# --------------------------------------------------------------------------
# 1. A8 — an adopted daemon restarts tidily too
# --------------------------------------------------------------------------

_ADOPTED = r"""
__EXTRACT__
const { EventEmitter } = require("events");
const scenario = process.argv[2];
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null, userDataDir = "X";
let authToken = "t";
const logs = []; const killed = []; const posts = []; const sweeps = []; const waits = [];
let tray = { setToolTip() {} };
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
function fileLogger() { return (s) => logs.push(String(s).trim()); }
class Notification { constructor(o) { this.o = o; } show() {} }
// exit 75 → the adoption decision (the real one probes /health + ownership).
function adoptOrReplaceExistingDaemon(rec) { rec.adopted = true; }
function verifyInstallIntegrity() { return { ok: true, checked: 1 }; }
function handleCorruptInstall() {}
function refreshTrayMenu() {}
function armDashboardReload() {}
function killChild(child, label, reason) { killed.push(`${label}:${reason}`); }
function sweepOrphanDaemons(opts) { sweeps.push(opts || null); }
// /health goes silent both when it stopped AND when it is wedged ("wedged":
// it never answers but still holds the port) — only the port tells them apart.
function waitForDaemonPortClosed(ms) {
  waits.push(ms);
  return Promise.resolve(scenario !== "portheld");
}
function waitForDaemonPortFree(ms) {
  waits.push(ms);
  return Promise.resolve(scenario !== "portheld" && scenario !== "wedged");
}
const http = {
  request(opts, cb) {
    const req = new (require("events").EventEmitter)();
    req.setTimeout = () => {};
    req.end = (data) => posts.push({ path: opts.path, body: data === undefined ? null : String(data) });
    return req;
  },
};
for (const d of ["WINDOWS_SHUTDOWN_EXIT_CODES", "WINDOWS_SHUTDOWN_GRACE_MS", "windowsSessionEnding",
                 "RESTART_BACKOFF_MS", "FAST_DEATH_MS", "FAST_DEATHS_TO_VERIFY", "RESTART_CAP_MAX",
                 "RESTART_CAP_WINDOW_MS", "DEATHS_DAY_MS", "DEATHS_DAY_TOAST_AT", "_services", "_trayDegraded",
                 "DAEMON_PORT", "_dwKills", "_dwBreakerTripped", "TIDY_RESTART_KILL_MS"]) eval(decl(d));
for (const f of ["isWindowsShutdownExit", "markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop",
                 "restartDaemonFromShell", "postDaemonShutdown", "restartDaemonTidy"]) eval(fnSource(f));
try { eval(fnSource("restartAdoptedDaemonTidy")); } catch { /* not in this version */ }
let spawns = 0;
const spawnFn = () => {
  spawns += 1; const c = new EventEmitter(); c.pid = 100 + spawns; c.exitCode = null; c.signalCode = null; return c;
};
startService("daemon", spawnFn);
// Our child finds one of ours on the port: exit 75 → adopted (no child of ours runs).
daemonProc.exitCode = 75; daemonProc.emit("close", 75);
const out = { scenario, adoptedBefore: !!_services.daemon.adopted };
out.ret = restartDaemonTidy(scenario === "stopping" ? { stopping: true } : undefined);
if (scenario === "double") out.ret2 = restartDaemonTidy();
if (scenario === "quit") isQuitting = true;
setTimeout(() => {
  Object.assign(out, {
    posts, killed, sweeps, waits, spawns, adoptedAfter: !!_services.daemon.adopted, logs,
  });
  process.stdout.write(JSON.stringify(out) + "\n");
}, 50);
"""


def _adopted(scenario: str, tmp_path) -> dict:
    return _run(_ADOPTED.replace("__EXTRACT__", _EXTRACT), tmp_path, f"adopted_{scenario}", scenario)


@requires_node
def test_settings_restart_of_an_adopted_daemon_asks_it_to_stop_then_starts_our_own(tmp_path):
    out = _adopted("restart", tmp_path)
    assert out["adoptedBefore"] is True, out
    assert out["ret"] is True, "the desktop app refused to restart an adopted Iron Jarvis"
    assert [p["path"] for p in out["posts"]] == ["/shutdown"], out["posts"]
    assert out["posts"][0]["body"] == '{"restart":true}', out["posts"]  # the code host keeps running
    assert out["waits"] and out["waits"][0] >= 5000, out["waits"]
    assert out["spawns"] == 2 and out["adoptedAfter"] is False, out
    assert out["sweeps"] == [] and out["killed"] == [], out


@requires_node
def test_an_adopted_daemon_already_stopping_itself_is_not_asked_twice(tmp_path):
    out = _adopted("stopping", tmp_path)
    assert out["ret"] is True and out["posts"] == [], out
    assert out["spawns"] == 2 and out["adoptedAfter"] is False, out


@requires_node
def test_an_adopted_daemon_that_keeps_the_port_is_swept_sparing_the_code_host(tmp_path):
    out = _adopted("portheld", tmp_path)
    assert out["ret"] is True, out
    assert out["sweeps"] == [{"spareCodeHost": True}], out["sweeps"]
    assert out["spawns"] == 2, out


@requires_node
def test_a_wedged_adopted_daemon_that_holds_the_port_is_swept_before_ours_starts(tmp_path):
    """/health silent is not "stopped": a wedged daemon answers nothing and
    still holds the port, and our daemon then exits 1 on the held port and
    the crash ladder loops (the watchdog's own note). The port decides."""
    out = _adopted("wedged", tmp_path)
    assert out["ret"] is True, out
    assert out["sweeps"] == [{"spareCodeHost": True}], out["sweeps"]
    assert out["spawns"] == 2, out


@requires_node
def test_a_double_press_on_an_adopted_daemon_starts_one_daemon(tmp_path):
    out = _adopted("double", tmp_path)
    assert out["ret"] is True and out["ret2"] is True, out
    assert len(out["posts"]) == 1 and out["spawns"] == 2, out


@requires_node
def test_quit_during_an_adopted_restart_starts_nothing(tmp_path):
    out = _adopted("quit", tmp_path)
    assert out["ret"] is True and out["spawns"] == 1, out


# --------------------------------------------------------------------------
# 2. A11 — a probe that RAISES is a failure, not "off"
# --------------------------------------------------------------------------


def test_a_calendar_whose_probe_raises_is_named_as_failing_not_dropped(tmp_path, monkeypatch):
    from iron_jarvis.triggers.calendar import CalendarPoller

    state = {"raise": True}

    def _enabled(self):
        if state["raise"]:
            raise RuntimeError("the calendar address could not be read")
        return False

    monkeypatch.setattr(CalendarPoller, "enabled", _enabled)
    app = create_app(str(tmp_path))
    d = app.state.d
    with TestClient(app) as c:
        def _row():
            return c.get("/diagnostics").json()["background_loops"].get("calendar")

        assert _until(lambda: (_row() or {}).get("ok") is False), _row()
        assert "could not be read" in _row()["last_error"], _row()
        # Control: a probe that ANSWERS "off" still drops the row.
        state["raise"] = False
        loop = d._live_rearm["loop"]

        async def _call() -> None:
            d._live_rearm["calendar"]()

        asyncio.run_coroutine_threadsafe(_call(), loop).result(10)
        assert _until(lambda: _row() is None), _row()


def test_a_slack_socket_whose_probe_raises_is_named_as_failing_not_dropped(tmp_path, monkeypatch):
    from iron_jarvis.comm.slack_socket import SlackSocketMode

    def _enabled(self):
        raise RuntimeError("the Slack app token could not be read")

    monkeypatch.setattr(SlackSocketMode, "enabled", _enabled)
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        def _row():
            return c.get("/diagnostics").json()["background_loops"].get("slack_socket")

        assert _until(lambda: (_row() or {}).get("ok") is False), _row()
        assert "could not be read" in _row()["last_error"], _row()


# --------------------------------------------------------------------------
# 3. L2 — a loop that keeps failing warns once per run, not once per pass
# --------------------------------------------------------------------------


def test_a_terminal_snapshot_that_keeps_raising_warns_once_and_keeps_its_row(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(app_module, "_TERMINAL_SNAPSHOT_S", 0.02)
    app = create_app(str(tmp_path))
    terminals = app.state.platform.terminals
    passes = {"n": 0}

    def _boom():
        passes["n"] += 1
        raise RuntimeError("index unwritable")

    monkeypatch.setattr(terminals, "snapshot_if_changed", _boom)
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as c:
            assert _until(lambda: passes["n"] >= 6), passes
            row = c.get("/diagnostics").json()["background_loops"].get("terminal_snapshot")
            assert (row or {}).get("ok") is False, row
    hits = [r for r in caplog.records if "terminal snapshot failed" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in hits]


@pytest.mark.asyncio
async def test_a_repl_sweep_that_keeps_failing_warns_once_per_run(caplog):
    calls = {"n": 0}

    class _Repl:
        async def sweep(self):
            calls["n"] += 1
            if calls["n"] == 6:
                return []  # one good pass: the next failure is a new run
            raise RuntimeError("child would not die")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(app_module._repl_sweep_loop(_Repl(), interval_s=0))
        for _ in range(500):
            await asyncio.sleep(0.005)
            if calls["n"] >= 12:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls["n"] >= 12, calls
    hits = [r for r in caplog.records if "repl idle sweep failed" in r.getMessage()]
    # Two runs of failures (before and after the good pass): two warnings,
    # never one per pass.
    assert len(hits) == 2 and all(r.levelno == logging.WARNING for r in hits), [r.getMessage() for r in hits]


# --------------------------------------------------------------------------
# 4. D5 — notify:show answers the dashboard only
# --------------------------------------------------------------------------

_NOTIFY = r"""
__EXTRACT__
const handlers = {};
const ipcMain = { handle(ch, fn) { handlers[ch] = fn; } };
const shown = [];
var DASHBOARD_PORT = 8788;
eval(setDecl("DASHBOARD_ORIGINS"));
for (const f of ["isDashboardUrl", "isTrustedDashboardSender"]) eval(fnSource(f));
class Notification {
  constructor(o) { this.o = o; }
  on() {}
  show() { shown.push(this.o); }
}
function showMainWindow() {}
eval(handlerSource("notify:show"));
const ours = { senderFrame: { url: "http://127.0.0.1:8788/chat" } };
const evil = { senderFrame: { url: "https://evil.example/x" } };
(async () => {
  const out = {};
  out.evil = await handlers["notify:show"](evil, { title: "Iron Jarvis", body: "Sign in again at evil.example" });
  out.gone = await handlers["notify:show"]({}, { title: "x", body: "y" });
  out.shownByOthers = shown.length;
  out.ours = await handlers["notify:show"](ours, { title: "Iron Jarvis", body: "Your task finished" });
  out.shown = shown;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


@requires_node
def test_a_desktop_notice_is_shown_only_for_the_dashboard(tmp_path):
    """A toast titled "Iron Jarvis" is a trusted voice: a frame that is not the
    dashboard must not be able to make one say "sign in again at …"."""
    out = _run(_NOTIFY.replace("__EXTRACT__", _EXTRACT), tmp_path, "notify")
    assert out["evil"] is False and out["gone"] is False, out
    assert out["shownByOthers"] == 0, out["shown"]
    # Control: the dashboard still gets its notice.
    assert out["ours"] is True and out["shown"] == [{"title": "Iron Jarvis", "body": "Your task finished"}], out


# --------------------------------------------------------------------------
# 5. W1 — the refusal reads as plain English
# --------------------------------------------------------------------------


def test_the_webhook_refusal_says_the_webhook_would_never_receive_it():
    """"…so this webhook would never be sent it" is not a sentence a person
    says; the refusal reads plainly for one name and for several."""
    from iron_jarvis.core.events import known_event_types
    from iron_jarvis.webhooks.outbound import unknown_event_types_sentence

    known = known_event_types()
    one = unknown_event_types_sentence(["sesion.completed"], known)
    assert one.startswith(
        "Iron Jarvis never sends an event called “sesion.completed”, so this webhook would never "
        "receive it. Did you mean session.completed"
    ), one
    many = unknown_event_types_sentence(["zzz.qqq", "yyy.ppp"], known)
    assert "so this webhook would never receive them." in many, many
    assert "be sent" not in one + many


# --------------------------------------------------------------------------
# 6. A8 — Settings' restart is a fresh start, like the tray's and the window's
# --------------------------------------------------------------------------

_FRESH = r"""
__EXTRACT__
const { EventEmitter } = require("events");
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null, userDataDir = "X";
let authToken = "t";
const logs = []; const menus = [];
let tray = { setToolTip(t) { logs.push("tooltip:" + t); } };
function desktopLog() {}
function fileLogger() { return (s) => logs.push(String(s).trim()); }
class Notification { constructor(o) { this.o = o; } show() {} }
function adoptOrReplaceExistingDaemon() {}
function verifyInstallIntegrity() { return { ok: true, checked: 1 }; }
function handleCorruptInstall() {}
function refreshTrayMenu() { menus.push(1); }
function armDashboardReload() {}
function killChild() {}
const http = {
  request(opts, cb) {
    const req = new EventEmitter();
    req.setTimeout = () => {};
    // The daemon answers, then stops by itself.
    req.end = () => setImmediate(() => { daemonProc.exitCode = 3; daemonProc.emit("close", 3); });
    return req;
  },
};
for (const d of ["WINDOWS_SHUTDOWN_EXIT_CODES", "WINDOWS_SHUTDOWN_GRACE_MS", "windowsSessionEnding",
                 "RESTART_BACKOFF_MS", "FAST_DEATH_MS", "FAST_DEATHS_TO_VERIFY", "RESTART_CAP_MAX",
                 "RESTART_CAP_WINDOW_MS", "DEATHS_DAY_MS", "DEATHS_DAY_TOAST_AT", "_services", "_trayDegraded",
                 "DAEMON_PORT", "_dwKills", "_dwBreakerTripped", "TIDY_RESTART_KILL_MS"]) eval(decl(d));
for (const f of ["isWindowsShutdownExit", "markTrayDegraded", "markTrayHealthy", "startService", "notifyCrashLoop",
                 "restartDaemonFromShell", "postDaemonShutdown", "restartDaemonTidy"]) eval(fnSource(f));
try { eval(fnSource("restartAdoptedDaemonTidy")); } catch { /* not in this version */ }
const realSetTimeout = setTimeout;
global.setTimeout = () => ({}); global.clearTimeout = () => {};
let spawns = 0;
const spawnFn = () => {
  spawns += 1; const c = new EventEmitter(); c.pid = 100 + spawns; c.exitCode = null; c.signalCode = null; return c;
};
startService("daemon", spawnFn);
const rec = _services.daemon;
// Nine crashes in the last quarter hour, and the watchdog's breaker open.
const now = Date.now();
rec.restarts = 3; rec.restartTimes = Array.from({ length: 9 }, (_, i) => now - i * 1000);
_dwKills = [now, now, now]; _dwBreakerTripped = true;
markTrayDegraded("daemon", "Iron Jarvis keeps stalling (Restart from the tray)");
const ret = restartDaemonTidy();
realSetTimeout(() => {
  process.stdout.write(JSON.stringify({
    ret, spawns, restarts: rec.restarts, restartTimes: rec.restartTimes.length,
    dwKills: _dwKills.length, breaker: _dwBreakerTripped, degraded: Array.from(_trayDegraded),
  }) + "\n");
}, 30);
"""


@requires_node
def test_settings_restart_is_a_fresh_start_like_the_tray_and_the_window(tmp_path):
    """The tray's and the window's "Restart Iron Jarvis" reset the crash
    ladder and the watchdog's breaker: the user asked for a fresh start.
    Settings' tidy restart left them, so one more crash after it hit the
    10-in-15-minutes cap, and a daemon that stalled again was never killed."""
    out = _run(_FRESH.replace("__EXTRACT__", _EXTRACT), tmp_path, "fresh")
    assert out["ret"] is True and out["spawns"] == 2, out
    assert out["restarts"] == 0 and out["restartTimes"] == 0, out
    assert out["dwKills"] == 0 and out["breaker"] is False, out
    assert "daemon" not in out["degraded"], out


_PORT = r"""
__EXTRACT__
const net = require("net");
eval(fnSource("daemonPortHeld"));
eval(fnSource("waitForDaemonPortFree"));
var DAEMON_PORT = 0;
(async () => {
  const holder = net.createServer();
  await new Promise((r) => holder.listen({ host: "127.0.0.1", port: 0 }, r));
  DAEMON_PORT = holder.address().port;  // a free port of our own, never 8787
  const out = {};
  out.heldWhileListening = await daemonPortHeld();
  const t0 = Date.now();
  out.freedWhileHeld = await waitForDaemonPortFree(600);
  out.waitedMs = Date.now() - t0;
  await new Promise((r) => holder.close(r));
  out.heldAfterClose = await daemonPortHeld();
  out.freedAfterClose = await waitForDaemonPortFree(2000);
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


@requires_node
def test_the_port_probe_tells_a_held_port_from_a_free_one(tmp_path):
    out = _run(_PORT.replace("__EXTRACT__", _EXTRACT), tmp_path, "port")
    assert out["heldWhileListening"] is True, out
    assert out["freedWhileHeld"] is False and out["waitedMs"] >= 500, out
    assert out["heldAfterClose"] is False and out["freedAfterClose"] is True, out
