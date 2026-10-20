"""v1.361.0 lane 11A — one notice per outcome, and NONE LOST.

The final regression hunt over v1.359.0..v1.360.1 found four notices that
v1.360.0's "one notice per outcome" rules lost. Each worked at v1.359.0;
each is restored here WITHOUT bringing a duplicate back:

(1) A POP-OUT'S BELL. v1.360.1 silenced every popped-out window's bell while
    the tray watchers keyed on the MAIN window alone, so with the main window
    in the tray and a module popped out most notices raised no toast. Now
    ONE window speaks: the main window while it is open, else the first
    pop-out still open (main.js ``announcePopoutSpeaker`` → preload
    ``popout.speaks()``), and the tray watchers count a pop-out as an open
    window (``speakingWindowOpen``) so nothing is said twice.
(2) A SCHEDULED WORKFLOW'S STEP. Inside a schedule fire the schedule's own
    line is the ONE line every destination gets, so it now carries what the
    steps' alerts used to: a step with ``on_failure: skip`` that failed makes
    the fire ``partial`` and is named with its own words, and a halted run
    names the step that stopped it.
(3) A FAILED WORKFLOW AFTER A RETRIED STEP. ``workflow.completed`` carries
    ``failed_session`` (the run of the step that ENDED it), and only THAT
    step's notice stands in for the workflow's — on the bell and in the tray.
(4) is the bell's words (dashboard/__tests__/no-notice-lost-v13610.test.tsx).

Pinned through the REAL app factory, the real scheduler entry point (Run
now), the real notifier and engine, and the real main.js / preload.js
functions under node. The real daemon's events feed
``dashboard/__tests__/fixtures/no-notice-lost-v13610.json`` (regenerate with
``IJ_REGEN_NO_NOTICE_LOST=1``), which the real bell reads too.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import bell_pings
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentState
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.workflows.step_outcome import ended_by_session, step_failures
from tests.test_one_notice_v13600 import _lift as _lift_run_outcome

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "desktop" / "main.js"
PRELOAD = ROOT / "desktop" / "preload.js"
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "no-notice-lost-v13610.json"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

INBOX = "The inbox could not be reached."


@pytest.fixture
def desktop_sink(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: sent.append(message))
    return sent


def _fake_runtime():
    """A run fails when its task says FAIL; a FLAKY task fails its FIRST
    attempt only (so ``on_failure: retry`` succeeds on the second)."""
    tries: dict[str, int] = {}

    async def run(session, agent_def, *a, **kw):
        task = session.task or ""
        failed = "FAIL" in task
        if "FLAKY" in task:
            tries["flaky"] = tries.get("flaky", 0) + 1
            failed = tries["flaky"] == 1
        return SimpleNamespace(
            state=AgentState.FAILED if failed else AgentState.COMPLETED,
            provider=session.provider,
            model=session.model,
            result=INBOX if failed else "Wrote the summary.",
            input_tokens=0,
            output_tokens=0,
        )

    return run


def _phone(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("no mock channel")


def _capture(platform) -> list[dict]:
    seen: list[dict] = []

    def handler(ev) -> None:
        if ev.type in (EventType.SESSION_COMPLETED, EventType.WORKFLOW_COMPLETED):
            seen.append({"type": ev.type, "session_id": ev.session_id, "payload": dict(ev.payload or {})})

    platform.event_bus.add_handler(handler)
    return seen


def _audit(client) -> list[dict]:
    """The daemon's answer at the path desktop/main.js reads."""
    r = client.get("/audit", params={"type": "session.completed,workflow.completed", "since": "", "limit": "50"})
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def _fire(tmp_path, monkeypatch, steps: list[dict]):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_runtime())
    events = _capture(platform)
    r = client.post("/workflows", json={"name": "Nightly", "steps": steps})
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "nightly", "cron": "0 3 * * *", "kind": "workflow", "payload": {"workflow": "Nightly"}},
    )
    assert r.status_code == 200, r.text
    phone = _phone(app)
    before = len(phone.sent)
    body = client.post("/schedules/nightly/run").json()
    return SimpleNamespace(body=body, phone=phone.sent[before:], events=events, client=client)


