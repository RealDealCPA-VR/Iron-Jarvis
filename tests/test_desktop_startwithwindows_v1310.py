"""v1.310.0 (wave 2, the first five minutes) — Start-with-Windows waits.

Finding ``start-with-windows-on-first-launch``: on the very first packaged
boot a native "Start Iron Jarvis with Windows?" dialog popped up in the same
tick the main window opened — over the first-run wizard, asking a
system-level question about "schedules, messages and jobs" the user had not
heard of yet. And the asked-flag was written BEFORE the dialog, so a
reflexive close (or a crash) meant it never came back.

The fix pinned here:

* the FIRST launch stamps ``firstLaunchAt`` (ms since the epoch, a number)
  in desktop-settings.json and asks nothing;
* a launch less than a day later asks nothing (and keeps the first stamp);
* a launch a day or more later asks — parented to the main window;
* ``startWithWindowsAsked`` is written only once the dialog is ANSWERED, so
  an unanswered dialog asks again next launch.

House idiom (``test_desktop_reliability_v1249.py``): the shipped source is
LIFTED out of main.js verbatim and run under node against stubs. Lifted
regions (any new helper or constant the fix needs MUST live inside one of
them, or the harness cannot see it):

* the desktop-settings block, ``function desktopSettingsFile`` through the
  end of ``function writeDesktopSetting``;
* the start-at-login block, from the ``// --- Start at login`` banner through
  the end of ``function maybeOfferStartWithWindows``.

The real ``app`` / ``dialog`` are stubs; the settings file is a real file in
``tmp_path``. Time is NOT faked: seeds are computed from the real clock, so
the fix may read the time however it likes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000


def _src() -> str:
    # CRLF on the CI runner, LF here: normalise at the READER.
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str, end_after: str) -> str:
    """main.js from ``start_marker`` to the column-0 ``}`` that closes the
    function beginning at ``end_after``. Lifted, never copied."""
    src = _src()
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


_HARNESS = r"""
const fs = require("fs"), path = require("path");
Object.defineProperty(process, "platform", { value: "win32" });
const SETTINGS = process.argv[2];
const scenario = JSON.parse(process.argv[3]);
const IS_PACKAGED = true;
const START_HIDDEN = false;
const userDataDir = path.dirname(SETTINGS);
let keepRunningPref = null;
let mainShown = !scenario.hidden;
const showHandlers = [];
const mainWin = {
  __isMainWindow: true,
  isDestroyed: () => false,
  isVisible: () => mainShown,
  once: (ev, fn) => { if (ev === "show") showHandlers.push(fn); },
};
const dialogs = [];
const loginSet = [];
let loginOn = !!scenario.loginOn;
const app = {
  isPackaged: true,
  getLoginItemSettings: () => ({ openAtLogin: loginOn }),
  setLoginItemSettings: (o) => { loginSet.push(o); loginOn = !!o.openAtLogin; },
};
function refreshMenus() {}
function refreshTrayMenu() {}
function desktopLog() {}
let answer = null;
const dialog = {
  showMessageBox: (...args) => {
    dialogs.push({
      parented: args.length > 1 && args[0] === mainWin,
      message: String((args[args.length - 1] || {}).message || ""),
    });
    return new Promise((resolve) => { answer = resolve; });
  },
  showMessageBoxSync: (...args) => {
    dialogs.push({ parented: args.length > 1 && args[0] === mainWin, sync: true });
    return scenario.respond === undefined ? 1 : scenario.respond;
  },
};
__SETTINGS__
__LOGIN__
const read = () => { try { return JSON.parse(fs.readFileSync(SETTINGS, "utf8")); } catch { return null; } };
const tick = () => new Promise((r) => setTimeout(r, 30));
(async () => {
  maybeOfferStartWithWindows();
  await tick();
  const dialogsBeforeShow = dialogs.length;
  if (!mainShown) { mainShown = true; showHandlers.splice(0).forEach((fn) => fn()); await tick(); }
  const beforeAnswer = read();
  if (answer && scenario.respond !== undefined) answer({ response: scenario.respond });
  await tick();
  process.stdout.write(JSON.stringify({
    dialogs, dialogsBeforeShow, beforeAnswer, after: read(), loginSet,
  }) + "\n");
})();
"""


def _harness() -> str:
    settings = _lift("function desktopSettingsFile", "function writeDesktopSetting")
    login = _lift("// --- Start at login", "function maybeOfferStartWithWindows")
    assert "function writeDesktopSetting" in settings
    assert "function maybeOfferStartWithWindows" in login
    return _HARNESS.replace("__SETTINGS__", settings).replace("__LOGIN__", login)


def _launch(tmp_path: Path, name: str, *, seed: dict | None = None,
            respond: int | None = None, login_on: bool = False,
            settings: Path | None = None, hidden: bool = False) -> dict:
    """One packaged boot's ``maybeOfferStartWithWindows()`` against a real
    settings file. ``seed`` (written first, when given) is the file's content;
    ``respond`` answers the dialog (0 = Yes, 1 = Not now; None = left open)."""
    settings = settings or (tmp_path / "userData" / "desktop-settings.json")
    settings.parent.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        settings.write_text(json.dumps(seed), encoding="utf-8")
    script = tmp_path / f"{name}.js"
    script.write_text(_harness(), encoding="utf-8")
    scenario = {"loginOn": login_on, "hidden": hidden}
    if respond is not None:
        scenario["respond"] = respond
    proc = subprocess.run(
        ["node", str(script), str(settings), json.dumps(scenario)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _now_ms() -> int:
    return int(time.time() * 1000)


@requires_node
def test_the_first_launch_asks_nothing_and_remembers_when_it_was(tmp_path):
    """The first-run wizard owns the first click: no native dialog over it.
    The launch is stamped (ms since the epoch) so a later one can ask."""
    t0 = _now_ms()
    out = _launch(tmp_path, "first")
    assert out["dialogs"] == [], "the very first launch must not offer Start-with-Windows"
    after = out["after"] or {}
    stamp = after.get("firstLaunchAt")
    assert isinstance(stamp, (int, float)) and not isinstance(stamp, bool), (
        f"firstLaunchAt must be a number of ms since the epoch: {after}"
    )
    assert abs(stamp - t0) < 60_000, (stamp, t0)
    assert after.get("startWithWindowsAsked") is not True, (
        "nothing was asked, so nothing may be marked asked"
    )


@requires_node
def test_a_launch_the_same_day_still_asks_nothing_and_keeps_the_first_stamp(tmp_path):
    seeded = _now_ms() - 23 * HOUR_MS
    out = _launch(tmp_path, "same-day", seed={"firstLaunchAt": seeded})
    assert out["dialogs"] == [], "less than a day after the first launch: too soon to ask"
    assert (out["after"] or {}).get("firstLaunchAt") == seeded, (
        "a later launch must never move the first-launch stamp forward"
    )


@requires_node
def test_a_launch_a_day_later_asks_once_parented_to_the_main_window(tmp_path):
    """A day on, the user has something worth keeping alive: ask — as a
    sheet of the main window, never a free-floating box over the splash or
    the wizard."""
    out = _launch(tmp_path, "day-later", seed={"firstLaunchAt": _now_ms() - 25 * HOUR_MS},
                  respond=1)
    assert len(out["dialogs"]) == 1, out["dialogs"]
    assert out["dialogs"][0]["parented"] is True, (
        "the dialog must be parented to mainWin (showMessageBox(mainWin, {...}))"
    )


@requires_node
def test_asked_is_written_only_once_the_dialog_is_answered(tmp_path):
    """Before the answer the flag is NOT on disk; a 'Not now' answer writes
    it (and turns nothing on)."""
    out = _launch(tmp_path, "answered-no", seed={"firstLaunchAt": _now_ms() - 2 * DAY_MS},
                  respond=1)
    assert len(out["dialogs"]) == 1, out["dialogs"]
    assert (out["beforeAnswer"] or {}).get("startWithWindowsAsked") is not True, (
        "startWithWindowsAsked was written before the user answered"
    )
    assert (out["after"] or {}).get("startWithWindowsAsked") is True, (
        "an answered dialog must be remembered"
    )
    assert out["loginSet"] == [], "'Not now' must not turn start-at-login on"


@requires_node
def test_an_unanswered_dialog_asks_again_next_launch(tmp_path):
    """The app was closed (or crashed) with the question open: nothing was
    answered, so the next launch asks again."""
    settings = tmp_path / "userData" / "desktop-settings.json"
    first = _launch(tmp_path, "left-open", seed={"firstLaunchAt": _now_ms() - 2 * DAY_MS},
                    settings=settings)
    assert len(first["dialogs"]) == 1, first["dialogs"]
    again = _launch(tmp_path, "left-open-2", settings=settings)
    assert len(again["dialogs"]) == 1, (
        "a dialog nobody answered was counted as asked - it never comes back"
    )


@requires_node
def test_control_yes_turns_it_on_and_is_remembered(tmp_path):
    """Control (passes before and after the fix): Yes sets the login item and
    marks it asked."""
    out = _launch(tmp_path, "answered-yes", seed={"firstLaunchAt": _now_ms() - 2 * DAY_MS},
                  respond=0)
    assert len(out["dialogs"]) == 1
    assert out["loginSet"] and out["loginSet"][0].get("openAtLogin") is True, out["loginSet"]
    assert (out["after"] or {}).get("startWithWindowsAsked") is True


@requires_node
def test_control_already_answered_or_already_on_never_asks(tmp_path):
    """Anti-vacuity: an old install that already answered, or already starts
    with Windows, is never asked — whatever the first-launch stamp says."""
    old = _now_ms() - 30 * DAY_MS
    answered = _launch(tmp_path, "already-answered",
                       seed={"firstLaunchAt": old, "startWithWindowsAsked": True})
    assert answered["dialogs"] == []
    on = _launch(tmp_path / "on", "already-on", seed={"firstLaunchAt": old}, login_on=True)
    assert on["dialogs"] == []


@requires_node
def test_a_window_still_hidden_at_boot_is_asked_over_once_it_shows(tmp_path):
    """Boot calls the offer in the same tick as createMainWindow(), and the
    window is built hidden (shown on 'ready-to-show'). The question waits for
    the window to appear rather than landing over the splash, owned by a
    window nobody can see yet - and is then parented to it as usual."""
    out = _launch(tmp_path, "hidden", seed={"firstLaunchAt": _now_ms() - 25 * HOUR_MS},
                  respond=1, hidden=True)
    assert out["dialogsBeforeShow"] == 0, "asked before the main window was visible"
    assert len(out["dialogs"]) == 1 and out["dialogs"][0]["parented"] is True, out["dialogs"]
    assert (out["after"] or {}).get("startWithWindowsAsked") is True, out["after"]


def test_a_tray_toggle_is_an_answer_so_the_question_never_follows_it():
    """Review (v1.310.0): with the asked-flag no longer written at first
    launch, a user who switched Start with Windows on and off from the tray
    on day 0 was asked the same question a day later. Both menu toggles now
    go through ONE helper that records the answer."""
    src = _src()
    assert src.count("click: (item) => setStartAtLoginFromMenu(item.checked)") == 2
    assert "click: (item) => setStartAtLogin(item.checked)" not in src
    helper = src[src.index("function setStartAtLoginFromMenu"):]
    helper = helper[: helper.index("\n}") + 2]
    assert 'writeDesktopSetting("startWithWindowsAsked", true)' in helper
    assert "setStartAtLogin(enabled)" in helper
