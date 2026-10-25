"""v1.351.0 lane 1A — schedules tell the truth; Emergency stop stops.

Readiness audit gaps:

* G01 — a scheduled run records its REAL outcome. A run whose ask expired
  (``Session.outcome == "needs_you"``) used to read a green "✓ ok" and be
  announced "done"; a step that failed (``completed_with_failures``) likewise;
  a failed run read ``RuntimeError: session ended failed: …``; a good one read
  the report's raw markdown. Now: ``needs_you`` / ``partial`` rows, "finished,
  but it needs you" / "finished, but part of it failed" notifications, "The
  run failed: <reason>" with no class name, and a plain-sentence detail.
  Queued assignments carry the verdict on ``assignment.finished``.
* G02 — the Status map's Schedules star counted ``last_status == "failed"``,
  a value nothing writes; it now counts ``error`` (and the short ones), and
  ``/ui/status-line`` carries ``short_schedules`` for the home line.
* G13 — Emergency stop was read by the motivation loop alone. Now every
  unattended door is refused at ``create_session``, a due schedule records a
  skip, engaging the switch (any door) cancels the unattended runs already
  going, and chat still answers.

Everything goes through the REAL app factory / platform, the real scheduler
entrypoints (Run now and APScheduler's ``_fire``) and the real ledger; the
only doubles stand in for the AGENT's work (``runtime.run``) where a test
needs a run that fails, waits, or writes a markdown report.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_REFUSAL,
    EMERGENCY_STOP_STOPPED,
    EmergencyStopOn,
    Orchestrator,
)
from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import (
    AgentState,
    EventRecord,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.scheduling.models import ScheduledTaskRecord


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _app(tmp_path):
    app = create_app(str(tmp_path))
    return app, TestClient(app)


def _mock_channel(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("mock channel missing from offline notifier")


def _schedule_messages(mock: MockChannel, before: int, name: str) -> list[str]:
    """The schedule's OWN delivery lines (the notifier also forwards a
    generic ``session.completed`` line, which is another surface's words)."""
    return [m for m in mock.sent[before:] if m.startswith(f"Schedule “{name}”")]


def _add_task(client, name: str, task: str = "Write the Friday digest.") -> None:
    r = client.post(
        "/schedules",
        json={"name": name, "cron": "0 17 * * 5", "kind": "task", "payload": {"task": task}},
    )
    assert r.status_code == 200, r.text


def _plant_on_create(platform, origin_prefix: str, plant) -> None:
    """Write ledger rows for a session the moment it is created — BEFORE it
    runs — through a real bus handler, so the finalize-time verdict
    (``agents/outcome.derive_outcome``) reads them like any other run's."""

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


def _expired_ask(db, sid: str) -> None:
    db.add(
        EventRecord(
            id=new_id("evt"),
            type=EventType.APPROVAL_RESOLVED,
            session_id=sid,
            payload_json=json.dumps({"approval_id": "a1", "decision": "timeout"}),
        )
    )


def _failed_write(db, sid: str) -> None:
    db.add(
        ToolInvocation(
            session_id=sid,
            agent_run_id="run-x",
            tool="write_file",
            ok=False,
            output="permission denied",
            reversibility="reversible",
        )
    )


def _fake_run(state: AgentState, result: str):
    """A stand-in for the AGENT's work (not the scheduler under test)."""

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


# --------------------------------------------------------------------------- #
# G01 — the real outcome, on the row and in the notification
# --------------------------------------------------------------------------- #


def test_a_run_whose_ask_expired_reads_needs_you_never_ok_or_done(tmp_path):
    app, client = _app(tmp_path)
    platform = app.state.platform
    _plant_on_create(platform, "schedule:", _expired_ask)
    _add_task(client, "weekly-review")
    mock = _mock_channel(app)
    before = len(mock.sent)

    body = client.post("/schedules/weekly-review/run").json()

    assert body["last_status"] == "needs_you", body
    assert body["last_detail"].startswith(ro.NEEDS_YOU_LEAD), body
    assert body["last_session_id"], "the row links the session to open"
    session = platform.orchestrator.get_session(body["last_session_id"])
    assert session.outcome == "needs_you", "the verdict came from the real ledger"
    fresh = _schedule_messages(mock, before, "weekly-review")
    assert fresh, "the result is still delivered"
    # v1.358.0 (lane 8A, on purpose): the notice says the row's own "needs
    # you" — never "finished" (a workflow parked on the user has not).
    assert all(": needs you" in m for m in fresh), fresh
    assert not any(": done" in m for m in fresh), fresh