FETCH_SKIP = {"name": "Fetch", "kind": "agent", "task": "FAIL: fetch the inbox", "on_failure": "skip"}
FETCH_HALT = {"name": "Fetch", "kind": "agent", "task": "FAIL: fetch the inbox", "on_failure": "halt"}
WRITE = {"name": "Write", "kind": "agent", "task": "Write the summary"}
# A tool step that fails on its own (no run of its own, so no bell row).
CHECK_TOOL = {"name": "Check", "kind": "tool", "tool": "read_file", "args": {"path": "no-such-report.txt"}}


# --------------------------------------------------------------------------- #
# (2) the scheduled workflow's ONE line carries its failed step
# --------------------------------------------------------------------------- #


def test_a_skipped_step_that_failed_makes_the_fire_partial_and_every_destination_hears_it(
    tmp_path, monkeypatch, desktop_sink
):
    out = _fire(tmp_path, monkeypatch, [FETCH_SKIP, WRITE])
    # The row: part of it failed, the step named with its own words (it read
    # "ok — workflow “Nightly” ran" at v1.360.1).
    assert out.body["last_status"] == ro.STATUS_PARTIAL, out.body
    assert "“Fetch”" in out.body["last_detail"] and INBOX in out.body["last_detail"], out.body
    # The phone (every non-desktop destination): ONE line, and it says which
    # step and why — never a bare "done".
    assert len(out.phone) == 1, out.phone
    line = out.phone[0]
    assert line.startswith("Schedule “nightly”: finished, but part of it failed — "), line
    assert "Fetch" in line and INBOX in line, line
    # This PC: the bell rings the failed step's own run ("A step of your
    # workflow Nightly could not finish"), so it is quiet — one notice.
    [step] = [e for e in out.events if e["type"] == EventType.SESSION_COMPLETED and e["payload"].get("status") == "failed"]
    assert bell_pings(step), step
    assert desktop_sink == [], desktop_sink


def test_a_skipped_tool_step_has_no_bell_row_so_this_pc_keeps_the_schedules_line(
    tmp_path, monkeypatch, desktop_sink
):
    out = _fire(tmp_path, monkeypatch, [dict(CHECK_TOOL, on_failure="skip"), WRITE])
    assert out.body["last_status"] == ro.STATUS_PARTIAL, out.body
    assert len(out.phone) == 1 and "“Check”" in out.phone[0], out.phone
    # No step run rang the bell: "This PC" says the schedule's line, once.
    assert len(desktop_sink) == 1 and "“Check”" in desktop_sink[0], desktop_sink
    assert desktop_sink[0].startswith("Schedule “nightly”: finished, but part of it failed — ")


def test_a_halted_scheduled_workflow_names_the_step_that_stopped_it(tmp_path, monkeypatch, desktop_sink):
    out = _fire(tmp_path, monkeypatch, [FETCH_HALT, WRITE])
    assert out.body["last_status"] == ro.STATUS_ERROR, out.body
    assert out.body["last_detail"] == (
        f"The run failed: workflow “Nightly” stopped at its step “Fetch”: {INBOX}"
    ), out.body
    assert len(out.phone) == 1, out.phone
    assert out.phone[0].startswith("Schedule “nightly”: FAILED — The run failed: "), out.phone
    assert "Fetch" in out.phone[0] and INBOX in out.phone[0], out.phone
    # The bell rings the failed workflow: "This PC" stays quiet (unchanged).
    assert desktop_sink == [], desktop_sink


def test_a_clean_scheduled_workflow_still_reads_done(tmp_path, monkeypatch, desktop_sink):
    out = _fire(tmp_path, monkeypatch, [WRITE])
    assert out.body["last_status"] == ro.STATUS_OK, out.body
    assert out.phone == ["Schedule “nightly”: done — workflow “Nightly” ran"], out.phone
    assert desktop_sink == ["Schedule “nightly”: done — workflow “Nightly” ran"], desktop_sink


