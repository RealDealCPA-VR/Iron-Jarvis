"""v1.438.0 (chat-first plan Wave 4 group C, decision D5): "Start Iron Jarvis
with Windows" and "Keep running in the background when I close the window"
are settings — the desktop app reads them and applies them.

THE RULE, pinned here end to end: the tray and the setting are the SAME
switch, and the newer choice wins.

* the shell READS ``start_with_windows`` / ``keep_running_in_background``
  from config.toml (the state home's, then ``~/.ironjarvis/config.toml``) and
  APPLIES a value only when it differs from the value it last saw — so a tray
  press made since is never undone by an older setting;
* a tray press WRITES the new value back through ``PUT /settings/values``
  (actor "desktop"); until the daemon has it, it waits and is sent again;
* "not set" changes nothing (the tray and the close question decide).

House idiom (``test_desktop_startwithwindows_v1310.py``): the shipped source
is LIFTED out of main.js verbatim and run under node against stubs; the
settings file and config.toml are real files in ``tmp_path``. The daemon half
(schema rows, tiers, the writer, the PUT's actor) runs the real app.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config, persist_config_values
from iron_jarvis.settings import schema
from iron_jarvis.settings.trigger import settings_ask

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
KEYS = ("start_with_windows", "keep_running_in_background")


def _src() -> str:
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
const scenario = JSON.parse(process.argv[2]);
Object.defineProperty(process, "platform", { value: scenario.platform || "win32" });
const IS_PACKAGED = scenario.packaged !== false;
const START_HIDDEN = false;
const userDataDir = scenario.userData;
const DAEMON_PORT = 8787;
const authToken = "tok";
let keepRunningPref = scenario.keepRunning === undefined ? null : scenario.keepRunning;
const loginSet = [];
let loginOn = !!scenario.loginOn;
const app = {
  isPackaged: IS_PACKAGED,
  getLoginItemSettings: () => ({ openAtLogin: loginOn }),
  setLoginItemSettings: (o) => { loginSet.push(o); loginOn = !!o.openAtLogin; },
};
const puts = [];
let daemonUp = !!scenario.daemonUp;
const http = {
  request: (opts, cb) => {
    const req = {
      on: (ev, fn) => { if (ev === "error") req._err = fn; return req; },
      setTimeout: () => req,
      destroy: () => {},
      end: (data) => {
        puts.push({ path: opts.path, method: opts.method, auth: opts.headers.Authorization, body: JSON.parse(data) });
        setTimeout(() => {
          if (!daemonUp) return req._err && req._err(new Error("ECONNREFUSED"));
          if (scenario.writeConfigOnPut) {
            const body = JSON.parse(data);
            const lines = Object.entries(body.values).map(([k, v]) => `${k} = ${v}`);
            fs.writeFileSync(path.join(scenario.home, "config.toml"), lines.join("\n") + "\n");
          }
          cb({ statusCode: 200, resume: () => {} });
        }, 1);
      },
    };
    return req;
  },
};
function refreshMenus() {}
function refreshTrayMenu() {}
function desktopLog() {}
function globalConfigFile() { return scenario.globalFile || ""; }
function updateChannelHome() { return scenario.home; }
const dialog = {};
let mainWin = null;
__SETTINGS__
__LOGIN__
__SWITCHES__
const read = () => { try { return JSON.parse(fs.readFileSync(path.join(userDataDir, "desktop-settings.json"), "utf8")); } catch { return null; } };
const tick = () => new Promise((r) => setTimeout(r, 30));
const writeConfig = (text) => fs.writeFileSync(path.join(scenario.home, "config.toml"), text);
(async () => {
  const out = { steps: [] };
  for (const step of scenario.steps || []) {
    if (step.config !== undefined) writeConfig(step.config);
    if (step.daemonUp !== undefined) daemonUp = step.daemonUp;
    if (step.sync) syncDesktopSwitches(step.sync === "nopush" ? { push: false } : {});
    if (step.tray === "login") setStartAtLoginFromMenu(step.value);
    if (step.tray === "keep") setKeepRunningPref(step.value);
    await tick();
    out.steps.push({ loginOn, keepRunningPref, settings: read(), puts: puts.length });
  }
  if (scenario.pure) {
    out.pure = {
      setting: scenario.pure.settings.map(([t, k]) => desktopSwitchSetting(t, k)),
      decision: scenario.pure.decisions.map(([s, seen, pending]) =>
        desktopSwitchDecision(s, seen === "undef" ? undefined : seen, pending === "undef" ? undefined : pending)),
      layered: readDesktopAppSettings(scenario.home, scenario.globalFile),
    };
  }
  out.loginSet = loginSet;
  out.puts = puts;
  process.stdout.write(JSON.stringify(out) + "\n");
})();
"""