def test_a_run_with_a_failed_step_reads_part_failed(tmp_path):
    app, client = _app(tmp_path)
    _plant_on_create(app.state.platform, "schedule:", _failed_write)
    _add_task(client, "renamer")
    mock = _mock_channel(app)
    before = len(mock.sent)

    body = client.post("/schedules/renamer/run").json()

    assert body["last_status"] == "partial", body
    assert body["last_detail"].startswith(ro.PARTIAL_LEAD), body
    sent = _schedule_messages(mock, before, "renamer")
    assert sent and all("finished, but part of it failed" in m for m in sent), sent


def test_a_clean_run_is_still_ok_and_done(tmp_path):
    """The control: nothing planted, the mock completes — unchanged words."""
    app, client = _app(tmp_path)
    _add_task(client, "digest")
    mock = _mock_channel(app)
    before = len(mock.sent)
    body = client.post("/schedules/digest/run").json()
    assert body["last_status"] == "ok", body
    sent = _schedule_messages(mock, before, "digest")
    assert sent and all(": done" in m for m in sent), sent


def test_a_failed_run_reads_the_run_failed_with_no_class_name(tmp_path, monkeypatch):
    app, client = _app(tmp_path)
    orch = app.state.platform.orchestrator

    async def boom(session, agent_def, *a, **kw):
        raise RuntimeError("the model endpoint at the office is not answering")

    monkeypatch.setattr(orch.runtime, "run", boom)
    _add_task(client, "nightly")
    mock = _mock_channel(app)
    before = len(mock.sent)

    body = client.post("/schedules/nightly/run").json()

    assert body["last_status"] == "error", body
    # Readiness 12A (G28), moved on purpose: never the raw text on the row —
    # what failed and what to do next (the raw text is the run's Details).
    assert body["last_detail"] == ro.failure_detail(
        "The run failed: Iron Jarvis couldn't finish this task. Run it again. If it happens "
        "again, open the run to see the details."
    ), body
    assert "office is not answering" not in body["last_detail"]
    assert "RuntimeError" not in body["last_detail"]
    sent = _schedule_messages(mock, before, "nightly")
    assert sent and all("FAILED" in m and "RuntimeError" not in m for m in sent), sent


def test_a_run_that_ends_failed_without_raising_says_why_in_plain_words(tmp_path, monkeypatch):
    """The old shape: ``RuntimeError: session ended failed: # Report …``."""
    app, client = _app(tmp_path)
    orch = app.state.platform.orchestrator
    monkeypatch.setattr(
        orch.runtime,
        "run",
        _fake_run(AgentState.FAILED, "# Report\n\nCould not reach the **file server**."),
    )
    _add_task(client, "backup-check")

    body = client.post("/schedules/backup-check/run").json()

    assert body["last_status"] == "error", body
    assert body["last_detail"] == "The run failed: Could not reach the file server.", body
    for leak in ("RuntimeError", "session ended", "#", "**"):
        assert leak not in body["last_detail"], (leak, body)


def test_the_row_detail_is_a_plain_sentence_not_the_report(tmp_path, monkeypatch):
    app, client = _app(tmp_path)
    orch = app.state.platform.orchestrator
    report = (
        "# Final Report\n## What happened\n"
        "I reviewed **12** conversations and added 3 notes to [memory](x).\n\n"
        "- note one\n- note two\n"
    )
    monkeypatch.setattr(orch.runtime, "run", _fake_run(AgentState.COMPLETED, report))
    _add_task(client, "memory-review-ish")

    body = client.post("/schedules/memory-review-ish/run").json()

    assert body["last_status"] == "ok", body
    assert body["last_detail"] == "I reviewed 12 conversations and added 3 notes to memory."


