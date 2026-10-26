"""v1.391.0 (the long-jobs plan's Phase 2, decision 1): the PC does not
sleep while work runs.

A coding reply runs for an hour; Windows put the PC to sleep in the middle of
it, and the reply stopped mid-step. Nothing called Electron's
``powerSaveBlocker``. Now the desktop app holds ONE
``prevent-app-suspension`` blocker while the daemon says work is in flight
(``GET /system/activity`` ``busy`` — a chat or coding reply, also one waiting
on the user's answer; an agent run or a mission; a workflow run; a Build
terminal whose coding tool works) and lets go the moment nothing is, and on
Quit. It never keeps the display awake. "Keep this PC awake while work runs"
(Settings › System, on by default) is read LIVE from the same answer.

House idiom (``test_desktop_reliability_v1249.py``, ``test_update_channel_
v1260.py``): the shipped functions are LIFTED out of main.js verbatim and run
under node against a stand-in blocker, so reverting them turns these red.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src() -> str:
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fn(name: str) -> str:
    src = _src()
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _run(script: str, tmp_path: Path, name: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8",
                          timeout=120, env={**os.environ})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_BLOCKER = """
const calls = [];
let nextId = 41;
const started = new Set();
const blocker = {
  start(type) { calls.push(["start", type]); const id = nextId++; started.add(id); return id; },
  stop(id) { calls.push(["stop", id]); started.delete(id); },
  isStarted(id) { return started.has(id); },
};
"""


@requires_node
def test_the_blocker_is_held_only_while_work_runs_and_never_stacked(tmp_path):
    script = "\n".join([
        _decl("KEEP_AWAKE_MISSES_RELEASE"), _decl("KEEP_AWAKE_TYPE"),
        _fn("keepAwakeDecision"), _fn("applyKeepAwake"), _BLOCKER,
        """
const state = { id: null, misses: 0 };
const out = [];
function step(activity) {
  const d = keepAwakeDecision(activity, state);
  state.misses = d.misses;
  const changed = applyKeepAwake(d.want, blocker, state);
  out.push({ held: state.id !== null, changed, live: started.size });
}
step({ busy: false, keep_awake: true });    // 0 idle
step({ busy: true, keep_awake: true });     // 1 a reply works
step({ busy: true, keep_awake: true });     // 2 still: never a second blocker
step({ busy: false, keep_awake: true });    // 3 nothing runs: let go at once
step({ busy: true });                       // 4 an older daemon (no key): on by default
step({ busy: true, keep_awake: false });    // 5 the setting turned off: let go
step({ busy: true, keep_awake: true });     // 6 on again
step(null);                                 // 7 no answer: keep it (a busy moment)
step(null);                                 // 8
step(null);                                 // 9 third miss in a row: let go
step(null);                                 // 10 still nothing: never started from nothing
started.clear();                            // the OS dropped it behind our back
step({ busy: true, keep_awake: true });     // 11
step({ busy: true, keep_awake: true });     // 12
console.log(JSON.stringify({ out, calls }));
""",
    ])
    got = _run(script, tmp_path, "keep_awake")
    held = [o["held"] for o in got["out"]]
    assert held == [False, True, True, False, True, False, True, True, True, False, False, True, True]
    assert all(o["live"] <= 1 for o in got["out"]), "ONE blocker at a time, never stacked"
    starts = [c for c in got["calls"] if c[0] == "start"]
    assert {c[1] for c in starts} == {"prevent-app-suspension"}, "sleep only, never the display"
    assert len(starts) == 3 + 1, "started at 1, 4, 6 and again when the OS dropped it"
    stops = [c for c in got["calls"] if c[0] == "stop"]
    assert len(stops) == 3, "let go at 3, 5 and after the third miss"


@requires_node
def test_the_tray_says_so_only_while_the_blocker_is_held(tmp_path):
    stubs = """