def _harness() -> str:
    settings = _lift("function desktopSettingsFile", "function writeDesktopSetting")
    keep = _lift("function setKeepRunningPref", "function setKeepRunningPref")
    login = _lift("// --- Start at login", "function setStartAtLoginFromMenu")
    switches = _lift("// --- The desktop app's own switches", "function installDesktopSwitches")
    return (
        _HARNESS.replace("__SETTINGS__", settings + "\n" + keep)
        .replace("__LOGIN__", login)
        .replace("__SWITCHES__", switches)
    )


def _run(tmp_path: Path, scenario: dict) -> dict:
    home = tmp_path / "home"
    user = tmp_path / "userData"
    home.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    scenario = {"home": str(home), "userData": str(user), **scenario}
    script = tmp_path / "harness.js"
    script.write_text(_harness(), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script), json.dumps(scenario)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# the shell (node)
# --------------------------------------------------------------------------- #


@requires_node
def test_the_reader_and_the_decision_are_pure_and_say_what_the_daemon_says(tmp_path):
    glob = tmp_path / "global.toml"
    glob.write_text('keep_running_in_background = true\nstart_with_windows = true\n', encoding="utf-8")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / "config.toml").write_text(
        'start_with_windows = false\n[comm]\nkeep_running_in_background = false\n', encoding="utf-8"
    )
    out = _run(tmp_path, {
        "globalFile": str(glob),
        "pure": {
            "settings": [
                ["start_with_windows = true\n", "start_with_windows"],
                ['"start_with_windows" = false # off\n', "start_with_windows"],
                ["﻿start_with_windows = true", "start_with_windows"],
                ['start_with_windows = "yes"\n', "start_with_windows"],
                ["[x]\nstart_with_windows = true\n", "start_with_windows"],
                ["", "start_with_windows"],
                ["start_with_windows_x = true\n", "start_with_windows"],
            ],
            "decisions": [
                [True, "undef", "undef"],    # set, never seen: apply
                [True, True, "undef"],       # seen: keep (a tray press since stays)
                [None, "undef", "undef"],    # never set, never seen: adopt the tray's
                [None, True, "undef"],       # set back to not set: keep
                [False, True, False],        # the daemon holds the press: confirm
                [True, True, False],         # the press has not reached it: push
                [False, True, True],         # changed elsewhere since: apply (newer)
            ],
        },
    })
    assert out["pure"]["setting"] == [True, False, True, None, None, None, None]
    assert out["pure"]["decision"] == ["apply", "keep", "adopt", "keep", "confirm", "push", "apply"]
    # Per key, the home file wins; a key only the global file sets comes from
    # it (the daemon's own layering); a key under a [table] is not top-level.
    assert out["pure"]["layered"] == {"start_with_windows": False, "keep_running_in_background": True}