def test_the_outcome_vocabulary():
    assert ro.fire_status("needs_you") == "needs_you"
    assert ro.fire_status("completed_with_failures") == "partial"
    assert ro.fire_status("completed") == "ok"
    assert ro.fire_status(None) == "ok"
    assert ro.delivery_head("ok") == "done"
    assert ro.delivery_head("error") == "FAILED"
    # v1.358.0 (lane 8A, on purpose): the row's own word, never "finished".
    assert ro.delivery_head("needs_you") == "needs you"
    assert ro.delivery_head("partial") == "finished, but part of it failed"
    # A very short opener carries the next sentence; a title-only text is
    # not nothing; a reason is never prefixed twice.
    assert ro.plain_sentence("Done. Wrote RESULT.md summarizing the task.") == (
        "Done. Wrote RESULT.md summarizing the task."
    )
    assert ro.plain_sentence("# Weekly review") == "Weekly review"
    assert ro.failure_detail("The run failed: x") == "The run failed: x"
    assert ro.failure_detail("") .startswith("The run failed")
    assert "error" in ro.FAILED_STATUSES
    assert set(ro.NEEDS_A_LOOK_STATUSES) == {"needs_you", "partial"}


async def test_a_queued_job_carries_the_verdict_on_assignment_finished(tmp_path):
    from iron_jarvis.assignments import AssignmentDispatcher
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    platform.orchestrator = Orchestrator(platform)
    dispatcher = AssignmentDispatcher(platform, interval_s=0.05)
    platform.assignment_dispatcher = dispatcher
    _plant_on_create(platform, "assignment:", _expired_ask)
    rec, _ = platform.assignments.create("builder", "rename the scans")

    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()
    await platform.assignments.flush_events()
    await asyncio.sleep(0)

    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(select(EventRecord).where(EventRecord.type == EventType.ASSIGNMENT_FINISHED))
        )
    payloads = [json.loads(r.payload_json or "{}") for r in rows]
    assert len(payloads) == 1, payloads
    assert payloads[0]["ok"] is True, "the run completed…"
    assert payloads[0]["outcome"] == "needs_you", "…and the job still needs the user"


# --------------------------------------------------------------------------- #
# G02 — the Status map and the home line see a schedule that fell short
# --------------------------------------------------------------------------- #


def _nodes(body: dict) -> dict:
    return {n["key"]: n for area in body["areas"] for n in area["nodes"]}


def test_a_schedule_that_failed_on_its_last_run_turns_the_star_amber_and_reaches_the_home(
    tmp_path, monkeypatch
):
    app, client = _app(tmp_path)
    orch = app.state.platform.orchestrator
    assert _nodes(client.get("/ui/system-map").json())["schedules"]["attention"] is None
    assert client.get("/ui/status-line").json()["short_schedules"] == 0

    async def boom(session, agent_def, *a, **kw):
        raise RuntimeError("the endpoint is down")

    monkeypatch.setattr(orch.runtime, "run", boom)
    _add_task(client, "nightly")
    assert client.post("/schedules/nightly/run").json()["last_status"] == "error"

    star = _nodes(client.get("/ui/system-map").json())["schedules"]
    assert star["attention"] == "1 schedule failed on its last run"
    assert client.get("/ui/status-line").json()["short_schedules"] == 1


def test_short_rows_are_named_and_counted_and_disabled_or_fine_rows_are_not(tmp_path):
    app, client = _app(tmp_path)
    with session_scope(app.state.platform.engine) as db:
        for name, status, enabled in (
            ("a", "error", True),
            ("b", "needs_you", True),
            ("c", "partial", True),
            ("d", "ok", True),
            ("e", "skipped", True),
            ("f", "error", False),  # switched off: nothing to look at
        ):
            db.add(ScheduledTaskRecord(name=name, cron="0 2 * * *", kind="task", last_status=status, enabled=enabled))
        db.commit()
    star = _nodes(client.get("/ui/system-map").json())["schedules"]
    assert star["attention"] == (
        "1 schedule failed on its last run · 2 schedules finished but need a look"
    )
    assert client.get("/ui/status-line").json()["short_schedules"] == 3


# --------------------------------------------------------------------------- #
# G13 — Emergency stop stops self-started work, and only that
# --------------------------------------------------------------------------- #


