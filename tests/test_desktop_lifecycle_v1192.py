"""v1.192.0 — four Electron lifecycle defects in ``desktop/main.js``.

``desktop/`` has no test runner (the only mechanical check is ``node --check``),
so this follows the house idiom from ``test_desktop_trust_boundary_v1175.py``:
LIFT the real code out of ``main.js`` and run it under node against stubs, and
source-pin only the seams that cannot be executed (they live inside
``startup()`` / the ``before-quit`` closure).

The four findings:

22. ``autoInstallOnAppQuit`` installs an update on a REAL quit, and the
    ``before-quit`` handler already anticipates it (it sweeps orphan daemons so
    NSIS can extract) — but it never wrote ``markUpdatePending``. The entire
    failed-update recovery (marker -> ``readAndBumpUpdatePending`` -> the
    "update failed to start" dialog) was therefore bypassed for every
    quit-installed update.
23. A second launch DURING a ``--hidden`` login boot fell into an empty ``else``
    whose comment claimed "the in-flight startup will open the window itself".
    It does not: ``startup()`` skips the splash and its ``START_HIDDEN`` branch
    deliberately creates no window. Up to 90s of a packaged cold boot, an
    explicit launch produced NOTHING.
24. ``applyPendingUpdate()`` killed the daemon + dashboard (latching
    ``shuttingDown``, which permanently disables the crash supervisor) BEFORE
    ``quitAndInstall``. quitAndInstall never throws to the caller — every
    failure goes through electron-updater's ``error`` event, synchronously,
    with ``install()`` returning false — so a failed handoff left the app
    resident with dead children, no supervisor, a tray still claiming an update
    was ready, and no dialog anywhere.
42. The ``notify:show`` toast click did a guarded ``mainWin.show()`` and never
    rebuilt the window — but ``hideToTray()`` DESTROYS it, which is exactly the
    state the toast is clicked in.
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
    function that begins at ``end_after``.

    Lifted, never copied: if the shipped implementation changes, this test runs
    the CHANGED one.
    """
    src = _src()
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _run_node(script: str, tmp_path: Path) -> dict:
    f = tmp_path / "harness.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# 24 — the update-install handoff, executed for real
# --------------------------------------------------------------------------

_UPDATE_HARNESS = """
const { EventEmitter } = require("events");

const calls = [];
let pendingUpdateInfo = { version: "1.192.0" };
let updateInstallInFlight = false;
let isQuitting = false;
let shuttingDown = false;
let tray = { setToolTip: (s) => calls.push(["tooltip", s]) };
const app = { quit: () => calls.push(["app.quit"]) };
const dialog = {
  showMessageBoxSync: (o) => { calls.push(["dialog", String(o && o.title)]); return 0; },
};
function markUpdatePending(v) { calls.push(["markUpdatePending", v]); }
function clearUpdatePending() { calls.push(["clearUpdatePending"]); }
// The real shutdown() latches shuttingDown, which is what permanently disables
// the crash supervisor — model that exactly.
function shutdown() { calls.push(["shutdown"]); shuttingDown = true; }
function sweepOrphanDaemons() { calls.push(["sweepOrphanDaemons"]); }
function refreshTrayMenu() { calls.push(["refreshTrayMenu"]); }
function friendlyUpdateError(m) { return m; }
function _emitUpdateState(p) { calls.push(["updateState", p.status]); }
console.error = () => {};

__LIFTED__

// electron-updater's BaseUpdater.install() returns false SYNCHRONOUSLY when the
// cached download is gone and routes the failure through dispatchError -> the
// 'error' event. quitAndInstall itself returns void and never throws.
class FailingUpdater extends EventEmitter {
  quitAndInstall() {
    calls.push(["quitAndInstall"]);
    this.emit("error", new Error("No update filepath provided, can't quit and install"));
  }
}
// The success path only QUEUES app.quit() on setImmediate.
class OkUpdater extends EventEmitter {
  quitAndInstall() {
    calls.push(["quitAndInstall"]);
    setImmediate(() => calls.push(["app.quit(queued)"]));
  }
}

let _autoUpdater = process.argv[2] === "fail" ? new FailingUpdater() : new OkUpdater();
applyPendingUpdate();
if (process.argv[2] === "reentry") applyPendingUpdate();
console.log(JSON.stringify({
  calls,
  shuttingDown,
  isQuitting,
  updateInstallInFlight,
  pendingUpdateInfo,
  errorListeners: _autoUpdater.listenerCount("error"),
}));
"""


def _update_harness() -> str:
    lifted = _lift("function applyPendingUpdate()", "function abortUpdateInstall")
    assert "quitAndInstall" in lifted and "abortUpdateInstall" in lifted
    return _UPDATE_HARNESS.replace("__LIFTED__", lifted)


