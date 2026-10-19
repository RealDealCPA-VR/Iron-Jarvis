"""v1.352.0 integrate (a wave-1 leftover the v1.351.0 re-review flagged):
Emergency stop admits a workflow a PERSON started by pressing Run.

THE GAP. ``create_session`` refuses every unattended origin while the switch
is on, and a workflow's agent steps are stamped ``workflow:<name>`` whoever
started the run — a schedule, a rule, a goal, or the user pressing Run. So
with Emergency stop on, the user's own Run had its agent steps refused,
while the setting promises "work you start keeps going".

NOW: ``POST /workflows/run`` (and ``ironjarvis workflow run``) marks the
record ``started_by = "person"``; the engine reads it off the RECORD (so a
resume of that run is admitted too) and passes ``started_by_person=True`` to
``create_session`` for its agent steps, which admits them and stamps
``options["started_by_person"]`` so engaging the switch later
(``stop_unattended``) leaves them going. Every other door — the scheduler's
workflow dispatch, a rule, a goal, the chat tool — calls the engine with no
mark and stays refused. Driven through the real app factory, the real engine
and the real orchestrator (the offline mock answers the agent steps).
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_REFUSAL,
    PERSON_STARTED_OPTION,
    EmergencyStopOn,
    Orchestrator,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.workflows.engine import STARTED_BY_PERSON, WorkflowEngine, load_workflow
from iron_jarvis.workflows.models import WorkflowRunRecord

_STEPS = [{"name": "draft", "agent": "builder", "task": "say hi"}]


def _engage(client: TestClient) -> None:
    r = client.post("/autonomy/kill", json={"enabled": True})
    assert r.status_code == 200 and r.json()["kill_switch"] is True, r.text


def _wait_run(client: TestClient, run_id: str, timeout: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/workflows/runs/{run_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") not in ("running", "resuming", "cancelling"):
            return last
        time.sleep(0.1)
    raise AssertionError(f"the run never settled: {last}")


def _step_session(p, run: dict) -> Session | None:
    out = json.loads(run.get("outputs_json") or "{}")
    sid = (out.get("draft") or {}).get("session_id") or ""
    if not sid:
        return None
    with session_scope(p.engine) as db:
        row = db.get(Session, sid)
        if row is not None:
            db.expunge(row)
        return row


def _sessions(p) -> int:
    with session_scope(p.engine) as db:
        return len(list(db.exec(select(Session))))


def test_a_workflow_the_user_runs_keeps_going_under_emergency_stop(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    with TestClient(app) as client:
        _engage(client)
        r = client.post("/workflows/run", json={"name": "brief", "steps": _STEPS})
        assert r.status_code == 200, r.text
        rec = r.json()
        # the record says a person started it
        assert rec["started_by"] == STARTED_BY_PERSON
        run = _wait_run(client, rec["id"])
    out = json.loads(run["outputs_json"])["draft"]
    # the agent step RAN: a session exists and the refusal is nowhere
    assert EMERGENCY_STOP_REFUSAL not in str(out.get("summary") or ""), out
    row = _step_session(p, run)
    assert row is not None, out
    assert str(row.origin).startswith("workflow:"), row.origin  # provenance unchanged
    assert json.loads(row.options_json or "{}").get(PERSON_STARTED_OPTION) is True


def test_a_workflow_that_starts_on_its_own_is_still_refused(tmp_path):
    """The scheduler's workflow dispatch (and a rule's, a goal's, the chat
    tool's) runs the engine with no mark: the agent step is refused with the
    switch's own sentence and no session is created."""
    import asyncio

    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)
    p.config.autonomy_kill_switch = True
    before = _sessions(p)
    rec = asyncio.run(
        WorkflowEngine(p, orch).run(load_workflow({"name": "nightly", "steps": _STEPS}))
    )
    assert rec.started_by == ""
    out = json.loads(rec.outputs_json)["draft"]
    assert out["status"] == "failed", out
    assert EMERGENCY_STOP_REFUSAL in str(out.get("summary") or ""), out
    assert _sessions(p) == before, "no session may start under Emergency stop"


def test_a_resume_of_the_users_run_is_admitted_too(tmp_path):
    """The mark lives on the RECORD, so the user's Resume of their own
    interrupted run keeps going under the switch."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    with session_scope(p.engine) as db:
        rec = WorkflowRunRecord(
            workflow_name="brief",
            status="interrupted",
            steps_json=json.dumps(_STEPS),
            started_by=STARTED_BY_PERSON,
        )
        db.add(rec)
        db.commit()
        run_id = rec.id
    with TestClient(app) as client:
        _engage(client)
        r = client.post(f"/workflows/runs/{run_id}/resume")
        assert r.status_code == 200, r.text
        run = _wait_run(client, run_id)
    out = json.loads(run["outputs_json"])["draft"]
    assert EMERGENCY_STOP_REFUSAL not in str(out.get("summary") or ""), out
    assert _step_session(p, run) is not None, out


async def test_engaging_the_switch_leaves_the_users_workflow_step_going(tmp_path):
    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)
    mine = await orch.create_session("draft", origin="workflow:brief", started_by_person=True)
    theirs = await orch.create_session("draft", origin="workflow:nightly")
    assert json.loads(mine.options_json)[PERSON_STARTED_OPTION] is True
    assert PERSON_STARTED_OPTION not in json.loads(theirs.options_json or "{}")

    stopped = orch.stop_unattended()

    assert theirs.id in stopped and mine.id not in stopped, stopped
    assert orch.get_session(mine.id).status is SessionStatus.ACTIVE
    assert orch.get_session(theirs.id).status is SessionStatus.CANCELLED


async def test_only_the_kwarg_marks_a_run_person_started(tmp_path):
    """A door cannot smuggle the mark in through ``options``: it is dropped,
    and the run is refused like any other unattended start."""
    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)
    p.config.autonomy_kill_switch = True
    with pytest.raises(EmergencyStopOn):
        await orch.create_session(
            "draft", origin="workflow:nightly", options={PERSON_STARTED_OPTION: True}
        )
    p.config.autonomy_kill_switch = False
    row = await orch.create_session(
        "draft", origin="workflow:nightly", options={PERSON_STARTED_OPTION: True}
    )
    assert PERSON_STARTED_OPTION not in json.loads(row.options_json or "{}")