async def test_create_session_refuses_every_unattended_door_while_emergency_stop_is_on(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    platform.config.autonomy_kill_switch = True
    unattended = (
        "schedule:nightly", "goal:g1", "reflex:inbox", "workflow:w", "comm:telegram",
        "assignment:a1", "autonomy", "memory-review",
    )
    for origin in unattended:
        with pytest.raises(EmergencyStopOn) as err:
            await orch.create_session("tidy up", origin=origin)
        assert str(err.value) == EMERGENCY_STOP_REFUSAL
        assert orch.emergency_stop_reason(origin) == EMERGENCY_STOP_REFUSAL
    with session_scope(platform.engine) as db:
        assert list(db.exec(select(Session))) == [], "nothing was created"
    # A person is watching these: never refused.
    for origin in (None, "", "chat", "job:mission", "job:mission-member", "project:p1", "user"):
        s = await orch.create_session("answer me", origin=origin)
        assert s.id
        assert orch.emergency_stop_reason(origin) == ""
    platform.config.autonomy_kill_switch = False
    assert (await orch.create_session("tidy up", origin="schedule:nightly")).id
    assert orch.emergency_stop_reason("schedule:nightly") == ""


def test_a_due_schedule_is_skipped_and_its_row_says_why(tmp_path):
    app, client = _app(tmp_path)
    platform = app.state.platform
    _add_task(client, "nightly")
    first = client.post("/schedules/nightly/run").json()
    assert first["last_status"] == "ok" and first["last_session_id"]
    assert client.post("/autonomy/kill", json={"enabled": True}).json()["kill_switch"] is True
    mock = _mock_channel(app)
    before = len(mock.sent)

    # Run now, and the APScheduler entrypoint a due fire takes.
    body = client.post("/schedules/nightly/run").json()
    assert body["last_status"] == "skipped", body
    assert body["last_detail"] == ro.EMERGENCY_STOP_SKIP
    assert body["last_session_id"] == first["last_session_id"], "the last real run stays linked"
    platform.scheduler._fire("nightly")
    row = platform.scheduler.get("nightly")
    assert row.last_status == "skipped" and row.last_detail == ro.EMERGENCY_STOP_SKIP

    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(Session).where(Session.origin == "schedule:nightly")))
    assert len(runs) == 1, "no new run started"
    assert not _schedule_messages(mock, before, "nightly"), "a skip is not announced"

    # Released: it runs again.
    client.post("/autonomy/kill", json={"enabled": False})
    assert client.post("/schedules/nightly/run").json()["last_status"] == "ok"


