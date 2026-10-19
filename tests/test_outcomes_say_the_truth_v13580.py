"""v1.358.0 lane 8A — schedules, the bell, missions and computer use report
the REAL outcome (the post-build re-audit's regressions + the G01 partials).

What a new user meets, each pinned through the real app factory, the real
scheduler entry point (Run now), the real notifier and the real orchestrator:

* a scheduled workflow PARKED on the user's answer records ``needs_you`` (it
  read "ok" and a green "Done"), and its notice says "needs you", never
  "finished" or "done";
* a goal iteration the engine REFUSED records ``skipped`` (it read "ok");
* a scheduled run that ENDED FAILED while an ask expired records ``error`` —
  "Failed", like the run's own badge — and its reason says the ask went
  unanswered first (it read "Done · needs you" / "finished, but it needs
  you");
* a failure with no reason never sends the user to "the daemon log";
* the phone line flattens a run's model-written summary and says "could not
  finish" for a run that failed after an ask expired (the bell's words);
* a mission whose worklist kept failed items is announced as part-failed on
  the event the bell reads — never "Your objective is done".

THE CONTRACT FIXTURE — ``dashboard/__tests__/fixtures/outcomes-v13580.json``
holds what the REAL daemon wrote for those fires and that mission, plus every
terminal status the browser harness writes; the dashboard half
(``dashboard/__tests__/outcomes-say-the-truth-v13580.test.tsx``) feeds it
through the real Schedules page, Run now, the bell, the objectives rail and
the mission headline. After an intended change:
``IJ_REGEN_OUTCOMES_V13580=1 uv run pytest tests/test_outcomes_say_the_truth_v13580.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.comm.notifier import format_event
from iron_jarvis.computeruse.models import RUN_STATUSES
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import AgentState, AgentType, EventRecord, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.scheduling.models import ScheduledTaskRecord

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "outcomes-v13580.json"
REGEN = os.environ.get("IJ_REGEN_OUTCOMES_V13580") == "1"

#: The ``session.completed`` keys the bell's verdict reads (runVerdict +
#: unattendedDoor + the mission row): exactly what the daemon sent.
_BELL_KEYS = ("origin", "status", "ok", "outcome", "project_id")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}


def _hold(key: str, real) -> None:
    """Compare ``real`` with the fixture's ``key`` — or write it (REGEN)."""
    data = _fixture()
    if REGEN:
        data[key] = real
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        return
    assert key in data, f"run IJ_REGEN_OUTCOMES_V13580=1 once: the fixture has no {key!r}"
    assert real == data[key], (
        f"the daemon no longer sends what {FIXTURE.name}[{key!r}] says — if the "
        "change is intended, regenerate the fixture and update the dashboard side"
    )


def _app(tmp_path, name: str = "home"):
    app = create_app(str(tmp_path / name))
    return app, TestClient(app)


