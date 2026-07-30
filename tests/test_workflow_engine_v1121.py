"""v1.121.0 — W-B: the engine grows step kinds, failure routing, parallel
groups, and the human gate with durably parked runs.

Offline throughout: agent steps ride the mock provider; tool steps hit the
real registry; ask parks against the real database.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.workflows.engine import (
    Step,
    WorkflowDef,
    WorkflowEngine,
    load_workflow,
    render_template,
)
from iron_jarvis.workflows.models import (
    WorkflowRunRecord,
    reconcile_interrupted_runs,
)


# --------------------------------------------------------------------------- #
# parsing + templating
# --------------------------------------------------------------------------- #


def test_load_workflow_parses_and_coerces_new_fields():
    wf = load_workflow(
        {
            "name": "x",
            "steps": [
                {
                    "name": "a",
                    "kind": "tool",
                    "tool": "list_folder",
                    "args": {"path": "."},
                    "on_failure": "retry",
                    "group": "g1",
                },
                {"name": "b", "kind": "bogus", "on_failure": "explode"},
            ],
        }
    )
    assert wf.steps[0].kind == "tool"
    assert wf.steps[0].on_failure == "retry"
    assert wf.steps[0].group == "g1"
    assert wf.steps[1].kind == "agent"  # unknown kind coerces, never crashes
    assert wf.steps[1].on_failure == "halt"


def test_render_template_substitutes_prior_outputs():
    outs = {"Gather": {"status": "completed", "summary": "12 receipts"}}
    assert render_template("Check {{Gather}} now", outs) == "Check 12 receipts now"
    # Unknown references render empty — braces never leak into a prompt.
    assert render_template("x {{Nope}} y", outs) == "x  y"


# --------------------------------------------------------------------------- #
# step kinds
# --------------------------------------------------------------------------- #


async def test_tool_step_runs_deterministically_without_a_session(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="toolish",
        steps=[
            Step(name="List", kind="tool", tool="list_folder", args={"path": str(tmp_path)}),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    outs = json.loads(rec.outputs_json)
    assert outs["List"]["kind"] == "tool"
    assert outs["List"]["status"] == "completed"
    # ZERO sessions — that is the point of a tool step.
    assert json.loads(rec.session_ids_json) == []


async def test_notify_step_delivers_to_destinations(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = next(
        ch for ch in platform.notifier._channels.values() if isinstance(ch, MockChannel)
    )
    wf = WorkflowDef(
        name="speaker",
        steps=[
            Step(name="Work", agent="builder", task="produce a number"),
            Step(name="Tell", kind="notify", message="Result was {{Work}}"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    assert any("Result was" in m for m in mock.sent)


async def test_tool_step_refuses_non_readonly_ask_tools(tmp_path):
    # SECURITY (v1.121.0 review): a stored def is not interactive consent.
    # Agent-authorable + schedulable workflows must NOT be able to self-grant
    # ask-mode host tools (shell, run_code, …) — that chain would take planted
    # content to the host with no human in the loop.
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="sneaky",
        steps=[Step(name="Own", kind="tool", tool="shell", args={"command": "echo pwned"})],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Own"]["summary"]
    assert "interactive approval" in summary


async def test_notify_step_fails_honestly_when_delivery_fails(tmp_path):
    # A notify step's entire job is delivery — a swallowed failure would show
    # a green run whose message never arrived.
    platform = build_platform(str(tmp_path))
    platform.notifier.notify = lambda msg, channels=None: {
        "telegram": {"ok": False, "detail": "bot token revoked"}
    }
    wf = WorkflowDef(
        name="mute", steps=[Step(name="Tell", kind="notify", message="hello")]
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    assert "bot token revoked" in json.loads(rec.outputs_json)["Tell"]["summary"]


async def test_unknown_tool_fails_the_run_honestly(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="broken-tool",
        steps=[Step(name="Boom", kind="tool", tool="no_such_tool")],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    assert "no_such_tool" in json.loads(rec.outputs_json)["Boom"]["summary"]


# --------------------------------------------------------------------------- #
# failure routing
# --------------------------------------------------------------------------- #


async def test_on_failure_skip_continues_the_run(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="skippy",
        steps=[
            Step(name="Flaky", kind="tool", tool="no_such_tool", on_failure="skip"),
            Step(name="After", agent="builder", task="carry on"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"  # the run survived the failure
    outs = json.loads(rec.outputs_json)
    assert outs["Flaky"]["status"] == "failed"  # the failure stays VISIBLE
    assert outs["Flaky"]["handled"] == "skipped"
    assert outs["After"]["status"] == "completed"


async def test_on_failure_halt_still_stops_the_run(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="halty",
        steps=[
            Step(name="Boom", kind="tool", tool="no_such_tool"),  # halt default
            Step(name="Never", agent="builder", task="unreachable"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    assert json.loads(rec.outputs_json)["Never"]["status"] == "skipped"


# --------------------------------------------------------------------------- #
# parallel groups
# --------------------------------------------------------------------------- #


async def test_grouped_steps_run_and_both_complete(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="para",
        steps=[
            Step(name="L", kind="tool", tool="list_folder", args={"path": str(tmp_path)}, group="g"),
            Step(name="R", kind="tool", tool="list_folder", args={"path": str(tmp_path)}, group="g"),
            Step(name="Join", agent="builder", task="combine {{L}} and {{R}}"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    outs = json.loads(rec.outputs_json)
    assert outs["L"]["status"] == "completed"
    assert outs["R"]["status"] == "completed"
    assert outs["Join"]["status"] == "completed"


# --------------------------------------------------------------------------- #
# the human gate: park, deliver, answer, resume — across a "restart"
# --------------------------------------------------------------------------- #


ASKY = {
    "name": "gated",
    "steps": [
        {"name": "Draft", "agent": "builder", "task": "draft the email"},
        {"name": "Approve", "kind": "ask", "message": "Send the draft ({{Draft}})?"},
        {"name": "Send", "kind": "notify", "message": "Sent after: {{Approve}}"},
    ],
}


async def test_ask_parks_the_run_and_delivers_the_question(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = next(
        ch for ch in platform.notifier._channels.values() if isinstance(ch, MockChannel)
    )
    rec = await WorkflowEngine(platform).run(load_workflow(ASKY))
    assert rec.status == "waiting"
    waiting = json.loads(rec.waiting_json)
    assert waiting["step"] == "Approve"
    assert "Send the draft" in waiting["question"]
    # The question reached the user's destinations.
    assert any("needs you" in m and "gated" in m for m in mock.sent)
    # Later steps have NOT run.
    outs = json.loads(rec.outputs_json)
    assert "Send" not in outs


async def test_parked_run_survives_restart_and_answer_resumes(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    rec = await engine.run(load_workflow(ASKY))
    assert rec.status == "waiting"

    # A daemon restart reconciles interrupted runs — parked runs must SURVIVE.
    flipped = reconcile_interrupted_runs(platform.engine)
    assert flipped == 0

    # Rebuild everything from the database alone (fresh engine = fresh boot).
    from iron_jarvis.core.db import session_scope

    with session_scope(platform.engine) as db:
        stored = db.get(WorkflowRunRecord, rec.id)
    resumed = await WorkflowEngine(platform).resume_after_answer(stored, "yes, send it")
    assert resumed.status == "completed"
    outs = json.loads(resumed.outputs_json)
    assert outs["Approve"]["summary"] == "User answered: yes, send it"
    assert outs["Send"]["status"] == "completed"
    # The answer flowed into the templated downstream step.
    assert "yes, send it" in outs["Send"]["summary"]


# --------------------------------------------------------------------------- #
# the HTTP layer: answer endpoint + cancel of a parked run
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


# The daemon's TestClient tears its event loop down per request, so a
# BACKGROUND run never progresses between polls — the full park→answer→resume
# loop is proven at the engine level above (and live in the browser proof).
# These tests own the ROUTE mechanics against directly-seeded records.

def _seed_run(client, status: str, waiting: dict | None = None) -> str:
    from iron_jarvis.core.db import dumps, session_scope
    from iron_jarvis.workflows.engine import Step, step_to_dict

    steps = [
        step_to_dict(Step(name="Approve", kind="ask", message="Go?")),
        step_to_dict(Step(name="Send", kind="notify", message="Sent")),
    ]
    rec = WorkflowRunRecord(
        workflow_name="gated",
        status=status,
        steps_json=dumps(steps),
        outputs_json="{}",
        session_ids_json="[]",
        waiting_json=dumps(waiting) if waiting else "",
    )
    with session_scope(client.app.state.platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    return rec.id


WAITING = {"index": 0, "step": "Approve", "question": "Go?"}


def test_answer_endpoint_accepts_a_waiting_run(client):
    run_id = _seed_run(client, "waiting", WAITING)
    r = client.post(f"/workflows/runs/{run_id}/answer", json={"answer": "approved"})
    assert r.status_code == 200
    assert r.json()["answered"] is True


def test_answer_requires_text_and_a_waiting_run(client):
    run_id = _seed_run(client, "waiting", WAITING)
    assert (
        client.post(f"/workflows/runs/{run_id}/answer", json={"answer": "  "}).status_code
        == 400
    )
    done_id = _seed_run(client, "completed")
    r = client.post(f"/workflows/runs/{done_id}/answer", json={"answer": "ok"})
    assert r.status_code == 409
    assert "not waiting" in r.json()["detail"]


def test_answer_double_submit_loses_the_race_honestly(client):
    # The gate is answerable from chat AND the Workflows page at once — the
    # claim (waiting -> resuming) must be atomic or the tail runs twice.
    run_id = _seed_run(client, "waiting", WAITING)
    first = client.post(f"/workflows/runs/{run_id}/answer", json={"answer": "yes"})
    assert first.status_code == 200
    second = client.post(f"/workflows/runs/{run_id}/answer", json={"answer": "yes"})
    assert second.status_code == 409
    assert "already" in second.json()["detail"]


def test_scheduled_gated_workflow_reports_waiting_not_done(client):
    # A schedule firing a gated workflow must NOT stamp "done" the instant the
    # run parks — the contradictory "Schedule done" + "Workflow needs you"
    # pair was the bug.
    client.post(
        "/workflows",
        json={
            "name": "gated-sched",
            "steps": [{"name": "Approve", "kind": "ask", "message": "Go?"}],
        },
    )
    client.post(
        "/schedules",
        json={
            "name": "fire-gated",
            "cron": "0 8 * * *",
            "kind": "workflow",
            "payload": {"workflow": "gated-sched", "notify": False},
        },
    )
    body = client.post("/schedules/fire-gated/run").json()
    assert body["last_status"] == "ok"
    assert "waiting for your answer" in body["last_detail"]


def test_cancel_of_a_parked_run_cancels_directly(client):
    # A waiting run has NO engine loop to notice a 'cancelling' flag — the
    # cancel route must finalize it in place.
    run_id = _seed_run(client, "waiting", WAITING)
    r = client.post(f"/workflows/runs/{run_id}/cancel")
    assert r.status_code == 200
    got = client.get(f"/workflows/runs/{run_id}").json()
    assert got["status"] == "cancelled"
    assert got["finished_at"] is not None
    assert got["waiting_json"] == ""
