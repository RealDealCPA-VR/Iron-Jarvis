"""v1.360.0 lane 10A — ONE notice per run outcome (the final re-audit's G03
regression), in the bell's words, window open or not.

THE REGRESSION (v1.351.0): one failed or needs-you SCHEDULED run raised two
(often three) desktop notifications while a window was open, in different
words — the bell's own ping ("Your schedule X could not finish"), the
schedule's delivery to "This PC" ("Schedule “X”: FAILED — …") and the
notifier's generic run alert to "This PC" — and a failed workflow rang a step
row AND a workflow row. With the app closed to the tray NOTHING was said at
all (the renderer, and so the bell and the "This PC" bridge, is destroyed).

WHO OWNS THE NOTICE NOW:

* a window is open — the bell (its ping rule is `notifier.bell_pings`);
* the app sits in the tray — desktop/main.js's run-outcome watcher, which
  says the bell's attention rows in the bell's words;
* "This PC" stays quiet for exactly those outcomes (the notifier's event
  alert and the schedule's own delivery), and for a workflow step (its
  workflow's own notice says it);
* a run a schedule started is announced by the schedule's own delivery on
  every destination — never a second time by the generic run alert.

Pinned through the REAL app factory, the real scheduler entry point (Run now),
the real notifier and the real main.js functions under node, all against ONE
fixture the dashboard's bell reads too:
``dashboard/__tests__/fixtures/one-notice-v13600.json``.
"""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import (
    Notifier,
    bell_attention,
    bell_pings,
    in_schedule_fire,
    schedule_fire,
)
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentState
from iron_jarvis.daemon.app import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "one-notice-v13600.json"
MAIN = ROOT / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _case(name: str) -> dict:
    return next(c for c in _cases() if c["case"] == name)


# --------------------------------------------------------------------------- #
# (a) the daemon's rule IS the bell's rule (one fixture)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["case"])
def test_the_daemon_reads_each_outcome_exactly_as_the_bell_rings_it(case):
    ev = case["event"]
    assert bell_pings(ev) is case["pings"], case["case"]
    assert bell_attention(ev) is case["attention"], case["case"]


def test_a_completion_with_no_session_has_no_bell_row():
    ev = dict(_case("schedule-failed")["event"], session_id=None)
    assert bell_pings(ev) is False


# --------------------------------------------------------------------------- #
# (b) the notifier: "This PC" stays quiet for what the bell rings
# --------------------------------------------------------------------------- #


def _notifier(sink: list[str]) -> tuple[Notifier, MockChannel]:
    n = Notifier()
    phone = MockChannel()
    n.add_channel("phone", phone)
    desk = DesktopChannel({"type": "desktop"})
    n.add_channel("this-pc", desk)
    return n, phone


@pytest.fixture
def desktop_sink(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: sent.append(message))
    return sent


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["case"])
def test_this_pc_is_quiet_for_exactly_what_the_bell_pings(case, desktop_sink):
    n, phone = _notifier(desktop_sink)
    n.on_event(case["event"])
    step = (
        case["event"]["type"] == EventType.SESSION_COMPLETED
        and str((case["event"]["payload"] or {}).get("origin") or "").startswith("workflow:")
    )
    if case["pings"] or step:
        assert desktop_sink == [], f"{case['case']}: This PC repeated the bell"
    else:
        assert len(desktop_sink) == 1, f"{case['case']}: This PC lost its one notice"
    # Every other destination is unchanged: the phone still hears it.
    # v1.374.0 (moved on purpose): a case marked `phone: false` is not a
    # default alert (a long Build turn's notice is desktop only).
    if case.get("phone", True):
        assert len(phone.sent) == 1, case["case"]
    else:
        assert phone.sent == [], case["case"]