def _wait(pred, timeout=15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_engaging_emergency_stop_cancels_a_running_scheduled_run_and_spares_chat(
    tmp_path, monkeypatch
):
    app, client = _app(tmp_path)
    platform = app.state.platform
    orch = platform.orchestrator
    working = threading.Event()

    async def long_job(session, agent_def, *a, **kw):
        working.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(orch.runtime, "run", long_job)
    _add_task(client, "nightly")
    # A due fire runs on APScheduler's own thread under asyncio.run.
    fire = threading.Thread(target=platform.scheduler._fire, args=("nightly",), daemon=True)
    fire.start()
    assert working.wait(15), "the scheduled run never started"
    with session_scope(platform.engine) as db:
        chat = Session(task="what is on today?", origin="chat", status=SessionStatus.ACTIVE)
        rule = Session(task="file the email", origin="reflex:inbox", status=SessionStatus.ACTIVE)
        db.add(chat)
        db.add(rule)
        db.commit()
        chat_id, rule_id = chat.id, rule.id

    r = client.post("/autonomy/kill", json={"enabled": True})

    assert r.status_code == 200 and r.json() == {"kill_switch": True, "stopped": 2}, r.text
    fire.join(15)
    assert not fire.is_alive(), "the cancel reached the scheduler thread's loop"
    row = platform.scheduler.get("nightly")
    assert row.last_status == "cancelled", (row.last_status, row.last_detail)
    assert row.last_detail == EMERGENCY_STOP_STOPPED
    stopped = orch.get_session(row.last_session_id)
    assert stopped.status is SessionStatus.CANCELLED
    assert stopped.summary.startswith(EMERGENCY_STOP_STOPPED)
    assert orch.get_session(rule_id).status is SessionStatus.CANCELLED
    assert orch.get_session(chat_id).status is SessionStatus.ACTIVE, "chat is never touched"


def test_the_settings_row_and_ctrl_k_stop_running_work_too(tmp_path):
    """Every door that flips the switch goes through the ConfigWriter, which
    publishes ``config.changed``; the platform's watcher stops the runs."""
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        platform = app.state.platform
        with session_scope(platform.engine) as db:
            goal = Session(task="iterate", origin="goal:g1", status=SessionStatus.ACTIVE)
            chat = Session(task="hi", origin="chat", status=SessionStatus.ACTIVE)
            db.add(goal)
            db.add(chat)
            db.commit()
            goal_id, chat_id = goal.id, chat.id
        r = client.put("/settings/values", json={"values": {"autonomy_kill_switch": True}})
        assert r.status_code == 200, r.text
        orch = platform.orchestrator
        assert _wait(lambda: orch.get_session(goal_id).status is SessionStatus.CANCELLED)
        assert orch.get_session(goal_id).summary.startswith(EMERGENCY_STOP_STOPPED)
        assert orch.get_session(chat_id).status is SessionStatus.ACTIVE


def test_chat_still_answers_with_emergency_stop_on(tmp_path):
    app, client = _app(tmp_path)
    client.post("/autonomy/kill", json={"enabled": True})
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hello there"}]})
    assert r.status_code == 200, r.text
    assert str(r.json().get("reply") or "").strip()


# --------------------------------------------------------------------------- #
# Review of lane 1A (v1.351.0)
# --------------------------------------------------------------------------- #


def test_a_failed_run_whose_ask_expired_reads_failed_like_its_badge(tmp_path, monkeypatch):
    """v1.358.0 (lane 8A, CHANGED ON PURPOSE — the re-audit's regression): a
    run that ended FAILED while an ask expired used to read "Done · needs
    you" on the row and "finished, but it needs you" in the notice, while the
    run's own badge said "Failed". Now the row is ``error`` ("Failed"), the
    notice says FAILED, and the reason says the ask went unanswered first —
    the person still learns they were asked."""
    app, client = _app(tmp_path)
    platform = app.state.platform
    _plant_on_create(platform, "schedule:", _expired_ask)
    monkeypatch.setattr(
        platform.orchestrator.runtime,
        "run",
        _fake_run(AgentState.FAILED, "Stopped at the folder it was not allowed to open."),
    )
    _add_task(client, "tidy-scans")
    mock = _mock_channel(app)
    before = len(mock.sent)

    body = client.post("/schedules/tidy-scans/run").json()

    assert body["last_status"] == "error", body
    assert body["last_detail"].startswith("The run failed: " + ro.ASK_EXPIRED_REASON), body
    assert "Stopped at the folder it was not allowed to open." in body["last_detail"], body
    session = platform.orchestrator.get_session(body["last_session_id"])
    assert session.outcome == "needs_you", "the ledger's own verdict is unchanged"
    sent = _schedule_messages(mock, before, "tidy-scans")
    assert sent and all(": FAILED" in m for m in sent), sent
    assert not any("needs you" in m for m in sent), sent


async def test_emergency_stop_never_touches_a_self_development_run_a_person_started(tmp_path):
    """``create_session`` writes origin "self_dev" for a self-development run
    a person started (the Self-development page, ``ij``, a rerun of one). The
    switch must neither refuse it nor cancel it — "work you start keeps
    going" — while an unattended row beside it IS stopped (the control)."""
    from iron_jarvis.agents.orchestrator import SELF_DEV_ORIGIN, emergency_stop_covers
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    platform.config.autonomy_kill_switch = True
    assert SELF_DEV_ORIGIN == "self_dev"
    assert not emergency_stop_covers("self_dev")
    assert emergency_stop_covers("autonomy") and emergency_stop_covers("schedule:x")
    assert orch.emergency_stop_reason("self_dev") == ""
    rerun = await orch.create_session("tidy the repo", origin="self_dev")
    assert rerun.id, "a rerun of a self-development run is not refused"
    with session_scope(platform.engine) as db:
        mine = Session(task="tidy the repo", origin="self_dev", status=SessionStatus.ACTIVE)
        rule = Session(task="file the email", origin="reflex:inbox", status=SessionStatus.ACTIVE)
        db.add(mine)
        db.add(rule)
        db.commit()
        mine_id, rule_id = mine.id, rule.id

    stopped = orch.stop_unattended()

    assert rule_id in stopped and mine_id not in stopped, stopped
    assert orch.get_session(mine_id).status is SessionStatus.ACTIVE
    assert orch.get_session(rule_id).status is SessionStatus.CANCELLED