def _run_update(mode: str, tmp_path: Path) -> dict:
    f = tmp_path / "update-harness.js"
    f.write_text(_update_harness(), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), mode], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_failed_install_never_tears_the_children_down(tmp_path):
    """THE ZOMBIE. install() fails synchronously -> nothing may be killed.

    The old code ran ``shutdown()`` (and the by-image-name orphan sweep, which
    kills OUR daemon too) before ever calling quitAndInstall, so this exact
    failure left dead children behind a latched ``shuttingDown``.
    """
    out = _run_update("fail", tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "quitAndInstall" in names, "the handoff was never attempted"
    assert "shutdown" not in names, "children were torn down for an install that never started"
    assert "sweepOrphanDaemons" not in names, "the orphan sweep kills our own daemon by image name"
    # The supervisor must still be armed and the app must still be resident.
    assert out["shuttingDown"] is False, "crash supervisor left permanently disabled"
    assert out["isQuitting"] is False, "left mid-quit: a window close would tear down"
    assert "app.quit" not in names, "quit with live children"
    assert out["updateInstallInFlight"] is False, "a retry would be locked out forever"
    assert out["errorListeners"] == 0, "the one-shot error listener leaked"


@requires_node
def test_failed_install_is_honest_everywhere_it_lied(tmp_path):
    """A dialog, an error state, no stale "update ready", no stale marker."""
    out = _run_update("fail", tmp_path)
    names = [c[0] for c in out["calls"]]
    assert "dialog" in names, "the user was told nothing"
    assert ["updateState", "error"] in out["calls"], "the Updates page still showed 'downloaded'"
    assert "clearUpdatePending" in names, (
        "the recovery marker survived an install that never happened — it would "
        "misreport the NEXT boot as a failed update"
    )
    assert out["pendingUpdateInfo"] is None, "the update is not ready; nothing may claim it is"
    assert "refreshTrayMenu" in names, "the tray still offered 'Restart to update'"
    tooltips = [c[1] for c in out["calls"] if c[0] == "tooltip"]
    assert tooltips, "the tray tooltip still claimed an update was ready"
    assert not any("update" in t.lower() for t in tooltips), tooltips


@requires_node
def test_successful_handoff_still_kills_the_children_before_nsis(tmp_path):
    """The other invariant: NSIS must not meet a locked frozen exe.

    Order is load-bearing — the marker first, then the handoff, and only then
    the synchronous teardown + sweep (which still run ahead of the setImmediate
    ``app.quit()`` electron-updater queued).
    """
    out = _run_update("ok", tmp_path)
    names = [c[0] for c in out["calls"]]
    for step in ("markUpdatePending", "quitAndInstall", "shutdown", "sweepOrphanDaemons"):
        assert step in names, f"{step} missing from the install path"
    assert names.index("markUpdatePending") < names.index("quitAndInstall")
    assert names.index("quitAndInstall") < names.index("shutdown")
    assert names.index("shutdown") < names.index("sweepOrphanDaemons")
    assert ["markUpdatePending", "1.192.0"] in out["calls"]
    assert out["shuttingDown"] is True
    assert "dialog" not in names, "a working install must not nag"


@requires_node
def test_a_second_click_cannot_re_enter_mid_teardown(tmp_path):
    """Tray item + notification + Updates page all call this; one wins."""
    out = _run_update("reentry", tmp_path)
    names = [c[0] for c in out["calls"]]
    assert names.count("quitAndInstall") == 1
    assert names.count("shutdown") == 1


# --------------------------------------------------------------------------
# 23 — second launch during a hidden boot, executed for real
# --------------------------------------------------------------------------

_SECOND_INSTANCE_HARNESS = """
const calls = [];
let mainWin = null;
let loadingWin = null;
let bootComplete = false;
let showWindowWhenReady = false;
function showMainWindow() { calls.push("showMainWindow"); }
let handler = null;
const app = { on: (ev, fn) => { if (ev === "second-instance") handler = fn; } };

__LIFTED__

const state = JSON.parse(process.argv[2]);
const live = { isDestroyed: () => false, isMinimized: () => false, restore: () => {}, focus: () => calls.push("focus") };
if (state.mainWin) mainWin = live;
if (state.loadingWin) loadingWin = live;
bootComplete = !!state.bootComplete;
handler();
console.log(JSON.stringify({ calls, showWindowWhenReady }));
"""


def _second_instance_harness() -> str:
    src = _src()
    start = src.index('app.on("second-instance"')
    end = src.index("\n  });\n", start) + len("\n  });\n")
    lifted = src[start:end]
    assert "showWindowWhenReady" in lifted
    return _SECOND_INSTANCE_HARNESS.replace("__LIFTED__", lifted)


def _run_second_instance(state: dict, tmp_path: Path) -> dict:
    f = tmp_path / "second-instance.js"
    f.write_text(_second_instance_harness(), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), json.dumps(state)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_second_launch_during_a_hidden_boot_is_recorded(tmp_path):
    """No window, no splash, not booted: the launch used to vanish entirely."""
    out = _run_second_instance(
        {"mainWin": False, "loadingWin": False, "bootComplete": False}, tmp_path
    )
    assert out["showWindowWhenReady"] is True, (
        "a second launch during a --hidden boot is still dropped silently"
    )


@requires_node
@pytest.mark.parametrize(
    "state, expect_show",
    [
        ({"mainWin": True, "loadingWin": False, "bootComplete": True}, True),
        ({"mainWin": False, "loadingWin": True, "bootComplete": False}, False),
        ({"mainWin": False, "loadingWin": False, "bootComplete": True}, True),
    ],
)
def test_the_other_second_instance_branches_are_unchanged(state, expect_show, tmp_path):
    """The new branch is a FALL-THROUGH addition, not a rewrite."""
    out = _run_second_instance(state, tmp_path)
    assert ("showMainWindow" in out["calls"]) is expect_show
    # A live splash is focused, and nothing needs deferring — startup will open
    # the real window itself in that (non-hidden) boot.
    if state["loadingWin"]:
        assert "focus" in out["calls"]
    assert out["showWindowWhenReady"] is False


def test_startup_honours_the_show_when_ready_intent():
    """The recording half is worthless without the honouring half.

    ``startup()`` cannot be executed here (it spawns children and health-gates
    them), so pin the seam: the START_HIDDEN "stay in the tray" branch must be
    conditional on the recorded intent.
    """
    src = _src()
    m = re.search(
        r"bootComplete = true;(.{0,900}?)\n  checkForUpdates\(\);", src, re.S
    )
    assert m, "startup()'s post-gate block moved"
    body = m.group(1)
    assert "if (START_HIDDEN && !showWindowWhenReady) {" in body, (
        "the tray-only login branch ignores a second launch that arrived mid-boot"
    )
    assert "createMainWindow();" in body
    assert "showWindowWhenReady = false;" in body, "the intent is never cleared"


# --------------------------------------------------------------------------
# 22 — the quit-install path gets the same recovery marker
# --------------------------------------------------------------------------


def test_quit_installed_update_writes_the_recovery_marker():
    """``autoInstallOnAppQuit`` is an install; it needs the marker too.

    The before-quit teardown lives in a closure that cannot be lifted, so pin
    the two preparations together: the marker AND the sweep, both gated on
    ``pendingUpdateInfo``, exactly as ``applyPendingUpdate`` does them.
    """
    src = _src()
    m = re.search(
        r"requestDaemonShutdown\(2000\)\.finally\(\(\) => \{(.{0,900}?)\n    \}\);",
        src,
        re.S,
    )
    assert m, "the before-quit teardown moved"
    body = m.group(1)
    assert "sweepOrphanDaemons();" in body
    assert "markUpdatePending(pendingUpdateInfo.version);" in body, (
        "a quit-installed update still leaves no recovery marker, so a bad "
        "update can never reach handleStartupFailure's recovery dialog"
    )
    # Both must be gated on there actually being an update to install.
    assert re.search(
        r"if \(pendingUpdateInfo\) \{\s*markUpdatePending\(pendingUpdateInfo\.version\);\s*"
        r"sweepOrphanDaemons\(\);\s*\}",
        body,
    ), "marker/sweep are no longer gated together on pendingUpdateInfo"


def test_the_marker_is_what_the_recovery_dialog_reads():
    """Guard the far end of the contract: marker -> bump -> dialog."""
    src = _src()
    assert "const pendingUpdate = readAndBumpUpdatePending();" in src
    m = re.search(
        r"function handleStartupFailure\(title, message, pendingUpdate\) \{(.{0,900}?)\n  \} else",
        src,
        re.S,
    )
    assert m, "handleStartupFailure changed shape"
    assert "pendingUpdate.attempts >= 2" in m.group(1)


# --------------------------------------------------------------------------
# 42 — the toast click must rebuild a destroyed window
# --------------------------------------------------------------------------


def test_notify_toast_click_rebuilds_a_destroyed_window():
    """hideToTray() DESTROYS the window; a guarded show() is a no-op there."""
    src = _src()
    m = re.search(
        r'ipcMain\.handle\("notify:show", \(_e, opts\) => \{(.{0,700}?)\n  \}\);',
        src,
        re.S,
    )
    assert m, "no notify:show handler"
    body = m.group(1)
    click = body[body.index('note.on("click"') :]
    assert "showMainWindow()" in click, (
        "the toast click still cannot reopen the app after a close-to-tray"
    )
    assert "mainWin.show()" not in click, "still the guarded no-op show"


def test_hide_to_tray_really_destroys_the_window():
    """The premise of the fix above — pin it so a future 'optimisation' that
    switches back to hide() does not silently make the fix look pointless."""
    src = _src()
    m = re.search(r"function hideToTray\(\) \{(.{0,600}?)\n\}", src, re.S)
    assert m, "hideToTray moved"
    assert "win.destroy()" in m.group(1)
    assert "mainWin = null" in src