def _mock_channel(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("mock channel missing from the offline notifier")


def _schedule_messages(mock: MockChannel, before: int, name: str) -> list[str]:
    """The schedule's OWN delivery lines (other alerts are other surfaces)."""
    return [m for m in mock.sent[before:] if m.startswith(f"Schedule “{name}”")]


def _insert_goal_schedule(platform, name: str, goal_id: str) -> None:
    with session_scope(platform.engine) as db:
        db.add(
            ScheduledTaskRecord(
                name=name,
                cron="0 9 * * *",
                trigger_type="cron",
                kind="goal",
                payload_json=json.dumps({"goal_id": goal_id}),
            )
        )
        db.commit()


def _expired_ask(db, sid: str) -> None:
    db.add(
        EventRecord(
            id=new_id("evt"),
            type=EventType.APPROVAL_RESOLVED,
            session_id=sid,
            payload_json=json.dumps({"approval_id": "a1", "decision": "timeout"}),
        )
    )


def _plant_on_create(platform, origin_prefix: str, plant) -> None:
    """Write ledger rows for a session the moment it is created, so the
    finalize-time verdict reads them like any other run's."""

    def handler(ev) -> None:
        if ev.type != EventType.SESSION_CREATED or not ev.session_id:
            return
        with session_scope(platform.engine) as db:
            row = db.get(Session, ev.session_id)
            if row is None or not str(row.origin or "").startswith(origin_prefix):
                return
            plant(db, ev.session_id)
            db.commit()

    platform.event_bus.add_handler(handler)


def _fake_run(state: AgentState, result: str):
    """A stand-in for the AGENT's work (not the code under test)."""

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


def _capture_completed(platform) -> list[dict]:
    seen: list[dict] = []

    def handler(ev) -> None:
        if ev.type == EventType.SESSION_COMPLETED:
            seen.append({"session_id": ev.session_id, "payload": dict(ev.payload or {})})

    platform.event_bus.add_handler(handler)
    return seen


# --------------------------------------------------------------------------- #
# (1) the Schedules row and its notice LEAD with the outcome
# --------------------------------------------------------------------------- #


def _fire_parked_workflow(tmp_path) -> dict:
    app, client = _app(tmp_path, "parked")
    r = client.post(
        "/workflows",
        json={"name": "gated", "steps": [{"name": "Approve", "kind": "ask", "message": "Send it?"}]},
    )
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "fire-gated", "cron": "0 8 * * *", "kind": "workflow", "payload": {"workflow": "gated"}},
    )
    assert r.status_code == 200, r.text
    mock = _mock_channel(app)
    before = len(mock.sent)
    body = client.post("/schedules/fire-gated/run").json()
    sent = _schedule_messages(mock, before, "fire-gated")
    short = client.get("/ui/status-line").json()["short_schedules"]
    return {"body": body, "sent": sent, "short": short}


def test_a_scheduled_workflow_parked_on_the_user_reads_needs_you_never_done(tmp_path):
    out = _fire_parked_workflow(tmp_path)
    body = out["body"]
    assert body["last_status"] == "needs_you", body
    assert body["last_detail"] == ro.workflow_waiting_detail("gated"), body
    # It is WAITING right now, not an ask that expired: never that lead.
    assert not body["last_detail"].startswith(ro.NEEDS_YOU_LEAD), body
    # The notice says the row's own word — never "finished", never "done".
    assert out["sent"], "the parked run is still announced"
    assert all(": needs you — " in m for m in out["sent"]), out["sent"]
    assert not any("finished" in m or ": done" in m for m in out["sent"]), out["sent"]
    # The home line counts it as a schedule that needs a look.
    assert out["short"] == 1, out


def test_a_refused_goal_iteration_reads_skipped_never_ok(tmp_path):
    app, client = _app(tmp_path)
    _insert_goal_schedule(app.state.platform, "goal-gone", "goal_gone")
    mock = _mock_channel(app)
    before = len(mock.sent)

    body = client.post("/schedules/goal-gone/run").json()

    assert body["last_status"] == "skipped", body
    assert body["last_detail"] == ro.goal_skipped_detail("unknown goal 'goal_gone'"), body
    sent = _schedule_messages(mock, before, "goal-gone")
    assert sent and all(": skipped — " in m for m in sent), sent
    assert not any(": done" in m for m in sent), sent
    # A skip is not a short run: the home line does not count it.
    assert client.get("/ui/status-line").json()["short_schedules"] == 0


def _fire_failed_after_ask(tmp_path, monkeypatch) -> dict:
    app, client = _app(tmp_path, "failed-asked")
    platform = app.state.platform
    _plant_on_create(platform, "schedule:", _expired_ask)
    monkeypatch.setattr(
        platform.orchestrator.runtime,
        "run",
        _fake_run(AgentState.FAILED, "Stopped at the folder it may not open."),
    )
    completed = _capture_completed(platform)
    r = client.post(
        "/schedules",
        json={"name": "tidy-scans", "cron": "0 17 * * 5", "kind": "task", "payload": {"task": "Tidy the scans."}},
    )
    assert r.status_code == 200, r.text
    mock = _mock_channel(app)
    before = len(mock.sent)
    body = client.post("/schedules/tidy-scans/run").json()
    sid = body["last_session_id"]
    mine = [c["payload"] for c in completed if c["session_id"] == sid]
    assert len(mine) == 1, completed
    return {
        "body": body,
        "sent": _schedule_messages(mock, before, "tidy-scans"),
        "completion": {k: mine[0][k] for k in _BELL_KEYS if k in mine[0]},
        "outcome": platform.orchestrator.get_session(sid).outcome,
    }


