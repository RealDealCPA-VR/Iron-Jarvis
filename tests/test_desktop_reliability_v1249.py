"""v1.249.0 — the desktop app survives the things that used to end the day.

Four items from the reliability ballot, all in ``desktop/main.js``:

R-02 An update force-killed the daemon in 0.6 s. Now the cached installer is
     checked BEFORE anything stops (a bad download can never cost a live
     daemon), the daemon gets the same tidy stop Quit gives it, a handoff that
     fails brings the children back, and the busy dialog says what is actually
     running — including a chat reply being written and a Build pane with
     Claude in it — with a third answer that waits for idle.
R-03 A job waiting on the user was invisible once the window was closed to the
     tray (that destroys the renderer, so the page's bell cannot speak). The
     main process watches the same listing and says so in the tray.
R-04 Windows shutting down was read as a crash: "restart #1" into a machine
     that was going away, counted toward the crash toast. And the app was
     never offered start-with-Windows, so an overnight update left it closed.
R-06 Every startup failure said "another program is using port 8787" — a
     locked database, a failed data upgrade, a quarantined exe, all of it —
     and then quit. Now the cause is named and Retry exists.

House idiom (``test_desktop_reliability_v1226.py``): the shipped source is
LIFTED out of main.js verbatim and run under node against stubs, so reverting
a fix turns these red. Seams that cannot execute here (the `session-end` hook,
the startup wiring) are pinned against the source instead.

NOT IN SCOPE, deliberately: [R-01] "updates reopen the app by themselves" was
REJECTED. ``test_an_update_still_never_reopens_the_app_by_itself`` is the
guard that nothing in this wave quietly implemented it.
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
    # CRLF on the CI runner, LF here (v1.232.1): normalise at the READER.
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str, end_after: str) -> str:
    """main.js from ``start_marker`` to the column-0 ``}`` that closes the
    function beginning at ``end_after``. Lifted, never copied."""
    src = _src()
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _decl(name: str) -> str:
    """One top-level ``const``/``let`` line, verbatim. The ladder's thresholds
    are the shipped ones — a copy here would keep passing after a retune."""
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _run(script: str, tmp_path: Path, name: str, *argv: str, env: dict | None = None) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ==========================================================================
# R-04 — Windows shutting down is not a crash
# ==========================================================================

_WINDOWS_HARNESS = """
const { EventEmitter } = require("events");
const logs = [];
const notes = [];
let shuttingDown = false, isQuitting = false, daemonProc = null, dashboardProc = null;
let tray = { setToolTip: (s) => logs.push("tooltip:" + s) };
class Notification { constructor(o) { this.o = o; } show() { notes.push(this.o.body); } }
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
function fileLogger(label) { return (s) => logs.push(`${label}: ${String(s).trim()}`); }
function verifyInstallIntegrity() { return { ok: true, checked: 1, missing: [], mismatched: [] }; }
function handleCorruptInstall() {}
function refreshTrayMenu() {}
function armDashboardReload() {}
function reportIncident() {}
function killChild() {}
__DECLS__
__WINDOWS__
__LADDER__
const scenario = process.argv[2];
let spawns = 0;
const spawnFn = () => { spawns += 1; const c = new EventEmitter(); c.pid = 100 + spawns; c.exitCode = null; c.signalCode = null; return c; };
const child = startService("daemon", spawnFn);
if (scenario === "session-end") markWindowsSessionEnding("shutting down");
// The session-end case exits with an ORDINARY code on purpose: only the
// session-end flag can spare it, so this scenario cannot pass by falling
// through to the exit-code branch (a mutation check caught exactly that).
const code = scenario === "shutdown-code" ? 0x40010004 : 1;
child.exitCode = code;
child.emit("close", code);
const spawnsRightAfter = spawns;
// Past the ladder's first backoff rung (so an ordinary crash HAS respawned by
// now) and far short of the Windows grace window (so a shutdown has not).
setTimeout(() => {
  process.stdout.write(JSON.stringify({
    spawns, spawnsRightAfter, restarts: (_services.daemon || {}).restarts || 0,
    deaths: ((_services.daemon || {}).deaths || []).length,
    logs, notes,
  }) + "\\n");
}, RESTART_BACKOFF_MS[0] + 400);
"""


def _windows_harness() -> str:
    windows = _lift("const WINDOWS_SHUTDOWN_EXIT_CODES", "function markWindowsSessionEnding")
    # The real ladder, its real thresholds, and the two helpers its death path
    # reaches (the tray verdict and the crash toast) — all lifted, so a
    # retuned threshold or a deleted seam shows up here.
    decls = "\n".join(
        _decl(n)
        for n in (
            "RESTART_BACKOFF_MS", "FAST_DEATH_MS", "FAST_DEATHS_TO_VERIFY", "RESTART_CAP_MAX",
            "RESTART_CAP_WINDOW_MS", "DEATHS_DAY_MS", "DEATHS_DAY_TOAST_AT",
            "_services", "_trayDegraded",
        )
    )
    ladder = "\n".join(
        _lift(f"function {n}", f"function {n}")
        for n in ("markTrayDegraded", "markTrayHealthy", "notifyCrashLoop", "startService")
    )
    assert "isWindowsShutdownExit" in windows and "markWindowsSessionEnding" in windows
    assert "onGone" in ladder, "the ladder's death handler moved"
    # The grace window is real time here: nothing is shrunk, so the scenarios
    # assert the DEFERRAL (no respawn inside the window) rather than its length.
    return (
        _WINDOWS_HARNESS.replace("__DECLS__", decls)
        .replace("__WINDOWS__", windows)
        .replace("__LADDER__", ladder)
    )


@requires_node
def test_a_child_killed_by_a_windows_shutdown_is_not_a_crash(tmp_path):
    """THE 03:29 RESTART. Windows terminates the daemon with
    DBG_TERMINATE_PROCESS; the supervisor logged "unexpected exit — restart
    #1", counted it toward the 24-hour crash toast, and spawned a new daemon
    into a machine that was already going down."""
    out = _run(_windows_harness(), tmp_path, "windows", "shutdown-code")
    assert out["spawnsRightAfter"] == 1, "a respawn was issued immediately"
    assert out["spawns"] == 1, "the service was restarted during the shutdown window"
    assert out["restarts"] == 0, "the shutdown was counted as a restart"
    assert out["deaths"] == 0, "the shutdown was counted toward the crash toast"
    assert out["notes"] == [], "the user was toasted about a normal Windows restart"
    assert any("looks like a Windows shutdown" in line for line in out["logs"]), out["logs"]


@requires_node
def test_the_session_end_signal_ends_the_restarts_outright(tmp_path):
    """With Windows having SAID it is ending the session, there is nothing to
    decide and no grace window to wait out."""
    out = _run(_windows_harness(), tmp_path, "windows-end", "session-end")
    assert out["spawns"] == 1, "the child was restarted into a closing session"
    assert out["restarts"] == 0 and out["deaths"] == 0
    # The ladder's OWN line, not the one markWindowsSessionEnding wrote: this
    # is what says the death reached onGone and was spared there.
    assert any("not restarting" in line for line in out["logs"]), out["logs"]


@requires_node
def test_an_ordinary_crash_is_still_a_crash(tmp_path):
    """THE ANTI-VACUITY CONTROL: the seam must not have turned the supervisor
    off. An exit(1) still restarts, still counts."""
    out = _run(_windows_harness(), tmp_path, "windows-ordinary", "ordinary")
    assert out["spawns"] == 2, f"a real crash was not restarted: {out}"
    assert out["restarts"] == 1
    assert out["deaths"] == 1


def test_a_shutdown_shaped_exit_only_defers_the_respawn():
    """A one-off odd exit code must not leave the service dead forever: the
    exit code alone is not proof, so the respawn is DEFERRED, not refused."""
    src = _src()
    m = re.search(r"if \(isWindowsShutdownExit\(code\)\) \{(.{0,900}?)\n      return;", src, re.S)
    assert m, "the shutdown-shaped-exit branch moved"
    body = m.group(1)
    assert "startService(label, rec.spawnFn);" in body, (
        "a shutdown-shaped exit now permanently refuses to bring the service back"
    )
    assert "WINDOWS_SHUTDOWN_GRACE_MS" in body, "the respawn is no longer delayed"
    assert "windowsSessionEnding" in body, (
        "the deferred respawn no longer re-checks whether Windows really did go down"
    )


def test_windows_tells_us_the_session_is_ending_through_both_doors():
    """A renderer `session-end` and powerMonitor's shutdown are the only two
    warnings Electron gives; both must reach the same flag."""
    src = _src()
    assert 'mainWin.on("session-end"' in src, "the window's session-end warning is not handled"
    assert 'powerMonitor.on("shutdown"' in src, "powerMonitor's shutdown is not handled"
    assert len(re.findall(r"markWindowsSessionEnding\(", src)) >= 3, (
        "one of the shutdown doors does not set the flag"
    )
    assert re.search(r"const \{[^}]*powerMonitor[^}]*\} = require\(\"electron\"\)", src, re.S), (
        "powerMonitor is not imported"
    )


def test_start_with_windows_is_offered_once_and_never_again():
    """The other half of R-04: start-at-login exists, ships OFF, and nobody
    finds it. Asked once, with Yes pre-selected — and the asked-flag is
    written BEFORE the dialog, so a crash mid-answer cannot ask forever."""
    src = _src()
    fn = _lift("function maybeOfferStartWithWindows", "function maybeOfferStartWithWindows")
    assert "IS_PACKAGED" in fn and 'process.platform !== "win32"' in fn, (
        "a dev run or a non-Windows box would be asked"
    )
    assert "START_HIDDEN" in fn, "a tray-only login start would be asked at every login"
    assert fn.index('writeDesktopSetting("startWithWindowsAsked", true)') < fn.index(
        "showMessageBox"
    ), "the asked-flag is written after the dialog: a crash would re-ask forever"
    assert "if (getStartAtLogin()) return;" in fn, "it offers what is already on"
    assert '"Yes, start with Windows"' in fn and "defaultId: 0" in fn
    assert "setStartAtLogin(true);" in fn
    assert "maybeOfferStartWithWindows();" in src, "nothing ever calls it"


# ==========================================================================
# R-02 — the update install: check first, stop tidily, come back if it fails
# ==========================================================================

_INSTALLER_HARNESS = """
const fs = require("fs"), path = require("path"), crypto = require("crypto");
__LIFTED__
process.stdout.write(JSON.stringify({ state: cachedInstallerState() }) + "\\n");
"""


def _installer_state(tmp_path: Path, *, info: dict | None, exe: bytes | None, name: str) -> str:
    local = tmp_path / name
    pending = local / "iron-jarvis-desktop-updater" / "pending"
    pending.mkdir(parents=True)
    if info is not None:
        (pending / "update-info.json").write_text(json.dumps(info), encoding="utf-8")
    if exe is not None:
        (pending / "Iron-Jarvis-Setup-1.249.0.exe").write_bytes(exe)
    script = _INSTALLER_HARNESS.replace(
        "__LIFTED__", _lift("function cachedInstallerState", "function cachedInstallerState")
    )
    return _run(script, tmp_path, f"installer-{name}", env={"LOCALAPPDATA": str(local)})["state"]


@requires_node
def test_the_cached_installer_is_judged_against_its_own_checksum(tmp_path):
    """Driven against a REAL directory, because this reads the layout
    electron-updater writes — a stubbed fs would prove nothing about it."""
    import base64
    import hashlib

    body = b"NSIS installer bytes"
    good = base64.b64encode(hashlib.sha512(body).digest()).decode()
    name = "Iron-Jarvis-Setup-1.249.0.exe"
    assert _installer_state(
        tmp_path, info={"fileName": name, "sha512": good}, exe=body, name="ok"
    ) == "ok"
    assert _installer_state(
        tmp_path, info={"fileName": name, "sha512": good}, exe=None, name="gone"
    ) == "missing"
    assert _installer_state(
        tmp_path, info={"fileName": name, "sha512": good}, exe=b"half a down", name="partial"
    ) == "corrupt"
    # Nothing to check against is NOT a failure: install exactly as before.
    assert _installer_state(tmp_path, info=None, exe=body, name="nomarker") == "unknown"
    assert _installer_state(
        tmp_path, info={"fileName": name}, exe=body, name="nosum"
    ) == "unknown"


_APPLY_HARNESS = """
const { EventEmitter } = require("events");
const calls = [];
const MODE = process.argv[2];
let pendingUpdateInfo = { version: "1.249.0" };
let updateInstallInFlight = false, isQuitting = false, shuttingDown = false;
let tray = { setToolTip: (s) => calls.push(["tooltip", s]) };
const app = { quit: () => calls.push(["app.quit"]), relaunch: () => calls.push(["app.relaunch"]) };
const dialog = { showMessageBoxSync: (o) => { calls.push(["dialog", String(o && o.message)]); return 0; } };
let daemonProc = { pid: 11, exitCode: null, signalCode: null };
let dashboardProc = { pid: 12, exitCode: null, signalCode: null };
const _services = { daemon: { spawnFn: () => ({ pid: 21, exitCode: null, signalCode: null }), restarts: 0 } };
function startService(label) { calls.push(["startService", label]); return { pid: 31, exitCode: null, signalCode: null }; }
function cachedInstallerState() { return MODE === "missing" ? "missing" : MODE === "corrupt" ? "corrupt" : MODE === "unknown" ? "unknown" : "ok"; }
// A tidy stop that SUCCEEDS means the daemon process is gone — model that, or
// respawnStoppedServices rightly finds a live child and this harness would
// assert a respawn that the real code never needed to make.
function requestDaemonShutdown(ms) {
  calls.push(["requestDaemonShutdown", ms]);
  if (MODE === "stopthrow") return Promise.reject(new Error("the post failed"));
  if (MODE === "nostop") return Promise.resolve(false);
  daemonProc.exitCode = 0;
  return Promise.resolve(true);
}
function killChild(child, label, reason) {
  calls.push(["killChild", label, reason]);
  if (child) child.exitCode = 1;
}
function markUpdatePending(v) { calls.push(["markUpdatePending", v]); }
function clearUpdatePending() { calls.push(["clearUpdatePending"]); }
function shutdown(reason) { calls.push(["shutdown", String(reason)]); shuttingDown = true; }
function sweepOrphanDaemons() { calls.push(["sweepOrphanDaemons"]); }
function refreshTrayMenu() { calls.push(["refreshTrayMenu"]); }
function friendlyUpdateError(m) { return m; }
function _emitUpdateState(p) { calls.push(["updateState", p.status]); }
function desktopLog() {}
__LIFTED__
class FailingUpdater extends EventEmitter {
  quitAndInstall(...args) {
    calls.push(["quitAndInstall", JSON.stringify(args)]);
    this.emit("error", new Error("No update filepath provided, can't quit and install"));
  }
}
class OkUpdater extends EventEmitter {
  quitAndInstall(...args) { calls.push(["quitAndInstall", JSON.stringify(args)]); }
}
let _autoUpdater = MODE === "fail" ? new FailingUpdater() : new OkUpdater();
(async () => {
  await applyPendingUpdate();
  process.stdout.write(JSON.stringify({
    calls, isQuitting, shuttingDown, updateInstallInFlight, pendingUpdateInfo,
    daemonRespawned: calls.some((c) => c[0] === "startService"),
  }) + "\\n");
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _apply(mode: str, tmp_path: Path) -> dict:
    # Lifted as one piece: respawnStoppedServices is what brings the daemon
    # back, and abortUpdateInstall owns "the update is not ready" — stubbing
    # either would leave this harness asserting its own stubs.
    lifted = _decl("UPDATE_TIDY_SHUTDOWN_MS") + "\n" + _lift(
        "function respawnStoppedServices", "function abortUpdateInstall"
    )
    assert "quitAndInstall" in lifted and "respawnStoppedServices" in lifted
    assert "clearUpdatePending()" in lifted, "the abort path is no longer lifted"
    script = _APPLY_HARNESS.replace("__LIFTED__", lifted)
    return _run(script, tmp_path, f"apply-{mode}", mode)


@requires_node
def test_the_daemon_gets_the_tidy_stop_quit_gives_it(tmp_path):
    """THE 0.6 SECONDS. The install taskkilled the tree, so uvicorn never
    drained, the WAL was never folded and the terminal snapshot was never
    written. Now: the same graceful stop, and the force-kill only if it does
    not go."""
    out = _apply("ok", tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "requestDaemonShutdown" in names, "the daemon is still force-killed with no warning"
    assert names.index("requestDaemonShutdown") < names.index("quitAndInstall"), (
        "the installer was handed off before the daemon was asked to stop"
    )
    assert "killChild" not in names, "a daemon that stopped tidily was killed anyway"
    budget = next(c for c in out["calls"] if c[0] == "requestDaemonShutdown")[1]
    assert budget >= 5000, f"the tidy stop is only {budget}ms — too short to fold the WAL"
    for step in ("markUpdatePending", "quitAndInstall", "shutdown", "sweepOrphanDaemons"):
        assert step in names, f"{step} missing from the install path"
    assert names.index("markUpdatePending") < names.index("quitAndInstall")
    assert names.index("quitAndInstall") < names.index("shutdown")


@requires_node
@pytest.mark.parametrize("mode", ["nostop", "stopthrow"])
def test_a_daemon_that_will_not_stop_is_still_killed(mode, tmp_path):
    """NSIS must not meet a locked frozen exe: tidy first, forceful second."""
    out = _apply(mode, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert ["killChild", "daemon", "update"] in out["calls"], out["calls"]
    assert names.index("killChild") < names.index("quitAndInstall")
    assert "shutdown" in names, "the install did not proceed"


@requires_node
@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_a_bad_download_never_costs_a_live_daemon(mode, tmp_path):
    """THE ORDERING THAT MATTERS. The known trigger is a cached download the
    30-minute re-check invalidated while pendingUpdateInfo stayed set. Check
    the file FIRST: nothing is stopped for an install that cannot happen."""
    out = _apply(mode, tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "requestDaemonShutdown" not in names, "the daemon was stopped for a dead install"
    assert "killChild" not in names and "shutdown" not in names
    assert "quitAndInstall" not in names, "it handed off an installer it knew was not there"
    assert out["isQuitting"] is False, "left mid-quit: a window close would tear the app down"
    assert "dialog" in names, "the user was told nothing"
    assert out["updateInstallInFlight"] is False, "a retry would be locked out forever"
    assert out["pendingUpdateInfo"] is None, "the tray still claims an update is ready"
    assert "clearUpdatePending" in names, "the recovery marker would misreport the next boot"


@requires_node
def test_an_install_that_will_not_start_brings_the_daemon_back(tmp_path):
    """The tidy stop already happened, so the old zombie guarantee is not
    enough any more: staying resident with a DEAD daemon is the same outage by
    another route. The respawn must land BEFORE the dialog the user reads."""
    out = _apply("fail", tmp_path)
    names = [c[0] for c in out["calls"]]
    assert out["daemonRespawned"] is True, "the app is resident with no daemon"
    assert names.index("startService") < names.index("dialog"), (
        "the user reads the error while the app is still broken"
    )
    assert "shutdown" not in names, "children were torn down for an install that never started"
    assert "sweepOrphanDaemons" not in names, "the sweep kills our own daemon by image name"
    assert out["shuttingDown"] is False, "crash supervisor left permanently disabled"
    assert out["isQuitting"] is False
    assert out["updateInstallInFlight"] is False


@requires_node
def test_an_unknown_installer_layout_installs_exactly_as_before(tmp_path):
    """Best-effort by contract: a dev run tells us nothing, so proceed."""
    out = _apply("unknown", tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "quitAndInstall" in names and "shutdown" in names


@requires_node
def test_an_update_still_never_reopens_the_app_by_itself(tmp_path):
    """[R-01] WAS REJECTED. The user asked for updates NOT to reopen the app,
    and this wave touched the install path all over — so pin the handoff's
    arguments and the absence of any relaunch."""
    out = _apply("ok", tmp_path)
    handoff = next(c for c in out["calls"] if c[0] == "quitAndInstall")
    assert handoff[1] == "[false,true]", (
        f"quitAndInstall's arguments changed to {handoff[1]} — isSilent/isForceRunAfter "
        "are exactly what R-01 was about"
    )
    assert "app.relaunch" not in [c[0] for c in out["calls"]]
    src = _src()
    fn = _lift("async function applyPendingUpdate", "function applyPendingUpdate")
    assert "relaunch" not in fn, "the install path now relaunches the app"
    assert "quitAndInstall(false, true)" in src


# --- what the user is told is about to stop -------------------------------

_DESCRIBE_HARNESS = """
__LIFTED__
const cases = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify({ said: cases.map((a) => describeBusyWork(a)) }) + "\\n");
"""


def _describe(cases: list, tmp_path: Path) -> list[str]:
    script = _DESCRIBE_HARNESS.replace(
        "__LIFTED__", _lift("function describeBusyWork", "function describeBusyWork")
    )
    return _run(script, tmp_path, "describe", json.dumps(cases))["said"]


@requires_node
def test_the_warning_names_the_work_the_old_one_could_not_see(tmp_path):
    """The dialog counted Session rows and workflow runs. A chat reply has no
    Session row (it runs as session id "chat") and a Build pane has none
    either, so both were ended without ever being mentioned."""
    said = _describe(
        [
            {"chat_replies": 1, "busy": True},
            {"busy_panes": 2, "busy_pane_clis": ["claude", "codex"], "busy": True},
            {"busy_panes": 1, "busy_pane_clis": ["claude", "claude"], "busy": True},
            {
                "chat_replies": 1,
                "busy_panes": 2,
                "busy_pane_clis": ["claude"],
                "active_sessions": 3,
                "running_workflow_runs": 1,
                "busy": True,
            },
            {"busy_panes": 1, "busy": True},
            {},
            {"chat_replies": "2"},
            {"chat_replies": -1, "busy_panes": None},
        ],
        tmp_path,
    )
    assert said[0] == "1 chat reply in progress"
    assert said[1] == "2 Build panes working (Claude, Codex)"
    assert said[2] == "1 Build pane working (Claude)", "the same CLI was named twice"
    assert said[3] == (
        "1 chat reply in progress · 2 Build panes working (Claude) · "
        "3 background jobs running · 1 workflow run running"
    )
    assert said[4] == "1 Build pane working"
    # Never an empty sentence, and never a negative or nonsense count.
    assert said[5] == "Work is still in progress"
    assert said[6] == "2 chat replies in progress"
    assert said[7] == "Work is still in progress"


# ==========================================================================
# R-03 — a job waiting on the user is visible with the window closed
# ==========================================================================

_ASK_HARNESS = """
const notes = [];
const tips = [];
let tray = { setToolTip: (s) => tips.push(s) };
const _trayDegraded = new Set();
__LIFTED__
__TOOLTIP__
const s = JSON.parse(process.argv[2]);
let seen = {};
const out = [];
for (const step of s.steps) {
  const plan = planAskNotifications(seen, step.approvals, step.now, !!step.windowOpen);
  seen = plan.seen;
  out.push({ notify: plan.notify, waiting: plan.waiting });
}
if (s.degraded) _trayDegraded.add("daemon");
if (s.tooltip !== undefined) setAskWaitingTooltip(s.tooltip);
process.stdout.write(JSON.stringify({ out, tips }) + "\\n");
"""

_HOUR = 60 * 60 * 1000


def _asks(state: dict, tmp_path: Path) -> dict:
    script = _ASK_HARNESS.replace(
        "__LIFTED__", _lift("const ASK_WATCH_MS", "function planAskNotifications")
    ).replace(
        "__TOOLTIP__", _lift("function setAskWaitingTooltip", "function setAskWaitingTooltip")
    )
    return _run(script, tmp_path, "asks", json.dumps(state))


@requires_node
def test_a_waiting_job_is_announced_once_then_reminded(tmp_path):
    """Since v1.247.0 an attended ask waits with NO expiry — so the ONE
    surface that spoke for it (the page's bell) is gone the moment the window
    is closed to the tray, and a job can sit there all day."""
    ask = {"id": "ap_1", "tool": "rename_file", "count": 4, "session_id": "sess_7"}
    out = _asks(
        {
            "steps": [
                {"approvals": [ask], "now": 0},
                {"approvals": [ask], "now": 25_000},
                {"approvals": [ask], "now": _HOUR + 1000},
                {"approvals": [ask], "now": _HOUR + 2000},
                {"approvals": [ask], "now": 8 * _HOUR + 1000},
                {"approvals": [ask], "now": 30 * _HOUR},
            ]
        },
        tmp_path,
    )["out"]
    first = out[0]["notify"]
    assert len(first) == 1, "the waiting job was never announced"
    assert first[0]["title"] == "Jarvis is waiting for you"
    assert first[0]["body"] == "rename 4 files — click to open the job."
    assert first[0]["sessionId"] == "sess_7", "the click cannot open the job"
    assert out[1]["notify"] == [], "it re-announced the same ask on the next tick"
    assert len(out[2]["notify"]) == 1, "no reminder after an hour"
    assert "waiting 1 hour" in out[2]["notify"][0]["body"]
    assert out[3]["notify"] == [], "the reminder repeated on the next tick"
    assert "waiting 8 hours" in out[4]["notify"][0]["body"]
    assert out[5]["notify"] == [], "it keeps nagging forever"
    assert all(s["waiting"] == 1 for s in out)


@requires_node
def test_the_page_owns_the_announcing_while_a_window_is_open(tmp_path):
    """Two toasts for one ask is worse than none: while a window is open the
    bell in front of the user owns the announcing. The ask is still RECORDED,
    so closing the window later brings a REMINDER of how long it has waited —
    never the first-notice toast for something the user already saw."""
    ask = {"id": "ap_1", "tool": "shell", "count": 1}
    out = _asks(
        {
            "steps": [
                {"approvals": [ask], "now": 0, "windowOpen": True},
                {"approvals": [ask], "now": 5 * _HOUR, "windowOpen": False},
                {"approvals": [ask], "now": 5 * _HOUR + 25_000, "windowOpen": False},
            ]
        },
        tmp_path,
    )["out"]
    assert out[0]["notify"] == [], "it toasted over the bell the user is looking at"
    assert out[0]["waiting"] == 1, "the tray does not know a job is waiting"
    later = out[1]["notify"]
    assert len(later) == 1 and later[0]["title"] == "Still waiting for you", (
        "closing the window re-announced the ask as if it were new"
    )
    assert later[0]["body"].startswith("run a command has been waiting 5 hours"), (
        f"the reminder misreports how long the job has waited: {later[0]['body']}"
    )
    assert out[2]["notify"] == [], "the reminder repeated on the next tick"


@requires_node
def test_an_answered_ask_is_forgotten(tmp_path):
    """The listing is the truth: an ask that is gone must leave no state
    behind, or the tooltip counts jobs nobody is waiting on."""
    ask = {"id": "ap_1", "tool": "write_document"}
    out = _asks(
        {"steps": [{"approvals": [ask], "now": 0}, {"approvals": [], "now": 60_000}]}, tmp_path
    )["out"]
    assert out[0]["waiting"] == 1
    assert out[1]["waiting"] == 0 and out[1]["notify"] == []


@requires_node
def test_the_asks_are_described_in_plain_words_and_never_quote_arguments(tmp_path):
    """v1.247.0's rule: listings carry NUMBERS, never arguments — a toast on a
    shared screen must not read out a client's file name."""
    out = _asks(
        {
            "steps": [
                {
                    "approvals": [
                        {"id": "a", "tool": "rename_file", "count": 1, "path": "C:/clients/Smith 1040.pdf"},
                        {"id": "b", "tool": "excel_edit", "count": 3},
                        {"id": "c", "tool": "some_new_tool", "count": 2},
                        {"id": "d"},
                    ],
                    "now": 0,
                }
            ]
        },
        tmp_path,
    )["out"]
    bodies = [n["body"] for n in out[0]["notify"]]
    assert bodies[0].startswith("rename a file — ")
    assert bodies[1].startswith("edit 3 workbooks — ")
    assert bodies[2].startswith("use some new tool (2 times) — ")
    assert bodies[3].startswith("use a tool — ")
    assert not any("Smith" in b or ".pdf" in b for b in bodies), bodies


@requires_node
def test_the_tray_tooltip_counts_waiting_jobs_but_yields_to_a_broken_service(tmp_path):
    assert _asks({"steps": [], "tooltip": 2}, tmp_path)["tips"] == [
        "Iron Jarvis — 2 jobs waiting for you"
    ]
    assert _asks({"steps": [], "tooltip": 1}, tmp_path)["tips"] == [
        "Iron Jarvis — 1 job waiting for you"
    ]
    assert _asks({"steps": [], "tooltip": 0}, tmp_path)["tips"] == ["Iron Jarvis — running"]
    assert _asks({"steps": [], "tooltip": 3, "degraded": True}, tmp_path)["tips"] == [], (
        "a waiting job overwrote the louder truth that a service is down"
    )


def test_the_ask_watcher_is_actually_installed_and_reads_the_bell_s_listing():
    """SHIPPING THE MECHANISM IS NOT SHIPPING THE FEATURE (v1.218.0)."""
    src = _src()
    assert "installAskWatcher();" in src, "the watcher is never started"
    fn = _lift("function installAskWatcher", "function installAskWatcher")
    assert '"/chat/approvals/pending"' in fn, "it does not read the listing the bell reads"
    assert "setInterval(tick, ASK_WATCH_MS)" in fn
    assert "mainWin && !mainWin.isDestroyed()" in fn, (
        "it cannot tell whether a window is open, so it would toast over the bell"
    )
    click = _lift("function showAskNotification", "function showAskNotification")
    assert "showMainWindow();" in click, "the toast click does not rebuild a destroyed window"
    assert "/sessions/" in click, "the click does not open the job"


# ==========================================================================
# R-06 — a startup failure says what went wrong, and offers Retry
# ==========================================================================

_CLASSIFY_HARNESS = """
const DAEMON_PORT = 8787;
const STARTUP_TIMEOUT_MS = 90000;
__LIFTED__
const cases = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify({ out: cases.map((c) => classifyStartupFailure(c)) }) + "\\n");
"""


def _classify(cases: list, tmp_path: Path) -> list[dict]:
    script = _CLASSIFY_HARNESS.replace(
        "__LIFTED__", _lift("function classifyStartupFailure", "function classifyStartupFailure")
    )
    return _run(script, tmp_path, "classify", json.dumps(cases))["out"]


@requires_node
def test_every_startup_failure_used_to_say_port_8787(tmp_path):
    """The old dialog named a port conflict for a locked database, a failed
    data upgrade and a quarantined exe alike — then quit. Each cause now gets
    words the user can act on."""
    out = _classify(
        [
            {"gateError": "[Errno 10048] error while attempting to bind on address"},
            {"logTail": "OSError: [WinError 10048] Only one usage of each socket address"},
            {"logTail": "sqlite3.OperationalError: database is locked"},
            {"logTail": "sqlite3.OperationalError: no such column: session.interrupted_at"},
            {"logTail": "Error: spawn ENOENT", "label": "dashboard"},
            {"logTail": 'Traceback (most recent call last):\\n  File "app.py"'},
            {"exitCode": 3},
            {"gateError": "timed out waiting for /health", "timeoutS": 90},
        ],
        tmp_path,
    )
    causes = [o["cause"] for o in out]
    assert causes == [
        "port", "port", "db-locked", "upgrade", "missing", "crash", "crash", "timeout",
    ], causes
    assert "8787" in out[0]["detail"] and "Retry" in out[0]["detail"]
    assert "Your data is untouched" in out[2]["detail"], "a locked DB reads like data loss"
    assert "Nothing has been deleted" in out[3]["detail"]
    assert out[4]["message"].startswith("The Iron Jarvis dashboard"), (
        "a dashboard failure is reported as the service"
    )
    assert "antivirus" in out[4]["detail"]
    assert "90 seconds" in out[7]["message"]
    for o in out:
        for text in (o["message"], o["detail"]):
            assert "Traceback" not in text and "Errno" not in text, (
                f"the dialog shows developer text: {text}"
            )


@requires_node
def test_the_cause_is_read_from_the_log_the_user_never_opens(tmp_path):
    """The gate's own error says "did not answer in time" for all of these —
    the reason is in the child's log file, which is why readLogTail exists."""
    out = _classify(
        [
            {"gateError": "daemon did not answer in time", "logTail": "database is locked"},
            {"gateError": "daemon did not answer in time", "logTail": ""},
        ],
        tmp_path,
    )
    assert out[0]["cause"] == "db-locked"
    assert out[1]["cause"] == "timeout"


def test_a_failed_start_offers_retry_instead_of_only_quitting():
    """It used to show one error box and quit — on a transient port conflict
    the user's only recovery was to relaunch and hope."""
    src = _src()
    fn = _lift("function handleStartupFailure", "function handleStartupFailure")
    assert '"Retry", "Open logs", "Quit"' in fn, "the recovery dialog does not offer Retry"
    assert "app.relaunch();" in fn and "app.exit(0);" in fn, "Retry does not restart the app"
    assert "openLogsFolder();" in fn and "continue;" in fn, (
        "Open logs ends the dialog, so Retry is no longer one click away"
    )
    assert "classifyStartupFailure(" in fn, "the dialog does not name the cause"
    assert "readLogTail(" in fn, "the child's log is never read"
    # The update-recovery branch (v1.192.0) must still come first.
    assert fn.index("pendingUpdate.attempts >= 2") < fn.index("classifyStartupFailure")
    # Both gate call sites must pass the context the classifier needs.
    for m in re.finditer(r"handleStartupFailure\(\s*\"Iron Jarvis[^)]*?\)\s*;", src, re.S):
        assert "label:" in m.group(0), f"a call site passes no context: {m.group(0)[:120]}"


def test_the_dialog_still_shows_the_underlying_error(tmp_path):
    """R-06 replaces the WORDS, not the evidence: the raw gate error stays in
    the detail so a report can still be diagnosed."""
    fn = _lift("function handleStartupFailure", "function handleStartupFailure")
    assert "Details: ${(context && context.gateError) || message}" in fn, (
        "the underlying error is no longer shown anywhere"
    )