def test_inside_a_schedule_fire_no_destination_gets_the_generic_run_alert(desktop_sink):
    n, phone = _notifier(desktop_sink)
    with schedule_fire("morning-brief"):
        n.on_event(_case("schedule-done")["event"])
        n.on_event(_case("workflow-done")["event"])
        # An ask is not an outcome: it still reaches the phone inside a fire.
        n.on_event({"type": EventType.APPROVAL_REQUESTED, "payload": {"tool": "shell"}})
    assert len(phone.sent) == 1 and "asking to use shell" in phone.sent[0], phone.sent
    # v1.360.0 integrate (moved on purpose, lane 10A note g): on THIS PC the
    # bell's "N pending" ping (window open) and the desktop app's ask watcher
    # (tray) say every agent ask, so "This PC" no longer adds a second toast.
    assert desktop_sink == [], desktop_sink
    # After the fire, the same alert is sent again as usual.
    n.on_event(_case("schedule-done")["event"])
    assert len(phone.sent) == 2


def test_work_a_fire_started_that_outlives_it_is_announced_as_usual():
    with schedule_fire("x"):
        assert in_schedule_fire()
        ctx = contextvars.copy_context()  # what a background task would hold
    assert ctx.run(in_schedule_fire) is False
    assert in_schedule_fire() is False


# --------------------------------------------------------------------------- #
# (c) the real app: one scheduled run, one notice per destination
# --------------------------------------------------------------------------- #


def _fake_run(state: AgentState, result: str):
    async def run(session, agent_def, *a, **kw):
        return SimpleNamespace(
            state=state,
            provider=session.provider,
            model=session.model,
            result=result,
            input_tokens=0,
            output_tokens=0,
        )

    return run


def _app(tmp_path):
    app = create_app(str(tmp_path / "home"))
    return app, TestClient(app)


def _mock(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("no mock channel")


def _completed(platform) -> list[dict]:
    seen: list[dict] = []

    def handler(ev) -> None:
        if ev.type == EventType.SESSION_COMPLETED:
            seen.append({"type": ev.type, "session_id": ev.session_id, "payload": dict(ev.payload or {})})

    platform.event_bus.add_handler(handler)
    return seen


def _fire(tmp_path, monkeypatch, state: AgentState, result: str, payload: dict | None = None):
    app, client = _app(tmp_path)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_run(state, result))
    completed = _completed(platform)
    r = client.post(
        "/schedules",
        json={
            "name": "morning-brief",
            "cron": "0 7 * * *",
            "kind": "task",
            "payload": {"task": "Write the morning brief.", **(payload or {})},
        },
    )
    assert r.status_code == 200, r.text
    mock = _mock(app)
    before = len(mock.sent)
    body = client.post("/schedules/morning-brief/run").json()
    return SimpleNamespace(body=body, phone=mock.sent[before:], completed=completed)


def test_a_failed_scheduled_run_is_one_notice_on_this_pc_and_one_on_the_phone(
    tmp_path, monkeypatch, desktop_sink
):
    out = _fire(tmp_path, monkeypatch, AgentState.FAILED, "Stopped at the inbox it could not open.")
    assert out.body["last_status"] == "error", out.body
    # This PC: NOTHING from the daemon — the bell (window open) or the tray
    # watcher (no window) is the one desktop notice, in the bell's words.
    assert desktop_sink == [], desktop_sink
    [ev] = [c for c in out.completed if c["session_id"] == out.body["last_session_id"]]
    assert bell_pings(ev) and bell_attention(ev), ev
    # The phone: exactly ONE line, the schedule's own — never the generic
    # "Your schedule … could not finish" as a second message.
    assert len(out.phone) == 1, out.phone
    assert out.phone[0].startswith("Schedule “morning-brief”: FAILED — "), out.phone