def test_a_failed_run_whose_ask_expired_reads_failed_like_its_own_badge(tmp_path, monkeypatch):
    out = _fire_failed_after_ask(tmp_path, monkeypatch)
    body = out["body"]
    assert body["last_status"] == "error", body
    assert body["last_detail"] == (
        "The run failed: " + ro.ASK_EXPIRED_REASON + " Stopped at the folder it may not open."
    ), body
    # The ledger's own verdict is unchanged; only the row's word follows the run.
    assert out["outcome"] == "needs_you"
    assert out["completion"]["status"] == "failed"
    assert out["sent"] and all(": FAILED — " in m for m in out["sent"]), out["sent"]
    assert not any("needs you" in m or "finished" in m for m in out["sent"]), out["sent"]


def test_a_failure_with_no_reason_never_points_at_the_daemon_log(tmp_path):
    app, client = _app(tmp_path)
    platform = app.state.platform

    async def no_reason(goal_id):  # the goal engine raising with no words
        raise RuntimeError("")

    platform.goal_engine = SimpleNamespace(run_iteration=no_reason)
    _insert_goal_schedule(platform, "goal-silent", "goal_x")
    mock = _mock_channel(app)
    before = len(mock.sent)

    body = client.post("/schedules/goal-silent/run").json()

    assert body["last_status"] == "error", body
    detail = body["last_detail"]
    assert "daemon" not in detail.lower(), detail
    assert detail == ro.failure_detail(""), detail
    assert "Iron Jarvis kept the details in its log" in detail, detail
    sent = _schedule_messages(mock, before, "goal-silent")
    assert sent and not any("daemon" in m.lower() for m in sent), sent


# --------------------------------------------------------------------------- #
# (2)-(4) the phone line: the bell's verdict, a flattened summary
# --------------------------------------------------------------------------- #


def _line(payload: dict) -> str:
    return format_event({"type": EventType.SESSION_COMPLETED, "payload": payload, "session_id": "session_1"})


def test_the_phone_says_could_not_finish_for_a_run_that_failed_after_an_ask():
    failed = _line({"status": "failed", "summary": "", "origin": "schedule:tidy-scans", "outcome": "needs_you"})
    assert failed.startswith("Your schedule tidy-scans could not finish"), failed
    # The control: a run that FINISHED with the user as the next step.
    finished = _line({"status": "completed", "summary": "", "origin": "schedule:tidy-scans", "outcome": "needs_you"})
    assert finished.startswith("Your schedule tidy-scans needs you"), finished


def test_the_phone_line_flattens_a_run_summary():
    md = "# Final Report\n## What happened\nI reviewed **12** conversations and added `3` notes."
    line = _line({"status": "completed", "summary": md, "origin": "schedule:weekly", "outcome": "completed"})
    assert line == "Your schedule weekly finished — I reviewed 12 conversations and added 3 notes.", line
    mission = _line({"status": "completed", "summary": md, "origin": "job:mission", "outcome": "completed"})
    assert "#" not in mission and "**" not in mission and "`" not in mission, mission


# --------------------------------------------------------------------------- #
# the contract fixture the dashboard half reads
# --------------------------------------------------------------------------- #