def test_the_schedule_words_for_a_workflow_step():
    assert ro.workflow_failed_reason("Nightly", "Fetch", f"The run failed: {INBOX}") == (
        f"workflow “Nightly” stopped at its step “Fetch”: {INBOX}"
    )
    assert ro.workflow_failed_reason("Nightly", "Fetch", "") == "workflow “Nightly” stopped at its step “Fetch”."
    # No step to name (an unreadable record): where to look, never a dash aside.
    assert ro.workflow_failed_reason("Nightly") == (
        "your workflow “Nightly” failed. Its run history on the Workflows page says where."
    )
    two = [("Fetch", {"summary": "no inbox"}), ("Send", {"summary": ""})]
    assert ro.workflow_skipped_detail("Nightly", two) == (
        "workflow “Nightly” ran, but its step “Fetch” could not finish: no inbox. "
        "1 more step could not finish either."
    )
    assert ro.workflow_skipped_detail("Nightly", []) == "workflow “Nightly” ran"


def test_step_failures_reads_the_step_that_ended_the_run_and_the_ones_it_went_past():
    outputs = {
        "A": {"status": "completed"},
        "B": {"status": "failed", "handled": "skipped", "session_id": "s_b"},
        "C": {"status": "failed", "session_id": "s_c"},
        "D": {"status": "failed"},
    }
    halted, skipped = step_failures(["A", "B", "C", "D"], outputs)
    assert halted == ("C", outputs["C"]) and skipped == [("B", outputs["B"])]
    assert ended_by_session(["A", "B", "C"], outputs) == "s_c"
    assert ended_by_session(["A", "D", "C"], outputs) == ""  # D has no run of its own
    assert ended_by_session(["A", "B"], outputs) == ""  # nothing ended it
    assert step_failures(None, None) == (None, [])


# --------------------------------------------------------------------------- #
# (3) workflow.completed names the step that ENDED it; the tray says the rest
# --------------------------------------------------------------------------- #

_ID_RX = re.compile(r"^(session|wfrun|evt|wf|run)_[0-9a-f]{8,}$")


