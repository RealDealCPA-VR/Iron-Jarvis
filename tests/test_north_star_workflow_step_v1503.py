"""North-star plan 3.2 (v1.503.0): a workflow's agent step reads the verdict.

``workflows/engine._run_agent_step`` marked a step "completed" on the run's
STATUS alone, so a step whose run ended ``needs_you`` (an ask nobody answered)
or ``completed_with_failures`` (a change that did not land) was a done step:
``on_failure`` never routed it, the later steps ran on work that was never
done, and the scheduled workflow's Schedules row read "done — workflow ran".

Through the REAL app factory and the real scheduler entry point (Run now);
only the model is stubbed, and the shortfall is written where the ledger
reads it (a failed mutating call, an ask the clock answered).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import AgentState, EventRecord, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro

SHORT_SUMMARY = "I updated the ledger."


def _shortfall_runtime(platform):
    """A run whose task says PARTIAL makes a failed mutating call; one that
    says ASKED had its ask answered by the clock. Both end COMPLETED."""

    async def run(session, agent_def, *a, **kw):
        task = session.task or ""
        with session_scope(platform.engine) as db:
            if "PARTIAL" in task:
                db.add(ToolInvocation(session_id=session.id, agent_run_id="r", tool="write_file",
                                      ok=False, reversibility="reversible",
                                      output="disk is full"))
            if "ASKED" in task:
                db.add(EventRecord(id=new_id("evt"), type=EventType.APPROVAL_RESOLVED,
                                   session_id=session.id,
                                   payload_json=json.dumps({"decision": "timeout"})))
            if "CLEAN" in task:
                db.add(ToolInvocation(session_id=session.id, agent_run_id="r", tool="write_file",
                                      ok=True, reversibility="reversible", output="ok"))
            db.commit()
        return SimpleNamespace(
            state=AgentState.COMPLETED, provider=session.provider, model=session.model,
            result=SHORT_SUMMARY, input_tokens=0, output_tokens=0,
        )

    return run


def _fire(tmp_path, monkeypatch, steps):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _shortfall_runtime(platform))
    r = client.post("/workflows", json={"name": "Nightly", "steps": steps})
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "nightly", "cron": "0 3 * * *", "kind": "workflow",
              "payload": {"workflow": "Nightly"}},
    )
    assert r.status_code == 200, r.text
    body = client.post("/schedules/nightly/run").json()
    runs = client.get("/workflows/runs").json()
    runs = runs.get("runs", runs) if isinstance(runs, dict) else runs
    return SimpleNamespace(body=body, runs=runs, platform=platform, client=client)


def _outputs(out):
    from iron_jarvis.workflows.models import WorkflowRunRecord

    with session_scope(out.platform.engine) as db:
        rows = list(db.exec(__import__("sqlmodel").select(WorkflowRunRecord)))
    assert len(rows) == 1, rows
    return rows[0], json.loads(rows[0].outputs_json or "{}")


TELL = {"name": "Tell", "kind": "agent", "task": "CLEAN: tell the team"}


def test_a_part_failed_step_halts_the_run_and_the_row_says_why(tmp_path, monkeypatch):
    out = _fire(tmp_path, monkeypatch, [
        {"name": "Fetch", "kind": "agent", "task": "PARTIAL: update the ledger"}, TELL,
    ])
    record, outs = _outputs(out)
    assert outs["Fetch"]["status"] == "failed", outs
    assert outs["Fetch"]["outcome"] == "completed_with_failures", outs
    assert outs["Fetch"]["summary"].startswith(ro.PARTIAL_LEAD), outs
    assert SHORT_SUMMARY in outs["Fetch"]["summary"], outs
    # The later step never ran on work that was never done.
    assert outs["Tell"] == {"status": "skipped"}, outs
    assert record.status == "failed"
    # The Schedules row: the step that stopped it, in the verdict's words.
    assert out.body["last_status"] == ro.STATUS_ERROR, out.body
    assert out.body["last_detail"].startswith(
        f"The run failed: workflow “Nightly” stopped at its step “Fetch”: {ro.PARTIAL_LEAD}"
    ), out.body


def test_a_needs_you_step_is_routed_by_on_failure_skip(tmp_path, monkeypatch):
    out = _fire(tmp_path, monkeypatch, [
        {"name": "Fetch", "kind": "agent", "task": "ASKED: update the ledger", "on_failure": "skip"},
        TELL,
    ])
    record, outs = _outputs(out)
    assert outs["Fetch"]["status"] == "failed" and outs["Fetch"]["handled"] == "skipped", outs
    assert outs["Fetch"]["outcome"] == "needs_you", outs
    assert outs["Tell"]["status"] == "completed", outs
    assert record.status == "completed_with_failures"
    assert out.body["last_status"] == ro.STATUS_PARTIAL, out.body
    assert "“Fetch”" in out.body["last_detail"], out.body
    assert ro.NEEDS_YOU_LEAD in out.body["last_detail"], out.body


def test_a_step_whose_run_did_its_job_is_still_completed(tmp_path, monkeypatch):
    out = _fire(tmp_path, monkeypatch, [TELL])
    record, outs = _outputs(out)
    assert outs["Tell"]["status"] == "completed", outs
    assert outs["Tell"]["outcome"] == "completed", outs
    assert outs["Tell"]["summary"] == SHORT_SUMMARY
    assert record.status == "completed"
    assert out.body["last_status"] == ro.STATUS_OK, out.body
    assert out.body["last_detail"] == "workflow “Nightly” ran", out.body


def test_a_workflow_step_that_only_wrote_text_is_not_did_nothing(tmp_path, monkeypatch):
    """A workflow step's text is often the work (a summary the next step
    uses): no tool call is not a shortfall there (3.11 is scoped to the
    schedule, queued-job and phone doors)."""
    out = _fire(tmp_path, monkeypatch, [{"name": "Sum", "kind": "agent", "task": "summarise it"}])
    record, outs = _outputs(out)
    assert outs["Sum"]["status"] == "completed", outs
    assert record.status == "completed"
