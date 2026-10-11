"""v1.333.0 (lane F) — the desktop shell stops doing work nobody sees, and the
quick task (Spotlight) stops saying "done" about a task that is not.

* F4: the quick-task watcher treated only ``completed``/``failed`` as an end:
  a CANCELLED task was polled for 15 minutes and then announced "Task done",
  and at the 15-minute cap a task that was still RUNNING was announced "Task
  done" too. ``cancelled`` is now an end ("Task cancelled"), the cap says the
  task is still running, the read is the slim one (``?transcript=0``) and a
  tick never starts while the previous read is on its way.
* F10: the Spotlight BrowserWindow (a whole renderer process) lived for the
  life of the app once opened; it is destroyed after
  ``SPOTLIGHT_IDLE_DESTROY_MS`` hidden. A pop-out window whose renderer died
  sat on a blank page; it now reloads in place through the SAME breaker the
  main window's recovery uses.

House idiom (``test_desktop_lifecycle_v1192.py``, ``test_desktop_popout_v1283.py``):
the shipped functions are LIFTED out of ``desktop/main.js`` and run under node
against stubs — never copied, so a change to main.js runs here changed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parents[1] / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src() -> str:
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str) -> str:
    """From ``start_marker`` to the column-0 ``}`` that closes it."""
    src = _src()
    start = src.index(start_marker)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _run(script: str, tmp_path: Path, name: str):
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# F4 — the quick-task watcher
# --------------------------------------------------------------------------

_WATCH_HARNESS = """
const notes = [];
const requests = [];
let cleared = 0;
let tick = null;
function setInterval(fn, ms) { tick = fn; return 1; }
function clearInterval() { cleared += 1; tick = null; } // a cleared interval never fires again
class Notification {
  constructor(o) { this.o = o; }
  on() {}
  show() { notes.push({ title: this.o.title, body: this.o.body }); }
}
const DASHBOARD_URL = "http://127.0.0.1:8788";
let mainWin = null;
function showMainWindow() {}
const MODE = process.argv[2];
let statuses = (process.argv[3] || "").split(",");
function daemonRequest(method, p) {
  requests.push(p);
  if (MODE === "hang") return new Promise(() => {});
  const status = statuses.length > 1 ? statuses.shift() : statuses[0];
  return Promise.resolve({ session: { status, summary: status === "completed" ? "All set." : "" }, transcript: null });
}
__LIFTED__
const flush = () => new Promise((r) => setImmediate(r));
(async () => {
  watchSpotlightTask("s1", "rename the photos");
  const ticks = Number(process.argv[4] || "1");
  for (let i = 0; i < ticks && tick; i += 1) { tick(); await flush(); await flush(); }
  console.log(JSON.stringify({ notes, requests: requests.length, paths: [...new Set(requests)], cleared }));
})();
"""


def _watch(tmp_path: Path, mode: str, statuses: str, ticks: int) -> dict:
    lifted = "\n".join(
        [
            _decl("SPOTLIGHT_POLL_MS"),
            _decl("SPOTLIGHT_WATCH_CAP_MS"),
            _lift("function spotlightOutcome("),
            _lift("function watchSpotlightTask("),
        ]
    )
    script = _WATCH_HARNESS.replace("__LIFTED__", lifted)
    f = tmp_path / "watch.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), mode, statuses, str(ticks)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_a_cancelled_quick_task_ends_the_watch_and_says_cancelled(tmp_path):
    out = _watch(tmp_path, "answer", "active,cancelled", 10)
    assert out["notes"] == [{"title": "Task cancelled", "body": "Click to open the result."}]
    assert out["cleared"] == 1
    assert out["requests"] == 2, "the watch kept polling a cancelled task"


@requires_node
def test_at_the_fifteen_minute_cap_a_running_task_is_called_still_running_not_done(tmp_path):
    # 226 ticks of 4 s = 904 s, just past the 900 s cap.
    out = _watch(tmp_path, "answer", "active", 226)
    assert len(out["notes"]) == 1, out["notes"]
    note = out["notes"][0]
    assert note["title"] == "Still running: rename the photos"
    assert "15 minutes" in note["body"]
    assert "done" not in note["title"].lower()
    assert out["cleared"] == 1


@requires_node
def test_a_finished_quick_task_still_says_done_with_its_summary(tmp_path):
    out = _watch(tmp_path, "answer", "active,completed", 5)
    assert out["notes"] == [{"title": "Task done: rename the photos", "body": "All set."}]


@requires_node
def test_the_watch_reads_the_slim_session_and_never_stacks_reads(tmp_path):
    out = _watch(tmp_path, "hang", "active", 30)
    assert out["paths"] == ["/sessions/s1?transcript=0"]
    assert out["requests"] == 1, "a tick started while the previous read was still on its way"


# --------------------------------------------------------------------------
# F10 — a hidden Spotlight is destroyed; a crashed pop-out reloads
# --------------------------------------------------------------------------

_DESTROY_HARNESS = """
let timers = [];
function setTimeout(fn, ms) { const t = { fn, ms, live: true, unref() {} }; timers.push(t); return t; }
function clearTimeout(t) { if (t) t.live = false; }
__LIFTED__
function win(visible) {
  return { destroyed: 0, visible, isDestroyed() { return this.destroyed > 0; }, isVisible() { return this.visible; }, destroy() { this.destroyed += 1; } };
}
const fire = () => { for (const t of timers.splice(0)) if (t.live) t.fn(); };
const out = {};
// Hidden for the whole idle span -> destroyed once.
let w = win(false);
scheduleSpotlightDestroy(w);
out.delay = timers[0].ms;
fire();
out.hiddenDestroyed = w.destroyed;
// Shown again before the span ends -> cancelled, kept.
w = win(false);
scheduleSpotlightDestroy(w);
cancelSpotlightDestroy();
fire();
out.reshownDestroyed = w.destroyed;
// Visible when the timer fires (a race) -> kept.
w = win(true);
scheduleSpotlightDestroy(w);
fire();
out.visibleDestroyed = w.destroyed;
// Hidden twice -> ONE timer, one destroy.
w = win(false);
scheduleSpotlightDestroy(w);
scheduleSpotlightDestroy(w);
out.liveTimers = timers.filter((t) => t.live).length;
fire();
out.twiceDestroyed = w.destroyed;
console.log(JSON.stringify(out));
"""


@requires_node
def test_a_spotlight_hidden_for_the_idle_span_is_destroyed_and_a_reshown_one_is_kept(tmp_path):
    lifted = "\n".join(
        [
            _decl("SPOTLIGHT_IDLE_DESTROY_MS"),
            _decl("_spotlightDestroyTimer"),
            _lift("function cancelSpotlightDestroy("),
            _lift("function scheduleSpotlightDestroy("),
        ]
    )
    out = _run(_DESTROY_HARNESS.replace("__LIFTED__", lifted), tmp_path, "destroy")
    assert out["delay"] == 10 * 60 * 1000
    assert out["hiddenDestroyed"] == 1
    assert out["reshownDestroyed"] == 0
    assert out["visibleDestroyed"] == 0
    assert out["liveTimers"] == 1
    assert out["twiceDestroyed"] == 1


def test_the_spotlight_window_arms_the_destroy_on_hide_and_disarms_it_on_show():
    body = _lift("function createSpotlightWindow(")
    assert 'win.on("hide", () => scheduleSpotlightDestroy(win));' in body
    assert 'win.on("show", () => cancelSpotlightDestroy());' in body
    # toggleSpotlight still rebuilds a destroyed window (the first-open path).
    assert "if (spotlightWin && !spotlightWin.isDestroyed()) return spotlightWin;" in body


_POPOUT_CRASH_HARNESS = """
const log = [];
let shuttingDown = false;
let isQuitting = false;
let exhausted = 0;
let notified = 0;
function rendererLog(l) { log.push(l); }
function reportIncident(kind, detail) { log.push(kind + ":" + detail); }
function recoveryExhausted() { exhausted += 1; }
function notifyRecovered() { notified += 1; }
function setTimeout(fn) { fn(); return 0; }
__LIFTED__
function popout() {
  const handlers = {};
  return {
    reloads: 0,
    destroyed: false,
    isDestroyed() { return this.destroyed; },
    webContents: {
      on(name, fn) { handlers[name] = fn; },
      reload() { w.reloads += 1; },
    },
    gone(reason) { handlers["render-process-gone"]({}, { reason, exitCode: 1 }); },
  };
}
const out = {};
let w = popout();
installPopoutCrashReload(w, "/chat");
w.gone("crashed");
out.crashReloads = w.reloads;
w = popout();
installPopoutCrashReload(w, "/chat");
w.gone("clean-exit");
out.cleanReloads = w.reloads;
// A crash loop: the shared breaker allows RECOVERY_MAX_IN_WINDOW in the window.
_wdRecoveries = [];
w = popout();
installPopoutCrashReload(w, "/terminals");
for (let i = 0; i < 5; i += 1) w.gone("oom");
out.loopReloads = w.reloads;
out.exhausted = exhausted;
out.notified = notified;
console.log(JSON.stringify(out));
"""


@requires_node
def test_a_crashed_popout_reloads_in_place_through_the_shared_breaker(tmp_path):
    lifted = "\n".join(
        [
            _decl("RECOVERY_WINDOW_MS"),
            _decl("RECOVERY_MAX_IN_WINDOW"),
            _decl("_wdRecoveries"),
            _lift("function recoveryAllowed("),
            _lift("function installPopoutCrashReload("),
        ]
    )
    out = _run(_POPOUT_CRASH_HARNESS.replace("__LIFTED__", lifted), tmp_path, "popout_crash")
    assert out["crashReloads"] == 1
    assert out["cleanReloads"] == 0
    # 1 earlier crash was reset; the loop gets RECOVERY_MAX_IN_WINDOW (3) reloads.
    assert out["loopReloads"] == 3
    assert out["exhausted"] == 2
    assert out["notified"] == 1 + 3


def test_every_popout_installs_the_crash_reload():
    body = _lift("function openPopout(")
    assert "installPopoutCrashReload(win, route);" in body