def _stable(obj, names: dict[str, str]):
    if isinstance(obj, dict):
        return {k: _stable(v, names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stable(v, names) for v in obj]
    if isinstance(obj, str) and _ID_RX.match(obj):
        if obj not in names:
            names[obj] = f"{obj.split('_', 1)[0]}_{len(names) + 1:02d}"
        return names[obj]
    return obj


def _with_ts(entries: list[dict]) -> list[dict]:
    """Audit entries newest first, with distinct stable times."""
    n = len(entries)
    return [{**e, "ts": f"2026-10-20T03:00:{(n - i):02d}"} for i, e in enumerate(entries)]


def _check_or_write(key: str, value) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if os.environ.get("IJ_REGEN_NO_NOTICE_LOST") == "1":
        data.setdefault(
            "_about",
            "v1.361.0 lane 11A: what the REAL daemon publishes for a failed workflow "
            "(tests/test_no_notice_lost_v13610.py writes it with IJ_REGEN_NO_NOTICE_LOST=1; "
            "dashboard/__tests__/no-notice-lost-v13610.test.tsx feeds it through the real "
            "bell). `audit` is GET /audit's answer (newest first), ids and times made stable.",
        )
        data[key] = value
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    assert key in data, f"the fixture has no {key!r}: regenerate with IJ_REGEN_NO_NOTICE_LOST=1"
    assert data[key] == value, f"the daemon's {key!r} changed: if on purpose, regenerate with IJ_REGEN_NO_NOTICE_LOST=1"


def _retried_then_tool_failed(tmp_path, monkeypatch):
    """Fetch fails its first attempt and its retry succeeds; then a TOOL step
    ends the run. The step's first attempt rang on its own — and the
    workflow's own failure is a different outcome."""
    fetch = {"name": "Fetch", "kind": "agent", "task": "FLAKY: fetch the inbox", "on_failure": "retry"}
    return _fire(tmp_path, monkeypatch, [fetch, CHECK_TOOL])


def test_a_workflow_ended_by_a_step_with_no_run_names_no_session(tmp_path, monkeypatch):
    out = _retried_then_tool_failed(tmp_path, monkeypatch)
    sessions = [e for e in out.events if e["type"] == EventType.SESSION_COMPLETED]
    [wf] = [e for e in out.events if e["type"] == EventType.WORKFLOW_COMPLETED]
    assert [s["payload"]["status"] for s in sessions] == ["failed", "completed"], sessions
    assert wf["payload"]["status"] == "failed"
    assert wf["payload"]["sessions"] == [s["session_id"] for s in sessions]
    # The step that ENDED it (Check) had no run of its own.
    assert wf["payload"]["failed_session"] == "", wf
    names: dict[str, str] = {}
    _check_or_write(
        "retried_then_tool_failed",
        {"events": _stable([wf, *reversed(sessions)], names), "audit": _with_ts(_stable(_audit(out.client), names))},
    )


def test_a_workflow_ended_by_an_agent_step_names_that_steps_run(tmp_path, monkeypatch):
    out = _fire(tmp_path, monkeypatch, [FETCH_HALT, WRITE])
    [step] = [e for e in out.events if e["type"] == EventType.SESSION_COMPLETED]
    [wf] = [e for e in out.events if e["type"] == EventType.WORKFLOW_COMPLETED]
    assert wf["payload"]["failed_session"] == step["session_id"], (wf, step)
    names: dict[str, str] = {}
    _check_or_write(
        "halted_by_agent_step",
        {"events": _stable([wf, step], names), "audit": _with_ts(_stable(_audit(out.client), names))},
    )


def test_a_workflow_that_did_not_fail_names_no_session(tmp_path, monkeypatch):
    out = _fire(tmp_path, monkeypatch, [FETCH_SKIP, WRITE])
    [wf] = [e for e in out.events if e["type"] == EventType.WORKFLOW_COMPLETED]
    assert wf["payload"]["status"] == "completed" and wf["payload"]["failed_session"] == "", wf


def test_an_ask_step_that_ends_a_run_after_the_answer_names_no_session(tmp_path, monkeypatch):
    """resume_after_answer's halt path: an ask whose gate the answer did not
    satisfy ends the run — an ask has no run of its own. The REAL engine on
    the real platform, parked and answered as the answer route does."""
    import asyncio

    from iron_jarvis.workflows.engine import WorkflowEngine, load_workflow

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_runtime())
    events = _capture(platform)
    wf = load_workflow(
        {
            "name": "Gated",
            "steps": [
                {"name": "Approve", "kind": "ask", "message": "Send it?", "expect": {"summary_contains": ["yes"]}},
                WRITE,
            ],
        }
    )
    engine = WorkflowEngine(platform, platform.orchestrator)
    parked = asyncio.run(engine.run(wf))
    assert parked.status == "waiting", parked
    final = asyncio.run(engine.resume_after_answer(parked, "no"))
    assert final.status == "failed", final
    [done] = [e for e in events if e["type"] == EventType.WORKFLOW_COMPLETED]
    assert done["payload"]["status"] == "failed" and done["payload"]["failed_session"] == "", done


def _node(script: str, tmp_path: Path, payload: dict, name: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), json.dumps(payload)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


_PLAN = """
const input = JSON.parse(process.argv[2]);
let state = { seen: {}, announced: {} };
const out = [];
for (const step of input.steps) {
  const plan = planRunOutcomeNotifications(state, step.entries, !!step.windowOpen, 0);
  state = plan.state;
  out.push(plan.notify.map((n) => n.title));
}
process.stdout.write(JSON.stringify({ out }) + "\\n");
"""


def _fixture(key: str) -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[key]


@requires_node
def test_in_the_tray_a_failed_workflow_is_said_even_after_its_steps_first_attempt_was(tmp_path):
    """The REAL daemon's audit (newest first): the step's failed first
    attempt is read on one tick and announced; the workflow's own failure
    lands on the next — and is announced too (v1.360.1 dropped it because a
    covered step had been announced)."""
    audit = _fixture("retried_then_tool_failed")["audit"]
    first = [e for e in audit if e["type"] == "session.completed" and e["payload"].get("status") == "failed"]
    steps = [{"entries": first}, {"entries": audit}]
    res = _node(_lift_run_outcome() + "\n" + _PLAN, tmp_path, {"steps": steps}, "plan_retry")
    assert res["out"] == [
        ["A step of your workflow Nightly could not finish"],
        ["Your workflow Nightly could not finish"],
    ], res