@requires_node
def test_a_setting_from_chat_is_applied_once_and_a_tray_press_since_is_never_undone(tmp_path):
    out = _run(tmp_path, {"daemonUp": False, "steps": [
        {"config": "start_with_windows = true\n", "sync": True},
        {"tray": "login", "value": False},   # the user unticks the tray; the daemon is down
        {"sync": True},                      # the old "true" is still in config.toml
        {"sync": True},
    ]})
    first, pressed, again, again2 = out["steps"]
    assert first["loginOn"] is True and out["loginSet"][0] == {"openAtLogin": True, "args": ["--hidden"]}
    assert first["settings"]["appSwitchesSeen"] == {"start_with_windows": True}
    assert first["settings"]["startWithWindowsAsked"] is True, "chosen in settings: never asked again"
    assert pressed["loginOn"] is False
    assert pressed["settings"]["appSwitchesPending"] == {"start_with_windows": False}
    # THE RULE: the older setting never undoes the tray; the press is sent again.
    assert again["loginOn"] is False and again2["loginOn"] is False
    assert again2["puts"] > pressed["puts"], "the waiting press is sent again at each check"
    body = out["puts"][0]
    assert body["path"] == "/settings/values" and body["method"] == "PUT"
    assert body["body"] == {"values": {"start_with_windows": False}, "actor": "desktop"}
    assert body["auth"] == "Bearer tok"


@requires_node
def test_a_tray_press_the_daemon_takes_is_seen_and_not_applied_back(tmp_path):
    out = _run(tmp_path, {"daemonUp": True, "writeConfigOnPut": True, "steps": [
        {"config": "keep_running_in_background = true\n", "sync": True},
        {"tray": "keep", "value": False},
        {"sync": True},
    ]})
    applied, pressed, after = out["steps"]
    assert applied["keepRunningPref"] is True
    assert applied["settings"]["keepRunningInBackground"] is True
    assert pressed["settings"]["appSwitchesSeen"]["keep_running_in_background"] is False
    assert "keep_running_in_background" not in (pressed["settings"].get("appSwitchesPending") or {})
    assert after["keepRunningPref"] is False


@requires_node
def test_not_set_adopts_the_trays_choice_once_and_nothing_is_sent_before_the_daemon(tmp_path):
    out = _run(tmp_path, {"daemonUp": True, "loginOn": True, "keepRunning": True, "steps": [
        {"sync": "nopush"},
        {"sync": True},
    ]})
    early, later = out["steps"]
    assert early["puts"] == 0, "the very first check runs before the daemon: it sends nothing"
    sent = {k: v for p in out["puts"] for k, v in p["body"]["values"].items()}
    assert sent == {"start_with_windows": True, "keep_running_in_background": True}
    assert later["loginOn"] is True and out["loginSet"] == [], "adopting changes nothing here"


@requires_node
def test_a_dev_run_never_touches_the_login_item(tmp_path):
    out = _run(tmp_path, {"packaged": False, "steps": [{"config": "start_with_windows = true\n", "sync": True}]})
    assert out["loginSet"] == []
    assert out["steps"][0]["settings"]["appSwitchesSeen"] == {"start_with_windows": True}