const Menu = { buildFromTemplate: (t) => t };
let pendingUpdateInfo = null;
const hotkeyState = { window: null, spotlight: null };
function accelLabel(a) { return String(a); }
function showMainWindow() {}
function toggleSpotlight() {}
function reloadUI() {}
const _services = {};
function restartServicesFromTray() {}
function openLogsFolder() {}
let keepRunningPref = null;
function setKeepRunningPref() {}
let hwAccelDisabled = false;
function setHwAccelPref() {}
const IS_PACKAGED = false;
function getStartAtLogin() { return false; }
function setStartAtLoginFromMenu() {}
let isQuitting = false;
const app = { quit() {} };
function requestUpdateInstall() {}
"""
    script = "\n".join([
        stubs, _decl("KEEP_AWAKE_TRAY_WORDS"), "const keepAwakeState = { id: null, misses: 0 };",
        _fn("keepAwakeHeld"), _fn("buildTrayContextMenu"),
        """
const idle = buildTrayContextMenu().map((i) => i.label || i.type);
keepAwakeState.id = 7;
const held = buildTrayContextMenu();
console.log(JSON.stringify({ idle, held: held.map((i) => i.label || i.type), first: held[0] }));
""",
    ])
    got = _run(script, tmp_path, "tray")
    words = "Keeping this PC awake while work runs"
    assert words not in got["idle"]
    assert got["held"][0] == words and got["first"]["enabled"] is False, "a line, not a press"


def test_quit_lets_go_and_the_display_is_never_held():
    src = _src()
    assert "prevent-display-sleep" not in src
    before_quit = src[src.index('app.on("before-quit"'):src.index('app.on("will-quit"')]
    assert "releaseKeepAwake()" in before_quit
    start = src.index('app.on("will-quit"')
    will_quit = src[start:src.index("\n  });\n", start)]
    assert "releaseKeepAwake()" in will_quit
    boot = src.index("bootComplete = true;")
    assert "installKeepAwake();" in src[boot:src.index("\n}\n", boot)], "armed at start-up, once"
    assert src.count("installKeepAwake();") == 1
    # Read through the daemon's activity answer, with the install token.
    tick = _fn("keepAwakeTick")
    assert "probeDaemonActivity(" in tick and "powerSaveBlocker" in tick


# --------------------------------------------------------------------------- #
# the daemon's half: the answer the desktop reads
# --------------------------------------------------------------------------- #


def test_the_activity_answer_carries_the_setting_live(tmp_path):
    from iron_jarvis.settings.schema import daemon_keys, get

    row = get("keep_awake")
    assert row is not None and row.group == "system" and row.tier == "allow" and row.type == "bool"
    assert row.restart is False, "read live, never at a restart"
    assert "keep_awake" in daemon_keys()
    client = TestClient(create_app(str(tmp_path / "home")))
    body = client.get("/system/activity").json()
    assert body["keep_awake"] is True and body["busy"] is False, "on by default; idle"
    r = client.put("/settings/values", json={"values": {"keep_awake": False}})
    assert r.status_code == 200, r.text
    assert client.get("/system/activity").json()["keep_awake"] is False


async def test_a_coding_reply_waiting_on_your_answer_is_busy_but_not_working(tmp_path):
    """MOVED in the review (v1.391.0): a reply parked on an approval card is
    still ``busy`` (an update cuts it off, so the update's warning names it),
    but it is not WORKING — it goes on only when the person answers, so it no
    longer holds the PC awake (decision 1: "only while ... working"; the
    doer's version said "so the answer can still reach it", but a sleeping
    PC wakes with the card still open)."""
    from tests.test_build_sessions_v1367 import Script, asgi, new_session, open_turn, until, wire
    from iron_jarvis.providers.adapters.base import ToolCall

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work, approval_mode="always_ask")["id"]
    wire(app, Script([[ToolCall(id="c1", name="Write", arguments={"file_path": "a.txt", "content": "1"})]]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "make a", "turn_id": "ka-1"}, drop=drop, timeout=60))
    logs = await until(lambda: getattr(app.state.platform, "turn_logs", None), 30)
    await until(lambda: logs.get("ka-1") is not None and any(e == "approval" for e, _ in logs.get("ka-1").frames), 30)
    status, body = await asgi(app, "GET", "/system/activity")
    assert status == 200 and body["busy"] is True and body["chat_replies"] == 1
    assert body["working"] is False, "parked on the user: not work that runs"
    await logs.shutdown()
    drop.set()
    await asyncio.wait_for(follower, 30)
    status, body = await asgi(app, "GET", "/system/activity")
    assert body["busy"] is False