@requires_node
def test_in_the_tray_the_step_that_ended_the_run_still_stands_in_for_it(tmp_path):
    """The control: the step whose failure ENDED the run was announced, so
    the workflow's notice is the same outcome and is not said again."""
    audit = _fixture("halted_by_agent_step")["audit"]
    first = [e for e in audit if e["type"] == "session.completed"]
    steps = [{"entries": first}, {"entries": audit}]
    res = _node(_lift_run_outcome() + "\n" + _PLAN, tmp_path, {"steps": steps}, "plan_halt")
    assert res["out"] == [["A step of your workflow Nightly could not finish"], []], res
    # In ONE read the workflow's notice stands in for its step's (unchanged).
    res = _node(_lift_run_outcome() + "\n" + _PLAN, tmp_path, {"steps": [{"entries": audit}]}, "plan_one")
    assert res["out"] == [["Your workflow Nightly could not finish"]], res


# --------------------------------------------------------------------------- #
# (1) one window speaks: the main window, else the first pop-out
# --------------------------------------------------------------------------- #


def _src(path: Path = MAIN) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fn(name: str) -> str:
    src = _src()
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


_WINDOWS = """
let mainWin = null;
const popouts = new Map();
const log = [];
function fakeWin(name) {
  return {
    destroyed: false,
    isDestroyed() { return this.destroyed; },
    webContents: { send: (channel, value) => log.push([name, channel, value]) },
  };
}
__LIFTED__
const steps = [];
const snap = (label) => {
  log.length = 0;
  announcePopoutSpeaker();
  steps.push({ label, said: log.slice(), open: speakingWindowOpen() });
};
mainWin = fakeWin("main");
popouts.set("/chat", fakeWin("/chat"));
popouts.set("/terminals", fakeWin("/terminals"));
snap("main open");
mainWin = null;
snap("main closed to the tray");
popouts.get("/chat").destroyed = true;
snap("first pop-out closed");
popouts.get("/terminals").destroyed = true;
snap("no window left");
mainWin = fakeWin("main");
popouts.set("/files", fakeWin("/files"));
snap("main back");
process.stdout.write(JSON.stringify({ steps, pure: [
  popoutSpeakerRoute(true, ["/chat"]),
  popoutSpeakerRoute(false, ["/chat", "/terminals"]),
  popoutSpeakerRoute(false, []),
] }) + "\\n");
"""


@requires_node
def test_one_window_speaks_the_main_window_else_the_first_pop_out(tmp_path):
    lifted = "\n".join(
        _fn(n)
        for n in ("popoutPaths", "livePopout", "popoutSpeakerRoute", "announcePopoutSpeaker", "speakingWindowOpen")
    )
    res = _node(_WINDOWS.replace("__LIFTED__", lifted), tmp_path, {}, "windows")
    by = {s["label"]: s for s in res["steps"]}
    assert by["main open"]["said"] == [["/chat", "popout:speaker", False], ["/terminals", "popout:speaker", False]]
    assert by["main open"]["open"] is True
    # Closed to the tray: the FIRST pop-out speaks, the other stays quiet, and
    # the tray watchers see an open window (they stay quiet for its bell).
    assert by["main closed to the tray"]["said"] == [
        ["/chat", "popout:speaker", True],
        ["/terminals", "popout:speaker", False],
    ]
    assert by["main closed to the tray"]["open"] is True
    assert by["first pop-out closed"]["said"] == [["/terminals", "popout:speaker", True]]
    # No window left: the tray watchers speak.
    assert by["no window left"] == {"label": "no window left", "said": [], "open": False}
    assert by["main back"]["said"] == [["/files", "popout:speaker", False]]
    assert res["pure"] == ["", "/chat", ""]