@requires_node
def test_the_daemons_own_config_toml_is_read_by_the_shell(tmp_path):
    """The two halves agree on the FILE: what the settings writer persists
    (tomli_w) is what the shell reads."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    persist_config_values(home, {"update_channel": "stable", "start_with_windows": True,
                                 "keep_running_in_background": False, "comm": {"channels": {}}})
    out = _run(tmp_path, {"pure": {"settings": [], "decisions": []}})
    assert out["pure"]["layered"] == {"start_with_windows": True, "keep_running_in_background": False}
    persist_config_values(home, {"start_with_windows": None})
    out = _run(tmp_path, {"pure": {"settings": [], "decisions": []}})
    assert out["pure"]["layered"]["start_with_windows"] is None, "an Undo back to not set removes the line"


def test_the_shell_reads_both_switches_at_start_and_every_check():
    src = _src()
    assert src.count("syncDesktopSwitches({ push: false });") == 1
    assert "installDesktopSwitches(); //" in src
    assert 'const START_WITH_WINDOWS_KEY = "start_with_windows";' in src
    assert 'const KEEP_IN_BACKGROUND_KEY = "keep_running_in_background";' in src
    # Every user press writes back: the tray, the app menu, the question's Yes.
    assert src.count("noteDesktopSwitchByUser(START_WITH_WINDOWS_KEY") == 2
    assert "noteDesktopSwitchByUser(KEEP_IN_BACKGROUND_KEY, keepRunningPref)" in src


# --------------------------------------------------------------------------- #
# the daemon
# --------------------------------------------------------------------------- #


def test_the_rows_their_tiers_and_the_tolerant_fields(tmp_path):
    start = schema.get("start_with_windows")
    keep = schema.get("keep_running_in_background")
    assert start.type == keep.type == "bool" and start.unset_ok and keep.unset_ok
    assert start.tier_for(True) == "ask-floor", "starting by itself after a restart asks every time"
    assert start.tier_for(False) == "allow"
    assert keep.tier_for(True) == keep.tier_for(False) == "allow"
    assert "15 seconds" in start.applies and "15 seconds" in keep.applies
    cfg = Config(home=tmp_path, project_root=tmp_path)
    assert cfg.start_with_windows is None and cfg.keep_running_in_background is None
    cfg.start_with_windows = "yes"  # a hand edit never stops a boot
    assert cfg.start_with_windows is None


@pytest.mark.parametrize(
    "text, key",
    [
        ("Start Iron Jarvis with Windows", "start_with_windows"),
        ("please start with windows", "start_with_windows"),
        ("don't start with Windows anymore", "start_with_windows"),
        ("make Jarvis start at login", "start_with_windows"),
        ("keep running in the background when I close the window", "keep_running_in_background"),
        ("stop running in the background when I close the window", "keep_running_in_background"),
        ("quit when I close the window", "keep_running_in_background"),
        ("please minimize to the tray when I close it", "keep_running_in_background"),
    ],
)
def test_a_request_arms_the_settings_writers(text, key):
    ask = settings_ask(text)
    assert ask.writers and key in ask.keys, (text, ask)


@pytest.mark.parametrize(
    "text",
    [
        "start Windows update",
        "Start the Windows update now",
        "start Windows Defender scan",
        "running in the background is slow",
        "start Windows in safe mode",
        "my app keeps running in the background and drains the battery",
    ],
)
def test_a_windows_sentence_arms_no_writer(text):
    assert not settings_ask(text).writers, text


def test_chat_turns_it_off_at_once_and_on_only_through_the_protected_tool(tmp_path):
    import asyncio

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.tools.base import ToolContext

    app = create_app(str(tmp_path))
    p = app.state.platform
    reg = p.registry
    ctx = ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
        event_bus=p.event_bus, engine=p.engine,
    )
    res = asyncio.run(reg.get("config_set").execute({"key": "start_with_windows", "value": "on"}, ctx))
    assert not res.ok and "config_change_protected" in res.error
    res = asyncio.run(reg.get("config_set").execute({"key": "keep_running_in_background", "value": "off"}, ctx))
    assert res.ok and "within about 15 seconds" in res.output, res.output
    assert p.config.keep_running_in_background is False
    assert "keep_running_in_background = false" in (p.config.home / "config.toml").read_text("utf-8")
    res = asyncio.run(
        reg.get("config_change_protected").execute({"key": "start_with_windows", "value": True}, ctx)
    )
    assert res.ok and p.config.start_with_windows is True


def test_undo_back_to_not_set_removes_the_value(tmp_path):
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    w = app.state.platform.config_writer
    w.apply({"start_with_windows": True}, actor="chat")
    w.restore({"start_with_windows": None})
    assert app.state.platform.config.start_with_windows is None
    toml = (app.state.platform.config.home / "config.toml").read_text("utf-8")
    assert "start_with_windows" not in toml


def test_a_tray_press_is_ledgered_as_the_desktop(tmp_path):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.put("/settings/values", json={"values": {"start_with_windows": False}, "actor": "desktop"})
    assert r.status_code == 200, r.text
    r = client.put("/settings/values", json={"values": {"keep_running_in_background": True}, "actor": "anyone"})
    assert r.status_code == 200, r.text
    with session_scope(app.state.platform.engine) as db:
        actors = [json.loads(i.args_json)["actor"] for i in db.exec(select(ToolInvocation).where(
            ToolInvocation.tool == "update_settings"))]
    assert sorted(actors) == ["desktop", "settings_page"], "only the one word is taken"