def _expired_ask_on_create(platform) -> None:
    """The run's ask expired unanswered (the ledger's ``needs_you``): an
    APPROVAL_RESOLVED timeout row written the moment the session exists."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import new_id
    from iron_jarvis.core.models import EventRecord, Session

    def handler(ev) -> None:
        if ev.type != EventType.SESSION_CREATED or not ev.session_id:
            return
        with session_scope(platform.engine) as db:
            row = db.get(Session, ev.session_id)
            if row is None or not str(row.origin or "").startswith("schedule:"):
                return
            db.add(
                EventRecord(
                    id=new_id("evt"),
                    type=EventType.APPROVAL_RESOLVED,
                    session_id=ev.session_id,
                    payload_json=json.dumps({"approval_id": "a1", "decision": "timeout"}),
                )
            )
            db.commit()

    platform.event_bus.add_handler(handler)


def test_a_needs_you_scheduled_run_is_one_notice_too(tmp_path, monkeypatch, desktop_sink):
    app, client = _app(tmp_path)
    platform = app.state.platform
    _expired_ask_on_create(platform)
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_run(AgentState.COMPLETED, "Drafted it."))
    completed = _completed(platform)
    client.post(
        "/schedules",
        json={"name": "tidy", "cron": "0 7 * * *", "kind": "task", "payload": {"task": "Tidy the scans."}},
    )
    mock = _mock(app)
    before = len(mock.sent)
    body = client.post("/schedules/tidy/run").json()
    assert body["last_status"] == "needs_you", body
    [ev] = [c for c in completed if c["session_id"] == body["last_session_id"]]
    assert bell_pings(ev), ev  # the bell's "Your schedule tidy needs you"
    assert desktop_sink == [], desktop_sink
    phone = mock.sent[before:]
    assert len(phone) == 1 and phone[0].startswith("Schedule “tidy”: needs you — "), phone


def test_a_clean_scheduled_run_is_one_notice_on_this_pc(tmp_path, monkeypatch, desktop_sink):
    out = _fire(tmp_path, monkeypatch, AgentState.COMPLETED, "Drafted the brief.")
    # A clean run: the bell's row is quiet, so "This PC" says it — once (the
    # generic "Your schedule … finished" used to be a second toast).
    assert out.body["last_status"] == "ok", out.body
    assert len(desktop_sink) == 1 and desktop_sink[0].startswith("Schedule “morning-brief”: done"), desktop_sink
    assert len(out.phone) == 1, out.phone


def test_a_fire_that_failed_before_any_run_still_reaches_this_pc(tmp_path, desktop_sink):
    # No session and no workflow run: the bell has no row for it, so "This
    # PC" keeps the schedule's own line (the one notice).
    app, client = _app(tmp_path)
    client.post(
        "/schedules",
        json={"name": "broken", "cron": "0 3 * * *", "kind": "workflow", "payload": {"workflow": "nope"}},
    )
    body = client.post("/schedules/broken/run").json()
    assert body["last_status"] == "error", body
    assert len(desktop_sink) == 1 and desktop_sink[0].startswith("Schedule “broken”: FAILED"), desktop_sink


def test_notify_false_silences_the_runs_generic_alert_too(tmp_path, monkeypatch, desktop_sink):
    out = _fire(
        tmp_path, monkeypatch, AgentState.COMPLETED, "Drafted the brief.", payload={"notify": False}
    )
    assert out.body["last_status"] == "ok", out.body
    assert out.phone == [], out.phone
    assert desktop_sink == [], desktop_sink


# (lane 10A review) The row's status is not the bell's rule: a GOAL schedule
# whose iteration the engine counts as done read "ok" on the row, while its
# session ended with an expired ask — the bell rings "Your goal's run needs
# you", and "This PC" used to add "Schedule “g”: done — …" beside it.
# (v1.360.0 integrate) The row now reads the session's verdict too.


def _goal_fire(tmp_path, monkeypatch, *, ask_expired: bool):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import new_id
    from iron_jarvis.core.models import EventRecord, Session
    from iron_jarvis.scheduling.models import ScheduledTaskRecord

    app, client = _app(tmp_path)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_run(AgentState.COMPLETED, "Did part."))

    def expire(ev) -> None:
        if not ask_expired or ev.type != EventType.SESSION_CREATED or not ev.session_id:
            return
        with session_scope(platform.engine) as db:
            row = db.get(Session, ev.session_id)
            if row is None or not str(row.origin or "").startswith("goal:"):
                return
            db.add(
                EventRecord(
                    id=new_id("evt"),
                    type=EventType.APPROVAL_RESOLVED,
                    session_id=ev.session_id,
                    payload_json=json.dumps({"approval_id": "a1", "decision": "timeout"}),
                )
            )
            db.commit()

    platform.event_bus.add_handler(expire)
    completed = _completed(platform)
    r = client.post(
        "/goals",
        json={
            "name": "inbox zero",
            "contract_text": "Create a file summarizing the task.",
            "budget": {"max_tokens": 1_000_000},
        },
    )
    assert r.status_code == 200, r.text
    with session_scope(platform.engine) as db:
        db.add(
            ScheduledTaskRecord(
                name="g",
                cron="0 9 * * *",
                trigger_type="cron",
                kind="goal",
                payload_json=json.dumps({"goal_id": r.json()["goal"]["id"]}),
            )
        )
        db.commit()
    mock = _mock(app)
    before = len(mock.sent)
    body = client.post("/schedules/g/run").json()
    [ev] = [c for c in completed if c["session_id"] == body["last_session_id"]]
    return SimpleNamespace(body=body, ev=ev, phone=mock.sent[before:])


def test_a_goal_schedule_whose_run_needs_you_is_the_bells_one_notice(
    tmp_path, monkeypatch, desktop_sink
):
    out = _goal_fire(tmp_path, monkeypatch, ask_expired=True)
    # v1.360.0 integrate (moved on purpose, lane 10A note a): the engine
    # counted the iteration, but its session ended needing the user — the
    # ROW now says so, in the bell's word ("ok" here before).
    assert out.body["last_status"] == "needs_you", out.body
    assert bell_pings(out.ev), out.ev  # … and the bell rings "needs you"
    assert desktop_sink == [], desktop_sink  # so "This PC" says nothing beside it
    assert len(out.phone) == 1 and out.phone[0].startswith("Schedule “g”: needs you — "), out.phone


def test_a_clean_goal_schedule_keeps_its_one_this_pc_notice(tmp_path, monkeypatch, desktop_sink):
    out = _goal_fire(tmp_path, monkeypatch, ask_expired=False)
    assert out.body["last_status"] == "ok", out.body  # (integrate) a clean iteration stays ok
    assert not bell_pings(out.ev), out.ev  # a clean run is a quiet bell row
    assert len(desktop_sink) == 1 and desktop_sink[0].startswith("Schedule “g”: done"), desktop_sink


def test_a_task_run_with_no_verdict_is_the_bells_one_notice(tmp_path, monkeypatch, desktop_sink):
    # A ledger read that gave no verdict: the row reads "ok", the bell rings
    # "Your schedule … finished. Check what it did".
    import iron_jarvis.agents.outcome as outcome_mod

    monkeypatch.setattr(outcome_mod, "derive_outcome", lambda *a, **k: None)
    out = _fire(tmp_path, monkeypatch, AgentState.COMPLETED, "Drafted the brief.")
    assert out.body["last_status"] == "ok", out.body
    [ev] = [c for c in out.completed if c["session_id"] == out.body["last_session_id"]]
    assert bell_pings(ev), ev
    assert desktop_sink == [], desktop_sink
    assert len(out.phone) == 1, out.phone


# --------------------------------------------------------------------------- #
# (d) the tray: desktop/main.js says the bell's attention rows, once
# --------------------------------------------------------------------------- #


def _lift() -> str:
    src = MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = src.index("const RUN_OUTCOME_AUDIT_TYPES")
    tail = src.index("function runOutcomePath", start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _node(script: str, tmp_path: Path, payload: dict) -> dict:
    f = tmp_path / "run_outcome.js"
    f.write_text(_lift() + "\n" + script, encoding="utf-8")
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


_NOTICES = """
const input = JSON.parse(process.argv[2]);
const out = input.cases.map((c) => runOutcomeNotice(c.event));
process.stdout.write(JSON.stringify({ out }) + "\\n");
"""

_PLAN = """
const input = JSON.parse(process.argv[2]);
let state = { seen: {}, announced: {} };
const out = [];
for (const step of input.steps) {
  const plan = planRunOutcomeNotifications(state, step.entries, !!step.windowOpen, step.windowSeenAt || 0);
  state = plan.state;
  out.push(plan.notify.map((n) => n.title));
}
process.stdout.write(JSON.stringify({ out, since: runOutcomeNewestTs(input.steps[0].entries) }) + "\\n");
"""


@requires_node
def test_the_tray_notice_is_the_bells_attention_row_word_for_word(tmp_path):
    cases = _cases()
    out = _node(_NOTICES, tmp_path, {"cases": cases})["out"]
    for case, notice in zip(cases, out):
        if case["attention"]:
            assert notice is not None, case["case"]
            assert notice["title"] == case["title"], case["case"]
            # v1.360.0 integrate (moved on purpose, lane 10A note f): the
            # tray says what the bell's PING says — the row's body, else the
            # ping's own "Open the dashboard for details." (never a bare title).
            assert notice["body"] == (case["body"] or "Open the dashboard for details."), case["case"]
            assert notice["href"] == case["href"], case["case"]
        else:
            assert notice is None, f"{case['case']}: the tray must not say what the bell does not ring"


def _entries(*names: str) -> list[dict]:
    """Audit entries, NEWEST FIRST, as GET /audit answers."""
    return [_case(n)["event"] for n in reversed(names)]


@requires_node
def test_a_failed_run_in_the_tray_raises_one_notice_and_never_twice(tmp_path):
    steps = [
        {"entries": _entries("schedule-failed", "schedule-done", "chat-run-failed")},
        # The next tick reads the same entries again (``since`` is inclusive).
        {"entries": _entries("schedule-failed", "schedule-done", "chat-run-failed")},
    ]
    res = _node(_PLAN, tmp_path, {"steps": steps})
    assert res["out"] == [["Your schedule morning-brief could not finish"], []]
    # The next read starts at the newest entry the daemon answered.
    assert res["since"] == _case("chat-run-failed")["event"]["ts"]


@requires_node
def test_with_a_window_open_the_bell_owns_it_and_a_later_close_does_not_repeat_it(tmp_path):
    steps = [
        {"entries": _entries("goal-failed"), "windowOpen": True},
        {"entries": _entries("goal-failed", "remote-task-failed")},
    ]
    res = _node(_PLAN, tmp_path, {"steps": steps})
    assert res["out"] == [[], ["A task from desk could not finish"]]


@requires_node
def test_a_run_that_ended_before_the_window_closed_is_the_bells_not_the_trays(tmp_path):
    # It ended at 03:05; the window was open until 03:05:30 (the bell pinged
    # it); the first tray read sees it — and says nothing.
    seen_at_ms = 1792379130000  # 2026-10-19T03:05:30Z
    steps = [{"entries": _entries("goal-failed", "remote-task-failed"), "windowSeenAt": seen_at_ms}]
    res = _node(_PLAN, tmp_path, {"steps": steps})
    assert res["out"] == [["A task from desk could not finish"]]


@requires_node
def test_a_failed_workflow_in_the_tray_is_one_notice(tmp_path):
    # Same read: the workflow's notice stands in for its step's.
    res = _node(_PLAN, tmp_path, {"steps": [{"entries": _entries("workflow-step-failed", "workflow-failed")}]})
    assert res["out"] == [["Your workflow Weekly report could not finish"]]
    # Across reads: the step's notice already said it.
    res = _node(
        _PLAN,
        tmp_path,
        {
            "steps": [
                {"entries": _entries("workflow-step-failed")},
                {"entries": _entries("workflow-step-failed", "workflow-failed")},
            ]
        },
    )
    assert res["out"] == [["A step of your workflow Weekly report could not finish"], []]


def test_the_watcher_reads_the_bells_events_and_the_notice_opens_the_run():
    src = MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    tick = src[src.index("function installAskWatcher") : src.index("function createTray")]
    assert "runOutcomePath(runOutcomeSince)" in tick
    assert "planRunOutcomeNotifications(" in tick
    # The click opens where the bell row opens.
    show = src[src.index("function showAskNotification") : src.index("function installAskWatcher")]
    assert "item.href" in show and "mainWin.loadURL(`${DASHBOARD_URL}${item.href}`)" in show
    # Closing to the tray marks the moment the bell stopped speaking.
    hide = src[src.index("function hideToTray") : src.index("// --- Start at login")]
    assert "runOutcomeWindowSeenAt = Date.now()" in hide
    # The audit types are exactly the bell's run-outcome types — v1.374.0
    # (moved on purpose): and a long Build turn that ended.
    assert (
        'const RUN_OUTCOME_AUDIT_TYPES = "session.completed,workflow.completed,build.turn_finished";' in src
    )