_TICK = """
let mainWin = null;
const popouts = new Map();
const seen = [];
let tick = null;
const setInterval = (fn) => { tick = fn; };
const ASK_WATCH_MS = 1;
let askSeen = {}, paneBlockedSeen = {}, runOutcomeState = { seen: {}, announced: {} };
let runOutcomeSince = "", runOutcomeWindowSeenAt = 0;
function daemonRequest(method, path) {
  if (path.startsWith("/chat/approvals")) return Promise.resolve({ approvals: [] });
  if (path.startsWith("/terminals")) return Promise.resolve({ panes: [] });
  return Promise.resolve({ entries: [] });
}
function planAskNotifications(s, list, now, open) { seen.push(["asks", open]); return { seen: {}, notify: [], waiting: 0 }; }
function planPaneNotifications(s, panes, open) { seen.push(["panes", open]); return { seen: {}, notify: [] }; }
function planRunOutcomeNotifications(s, e, open) { seen.push(["runs", open]); return { state: s, notify: [] }; }
function setAskWaitingTooltip() {}
function showAskNotification() {}
function runOutcomePath() { return "/audit"; }
function runOutcomeNewestTs() { return ""; }
__LIFTED__
installAskWatcher();
const live = { isDestroyed: () => false, webContents: { send() {} } };
(async () => {
  const out = {};
  for (const [label, main, pops] of [["main open", live, 0], ["a pop-out only", null, 1], ["nothing", null, 0]]) {
    mainWin = main;
    popouts.clear();
    for (let i = 0; i < pops; i++) popouts.set(`/p${i}`, live);
    seen.length = 0;
    tick();
    await new Promise((r) => setTimeout(r, 20));
    out[label] = seen.slice().sort();
  }
  process.stdout.write(JSON.stringify(out) + "\\n");
})();
"""


@requires_node
def test_the_tray_watchers_count_a_speaking_pop_out_as_an_open_window(tmp_path):
    lifted = "\n".join(_fn(n) for n in ("popoutPaths", "livePopout", "speakingWindowOpen", "installAskWatcher"))
    res = _node(_TICK.replace("__LIFTED__", lifted), tmp_path, {}, "tick")
    both = lambda v: [["asks", v], ["panes", v], ["runs", v]]  # noqa: E731
    assert res["main open"] == both(True)
    # v1.360.1 read the main window alone here: with it in the tray and a
    # module popped out, the pop-out's bell was silent AND so was this.
    assert res["a pop-out only"] == both(True)
    assert res["nothing"] == both(False)


def test_main_names_the_speaker_whenever_a_window_opens_or_closes():
    src = _src()
    create = src[src.index("function createMainWindow()") : src.index("function armDashboardReload()")]
    closed = create[create.index('mainWin.on("closed", () => {') :]
    assert closed.index("mainWin = null;") < closed.index("announcePopoutSpeaker();"), "the close names the next speaker"
    assert create.count("announcePopoutSpeaker();") == 2, "the main window's own open quiets the pop-outs"
    pop = _fn("openPopout")
    closing = pop[pop.index('win.on("closed", () => {') :]
    assert closing.index("popouts.delete(route)") < closing.index("announcePopoutSpeaker();")
    assert 'win.webContents.on("did-finish-load", () => announcePopoutSpeaker());' in pop


_PRELOAD_RUN = """
const Module = require("module");
const handlers = {};
let api = null;
const fake = {
  contextBridge: { exposeInMainWorld: (key, value) => { api = value; } },
  ipcRenderer: {
    on: (channel, fn) => { handlers[channel] = fn; },
    send() {},
    invoke() { return Promise.resolve(null); },
    removeListener() {},
  },
};
const origLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "electron") return fake;
  return origLoad.call(this, request, ...rest);
};
global.window = { localStorage: { getItem() { return null; }, setItem() {} } };
const arg = JSON.parse(process.argv[2]).arg;
if (arg) process.argv.push(arg);
require(__PRELOAD__);
const out = { before: api.popout.speaks(), listens: typeof handlers["popout:speaker"] === "function" };
if (out.listens) {
  handlers["popout:speaker"]({}, true);
  out.told = api.popout.speaks();
  handlers["popout:speaker"]({}, "yes");
  out.junk = api.popout.speaks();
}
process.stdout.write(JSON.stringify(out) + "\\n");
"""


@requires_node
def test_the_preload_says_whether_this_pop_out_speaks(tmp_path):
    script = _PRELOAD_RUN.replace("__PRELOAD__", json.dumps(str(PRELOAD)))
    pop = _node(script, tmp_path, {"arg": "--ij-popout=/chat"}, "preload_pop")
    # Quiet until main.js says it speaks; only a real true counts.
    assert pop == {"before": False, "listens": True, "told": True, "junk": False}
    main = _node(script, tmp_path, {"arg": ""}, "preload_main")
    # The main window never listens (it always speaks while it is open).
    assert main == {"before": False, "listens": False}