def test_the_real_schedule_rows_reach_the_fixture(tmp_path, monkeypatch):
    parked = _fire_parked_workflow(tmp_path)
    failed = _fire_failed_after_ask(tmp_path, monkeypatch)

    app, client = _app(tmp_path, "skipped")
    _insert_goal_schedule(app.state.platform, "goal-gone", "goal_gone")
    mock = _mock_channel(app)
    before = len(mock.sent)
    skipped = client.post("/schedules/goal-gone/run").json()
    skipped_sent = _schedule_messages(mock, before, "goal-gone")

    rows = [
        {
            "case": "workflow-parked",
            "name": "fire-gated",
            "last_status": parked["body"]["last_status"],
            "last_detail": parked["body"]["last_detail"],
            "notice": parked["sent"][0],
        },
        {
            "case": "goal-skipped",
            "name": "goal-gone",
            "last_status": skipped["last_status"],
            "last_detail": skipped["last_detail"],
            "notice": skipped_sent[0],
        },
        {
            "case": "failed-asked",
            "name": "tidy-scans",
            "last_status": failed["body"]["last_status"],
            "last_detail": failed["body"]["last_detail"],
            "notice": failed["sent"][0],
            "completion": failed["completion"],
        },
    ]
    assert [(r["case"], r["last_status"]) for r in rows] == [
        ("workflow-parked", "needs_you"),
        ("goal-skipped", "skipped"),
        ("failed-asked", "error"),
    ]
    _hold("schedule_rows", rows)


async def test_a_part_failed_mission_reaches_the_bell_as_part_failed(tmp_path, monkeypatch):
    """The event the bell reads for a mission whose worklist kept a failed
    item carries ``completed_with_failures`` — the verdict the mission screen
    shows ("Finished, but part of it failed") — never ``completed`` (which the
    bell words "Your objective is done")."""
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    orch = platform.orchestrator
    monkeypatch.setattr(orch.runtime, "run", _fake_run(AgentState.COMPLETED, "# Report\nAll done."))
    store = platform.worklist
    completed = _capture_completed(platform)

    session = await orch.create_session("rename the scans", AgentType.SUPERVISOR, origin="job:mission")
    board = store.root_session_for(session.id)
    store.add(board, [("a.pdf", "a"), ("b.pdf", "b")])
    store.finish(board, "a.pdf", status="done")
    store.finish(board, "b.pdf", status="failed", note="locked")
    done = await orch.run_session(session.id)
    assert done.status.value == "completed"
    for _ in range(50):
        if any(c["session_id"] == session.id for c in completed):
            break
        await asyncio.sleep(0.02)
    mine = [c["payload"] for c in completed if c["session_id"] == session.id]
    assert len(mine) == 1, completed
    payload = {k: mine[0][k] for k in _BELL_KEYS if k in mine[0]}
    assert payload["origin"] == "job:mission"
    assert payload["outcome"] == "completed_with_failures", payload
    _hold("mission_part_failed", payload)


def test_every_browser_status_the_harness_writes_is_in_the_fixture():
    """The bell's browser words (lib/browserRunWords) cover every TERMINAL
    status the harness ends a run with — read from the harness's own list."""
    terminal = [s for s in RUN_STATUSES if s != "running"]
    assert "awaiting_approval" in terminal and "blocked" in terminal
    _hold("browser_statuses", terminal)


def test_the_fixture_is_committed():
    data = _fixture()
    assert {"schedule_rows", "mission_part_failed", "browser_statuses"} <= set(data), sorted(data)


# --------------------------------------------------------------------------- #
# the vocabulary itself
# --------------------------------------------------------------------------- #


def test_the_vocabulary_leads_with_the_outcome():
    assert ro.fire_status("needs_you") == ro.STATUS_NEEDS_YOU
    assert ro.fire_status("skipped") == ro.STATUS_SKIPPED
    assert ro.fire_status("completed") == ro.STATUS_OK
    assert ro.delivery_head(ro.STATUS_NEEDS_YOU) == "needs you"
    assert "finished" not in ro.delivery_head(ro.STATUS_NEEDS_YOU)
    assert ro.delivery_head(ro.STATUS_SKIPPED) == "skipped"
    assert "daemon" not in ro.failure_detail("").lower()
    assert ro.failed_reason("Stopped.", "failed", asked=True).startswith(ro.ASK_EXPIRED_REASON)
    assert ro.failed_reason("", "failed") == "the run ended failed"
