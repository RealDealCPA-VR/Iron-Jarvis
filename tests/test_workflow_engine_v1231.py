"""Workflow engine, audit Wave 5 task 5B (v1.231.0): retries honour a
crashed session, finished steps survive a restart, references are checked.

Converted from the 2026-09-04 audit repros ``test_a2_step_failure.py`` and
``test_a1_workflow_restart.py`` (both deleted with this file). Every test
drives the REAL engine + DB on a temp root; agent steps go through a stubbed
``Orchestrator.run_session`` so RAISE vs FAIL is explicit, and a daemon
"crash" is the driver task being cancelled while an agent step is parked on
a never-completing session — nothing after the ``gather`` runs, exactly as
when the process dies.

  AE3   an agent step whose session RAISES is a failed step: retries and
        on_failure see it and ``workflow.step_completed`` fires
  AE4   each step's output lands on the record as it completes, so a
        restart mid-parallel-group keeps the finished members and Resume
        neither re-runs nor re-notifies them
  AE16  the pinned-folder note is written every run: a resume whose folder
        is back clears the stale "no folder" note
  AE10  ``{{Ref.data}}`` naming no step is refused at save (422, naming the
        step and the reference); a failed step's bare ``{{Ref}}`` renders
        ``[step Ref failed: …]`` instead of its error text as content, and
        its ``.data`` stays "" (a failed tool records no data)
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
import contextlib
import json

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.core.db import dumps, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Project, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.engine import (
    Step,
    WorkflowDef,
    WorkflowEngine,
    render_template,
    step_to_dict,
)
from iron_jarvis.workflows.models import WorkflowRunRecord, reconcile_interrupted_runs


def _mock_channel(platform) -> MockChannel:
    return next(
        ch for ch in platform.notifier._channels.values() if isinstance(ch, MockChannel)
    )


class ReportsFailure(Tool):
    """A tool that RAN and reported ok=False (the ledgered kind of failure)."""

    name = "t1231_reports_failure"
    description = "test-only"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=False, error="disk quota exceeded while writing report.xlsx")


class Crashes(Tool):
    """A tool that RAISED (never produced a ToolResult)."""

    name = "t1231_crashes"
    description = "test-only"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        raise KeyError("query")


class Captures(Tool):
    """Records the args it was called with (the downstream of a handoff)."""

    name = "t1231_captures"
    description = "test-only"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self):
        self.calls: list[dict] = []

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(ok=True, output="captured")


class FakeDataTool(Tool):
    name = "t1231_data_tool"
    description = "test-only data-returning tool"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, data=None):
        self._data = data

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output="made stuff", data=self._data)


def _completing(orch):
    async def run(session_id, definition=None):
        s = orch.get_session(session_id)
        s.status = SessionStatus.COMPLETED
        s.summary = "agent done"
        s.finished_at = utcnow()
        orch._save(s)
        return s

    return run


def _step_events(platform, step):
    return [
        e
        for e in platform.event_bus.history
        if e.type == "workflow.step_completed" and e.payload.get("step") == step
    ]


def _row(platform, run_id) -> WorkflowRunRecord:
    with session_scope(platform.engine) as db:
        return db.get(WorkflowRunRecord, run_id)


def _recorded_outputs(platform, run_id) -> dict:
    return json.loads(_row(platform, run_id).outputs_json or "{}")


# --------------------------------------------------------------------------- #
# AE3 — an agent step whose session RAISES (run_session re-raises provider
# blow-ups / DB errors after _finalize_failed) is a FAILED step: on_failure
# and retries see it, and the step's terminal event fires.
# --------------------------------------------------------------------------- #


async def test_agent_step_whose_session_raises_honors_on_failure_skip(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    orch = Orchestrator(platform)

    async def boom(session_id, definition=None):
        raise RuntimeError("provider fleet-custom returned HTTP 500")

    orch.run_session = boom
    engine = WorkflowEngine(platform, orch)
    wf = WorkflowDef(
        name="skipper",
        steps=[
            Step(name="Draft", kind="agent", task="draft it", on_failure="skip"),
            Step(name="Tell", kind="notify", message="after-draft"),
        ],
    )
    rec = await engine.run(wf)
    outs = json.loads(rec.outputs_json)
    assert outs["Draft"]["status"] == "failed"
    assert outs["Draft"]["summary"] == "RuntimeError: provider fleet-custom returned HTTP 500"
    assert outs["Draft"]["kind"] == "agent"
    assert outs["Draft"]["session_id"]  # the session that blew up is named
    # The promise of on_failure=skip: the failure stays VISIBLE on the step,
    # but the run continues.
    assert outs["Draft"].get("handled") == "skipped", outs["Draft"]
    assert any("after-draft" in m for m in mock.sent), mock.sent
    assert rec.status == "completed"
    # And the step's terminal event fires like every other failed step's.
    ev = _step_events(platform, "Draft")
    assert ev and ev[-1].payload["status"] == "failed", ev


async def test_agent_step_whose_session_raises_honors_on_failure_retry(tmp_path):
    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    calls = {"n": 0}
    ok = _completing(orch)

    async def flaky(session_id, definition=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ReadTimeout: model took too long")
        return await ok(session_id, definition)

    orch.run_session = flaky
    engine = WorkflowEngine(platform, orch)
    wf = WorkflowDef(
        name="retrier",
        steps=[Step(name="Draft", kind="agent", task="draft it", on_failure="retry")],
    )
    rec = await engine.run(wf)
    assert calls["n"] == 2, "retry never re-attempted the raised step"
    assert rec.status == "completed"
    outs = json.loads(rec.outputs_json)
    assert outs["Draft"]["status"] == "completed"
    assert len(json.loads(rec.session_ids_json)) == 2  # both attempts are listed


async def test_agent_step_whose_session_raises_under_halt_fails_the_run_with_the_reason(
    tmp_path,
):
    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)

    async def boom(session_id, definition=None):
        raise RuntimeError("provider fleet-custom returned HTTP 500")

    orch.run_session = boom
    engine = WorkflowEngine(platform, orch)
    rec = await engine.run(
        WorkflowDef(
            name="halter",
            steps=[
                Step(name="Draft", kind="agent", task="draft it"),
                Step(name="Tell", kind="notify", message="never"),
            ],
        )
    )
    outs = json.loads(rec.outputs_json)
    assert rec.status == "failed"
    assert outs["Draft"]["status"] == "failed" and "HTTP 500" in outs["Draft"]["summary"]
    assert outs["Tell"] == {"status": "skipped"}
    assert _step_events(platform, "Draft")[-1].payload["status"] == "failed"


# --------------------------------------------------------------------------- #
# Carried confirmations: "tool ran and reported failure" vs "tool raised"
# are both failed steps with distinct summaries; a session that FAILS
# (finalized, no raise) records the session summary.
# --------------------------------------------------------------------------- #


async def test_tool_reported_failure_and_tool_crash_are_worded_distinctly(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(ReportsFailure())
    platform.registry.register(Crashes())
    engine = WorkflowEngine(platform)
    rec = await engine.run(
        WorkflowDef(
            name="tools",
            steps=[
                Step(name="Report", kind="tool", tool="t1231_reports_failure", on_failure="skip"),
                Step(name="Crash", kind="tool", tool="t1231_crashes", on_failure="skip"),
            ],
        )
    )
    outs = json.loads(rec.outputs_json)
    assert outs["Report"]["status"] == "failed"
    assert outs["Report"]["summary"] == "disk quota exceeded while writing report.xlsx"
    assert outs["Crash"]["status"] == "failed"
    assert outs["Crash"]["summary"] == "KeyError: 'query'"
    assert rec.status == "completed"  # both skipped, run continued


async def test_agent_session_that_fails_records_the_session_summary(tmp_path):
    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)

    async def fails(session_id, definition=None):
        s = orch.get_session(session_id)
        s.status = SessionStatus.FAILED
        s.summary = "provider anthropic is not connected"
        s.finished_at = utcnow()
        orch._save(s)
        return s

    orch.run_session = fails
    engine = WorkflowEngine(platform, orch)
    rec = await engine.run(
        WorkflowDef(name="f", steps=[Step(name="Draft", kind="agent", task="x")])
    )
    outs = json.loads(rec.outputs_json)
    assert rec.status == "failed"
    assert outs["Draft"]["status"] == "failed"
    assert outs["Draft"]["summary"] == "provider anthropic is not connected"
    assert _step_events(platform, "Draft")[-1].payload["status"] == "failed"


# --------------------------------------------------------------------------- #
# AE10 — {{Step}} / {{Step.data}} handoff from a step that FAILED and was
# skipped: the bare ref says the step failed instead of passing its error
# text off as content; .data stays "" (a failed tool records no data).
# --------------------------------------------------------------------------- #


def test_render_template_marks_a_failed_step_and_keeps_its_data_empty():
    outs = {
        "Scan": {"status": "failed", "summary": "disk quota exceeded", "handled": "skipped"},
        "Make": {"status": "completed", "summary": "prose", "data": '{"n": 5}'},
    }
    assert render_template("got {{Scan}}", outs) == "got [step Scan failed: disk quota exceeded]"
    assert render_template("path={{Scan.data}}", outs) == "path="
    # Unchanged for a completed step and an unknown reference.
    assert render_template("{{Make}}/{{Make.data}}", outs) == 'prose/{"n": 5}'
    assert render_template("x {{Nope}} y", outs) == "x  y"


async def test_handoff_from_a_failed_skipped_step_says_the_step_failed(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(ReportsFailure())
    capture = Captures()
    platform.registry.register(capture)
    engine = WorkflowEngine(platform)
    rec = await engine.run(
        WorkflowDef(
            name="handoff",
            steps=[
                Step(name="Scan", kind="tool", tool="t1231_reports_failure", on_failure="skip"),
                Step(
                    name="Use",
                    kind="tool",
                    tool="t1231_captures",
                    args={"path": "{{Scan.data}}", "note": "{{Scan}}"},
                ),
            ],
        )
    )
    outs = json.loads(rec.outputs_json)
    assert outs["Scan"]["status"] == "failed" and outs["Scan"].get("handled") == "skipped"
    assert rec.status == "completed"
    assert capture.calls, "downstream tool never ran"
    args = capture.calls[0]
    # The bare ref names the failure instead of masquerading as Scan's output.
    assert args["note"] == "[step Scan failed: disk quota exceeded while writing report.xlsx]"
    # .data of a failed step is the empty string, by design (documented).
    assert args["path"] == ""


def test_save_refuses_a_data_reference_to_a_step_that_does_not_exist(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/workflows",
        json={
            "name": "typo",
            "steps": [
                {"name": "Scan", "kind": "notify", "message": "scanning"},
                {"name": "Tell", "kind": "notify", "message": "result: {{Scna.data}}"},
            ],
        },
    )
    assert r.status_code == 422, r.json()
    detail = r.json()["detail"]
    assert "steps[1] (Tell)" in detail and "{{Scna.data}}" in detail and "'Scna'" in detail
    assert client.get("/workflows/typo").status_code == 404  # nothing was saved


def test_save_checks_data_references_in_tool_args_too(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/workflows",
        json={
            "name": "typo-args",
            "steps": [
                {"name": "Scan", "kind": "tool", "tool": "read_file", "args": {"path": "x"}},
                {
                    "name": "Use",
                    "kind": "tool",
                    "tool": "read_file",
                    "args": {"path": "{{Scna.data}}", "tags": ["{{Scan.data}}"]},
                },
            ],
        },
    )
    assert r.status_code == 422, r.json()
    assert "steps[1] (Use)" in r.json()["detail"] and "{{Scna.data}}" in r.json()["detail"]


def test_save_accepts_references_to_steps_inputs_and_trigger(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/workflows",
        json={
            "name": "fine",
            "steps": [
                {"name": "Scan", "kind": "tool", "tool": "read_file", "args": {"path": "x"}},
                {
                    "name": "Tell",
                    "kind": "notify",
                    # a step's data, a step, the reflex Trigger, and a run
                    # INPUT (a def declares none — a bare unknown name is one)
                    "message": "{{Scan.data}} {{Scan}} {{Trigger}} {{Client}}",
                },
            ],
        },
    )
    assert r.status_code == 200, r.json()


# --------------------------------------------------------------------------- #
# AE4 — a parallel group across a "crash": the member that FINISHED must be
# on the record before its sibling finishes, or Resume re-runs it
# (re-delivers a notify, re-writes a tool's files).
# --------------------------------------------------------------------------- #


async def _crash_during_agent_step(platform, orch, engine, wf, *, wait_for_recorded=None):
    """Start ``wf``, park the FIRST agent step forever, then cancel the driver
    (the process-death proxy). With ``wait_for_recorded``, waits (bounded)
    until that step's output reads completed ON THE RECORD before crashing —
    the thing the AE4 tests assert, not a proxy signal. Returns the run id;
    the DB row is still 'running' exactly as a dead daemon would leave it."""
    started = asyncio.Event()

    async def hang(session_id, definition=None):
        started.set()
        await asyncio.Event().wait()

    orch.run_session = hang
    rec = engine.create_record(wf)
    driver = asyncio.create_task(engine.run_record(rec, wf))
    await asyncio.wait_for(started.wait(), 5)
    if wait_for_recorded is not None:
        for _ in range(150):
            outs = _recorded_outputs(platform, rec.id)
            if outs.get(wait_for_recorded, {}).get("status") == "completed":
                break
            await asyncio.sleep(0.02)
    driver.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await driver
    return rec.id


def _parallel_def() -> WorkflowDef:
    return WorkflowDef(
        name="par",
        steps=[
            Step(name="Tell", kind="notify", message="msg-A", group="g"),
            Step(name="Work", kind="agent", task="do it", group="g"),
            Step(name="After", kind="notify", message="msg-C"),
        ],
    )


async def test_parallel_member_that_finished_is_on_the_record_before_the_crash(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    orch = Orchestrator(platform)
    engine = WorkflowEngine(platform, orch)
    run_id = await _crash_during_agent_step(
        platform, orch, engine, _parallel_def(), wait_for_recorded="Tell"
    )
    assert any("msg-A" in m for m in mock.sent), mock.sent  # Tell really ran
    row = _row(platform, run_id)
    assert row.status == "running"  # the crash proxy left it as a dead daemon would
    outs = json.loads(row.outputs_json or "{}")
    assert outs.get("Tell", {}).get("status") == "completed", outs


async def test_resume_after_a_parallel_crash_does_not_redeliver_the_finished_member(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    orch = Orchestrator(platform)
    engine = WorkflowEngine(platform, orch)
    run_id = await _crash_during_agent_step(
        platform, orch, engine, _parallel_def(), wait_for_recorded="Tell"
    )
    assert sum("msg-A" in m for m in mock.sent) == 1, mock.sent
    assert reconcile_interrupted_runs(platform.engine) == 1
    orch.run_session = _completing(orch)
    resumed = await engine.resume_interrupted(_row(platform, run_id))
    assert resumed.status == "completed"
    assert any("msg-C" in m for m in mock.sent)
    # Completed steps are NOT re-run: the finished member was not re-delivered.
    assert sum("msg-A" in m for m in mock.sent) == 1, mock.sent
    outs = json.loads(resumed.outputs_json)
    assert outs["Work"]["status"] == "completed"
    started_tell = [
        e
        for e in platform.event_bus.history
        if e.type == "workflow.step_started" and e.payload.get("step") == "Tell"
    ]
    assert len(started_tell) == 1, "Tell was started again on resume"


async def test_sequential_resume_keeps_prior_outputs_and_data_handoff(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(FakeDataTool(data={"path": "x.txt"}))
    mock = _mock_channel(platform)
    orch = Orchestrator(platform)
    engine = WorkflowEngine(platform, orch)
    wf = WorkflowDef(
        name="seq",
        steps=[
            Step(name="Scan", kind="tool", tool="t1231_data_tool"),
            Step(name="Work", kind="agent", task="use {{Scan.data}}"),
            Step(name="Tell", kind="notify", message="got {{Scan.data}}"),
        ],
    )
    run_id = await _crash_during_agent_step(platform, orch, engine, wf)
    row = _row(platform, run_id)
    outs = json.loads(row.outputs_json)
    assert outs["Scan"]["status"] == "completed"
    dead_session = row.current_session_id
    assert dead_session
    reconcile_interrupted_runs(platform.engine)
    orch.reconcile_interrupted_sessions()
    assert orch.get_session(dead_session).status is SessionStatus.FAILED
    orch.run_session = _completing(orch)
    resumed = await engine.resume_interrupted(_row(platform, run_id))
    assert resumed.status == "completed"
    outs = json.loads(resumed.outputs_json)
    assert outs["Scan"]["status"] == "completed"
    assert outs["Work"]["status"] == "completed"
    assert outs["Work"]["session_id"] != dead_session  # a FRESH session did the step
    assert any('"path": "x.txt"' in m for m in mock.sent), mock.sent  # data handed off
    assert len(json.loads(resumed.session_ids_json)) == 2  # the dead one stays listed


# --------------------------------------------------------------------------- #
# AE16 — the pinned-folder note (v1.225.0) on a RESUMED run
# --------------------------------------------------------------------------- #


def _project(platform, root) -> str:
    with session_scope(platform.engine) as db:
        p = Project(name="Acme", root=str(root))
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id


def _seed_interrupted(platform, *, project_id, notes, steps, outputs=None):
    rec = WorkflowRunRecord(
        workflow_name="pinned",
        status="interrupted",
        project_id=project_id,
        steps_json=dumps([step_to_dict(s) for s in steps]),
        outputs_json=dumps(outputs or {}),
        notes_json=dumps(notes),
        session_ids_json="[]",
        finished_at=utcnow(),
    )
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    return rec


async def test_resumed_run_whose_folder_vanished_says_so(tmp_path):
    platform = build_platform(str(tmp_path))
    root = tmp_path / "client"
    root.mkdir()
    pid = _project(platform, root)
    engine = WorkflowEngine(platform)
    rec = _seed_interrupted(
        platform,
        project_id=pid,
        notes=[],
        steps=[Step(name="Tell", kind="notify", message="hi")],
    )
    root.rename(tmp_path / "moved")  # the folder is gone at resume time
    resumed = await engine.resume_interrupted(rec)
    assert resumed.status == "completed"
    notes = json.loads(resumed.notes_json)
    assert notes and "no folder" in notes[0] and "scratch workspace" in notes[0]


async def test_resume_clears_a_stale_missing_folder_note_once_the_folder_is_back(tmp_path):
    platform = build_platform(str(tmp_path))
    root = tmp_path / "client"
    root.mkdir()
    pid = _project(platform, root)
    engine = WorkflowEngine(platform)
    stale = (
        f"project “Acme” has no folder at {root} any more — its steps "
        "ran in a scratch workspace, NOT in the project folder; update the "
        "folder on the project page and run again"
    )
    rec = _seed_interrupted(
        platform,
        project_id=pid,
        notes=[stale],  # written by the ORIGINAL run while the drive was out
        steps=[Step(name="Tell", kind="notify", message="hi")],
    )
    resumed = await engine.resume_interrupted(rec)
    assert resumed.status == "completed"
    # The folder exists again and every step of THIS resume ran in it — the
    # record must not keep telling the user its files went to a scratch dir.
    assert json.loads(resumed.notes_json) == []


# --------------------------------------------------------------------------- #
# Carried confirmations: parked (waiting) and claimed-but-not-started
# (resuming) rows across the boot reconcile.
# --------------------------------------------------------------------------- #


def test_waiting_run_survives_the_boot_reconcile(tmp_path):
    platform = build_platform(str(tmp_path))
    rec = WorkflowRunRecord(
        workflow_name="gate",
        status="waiting",
        steps_json=dumps([step_to_dict(Step(name="Ask", kind="ask", message="Go?"))]),
        waiting_json=dumps({"index": 0, "step": "Ask", "question": "Go?"}),
    )
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    assert reconcile_interrupted_runs(platform.engine) == 0
    assert _row(platform, rec.id).status == "waiting"


async def test_resuming_claim_killed_by_a_restart_reparks_the_question(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    rec = WorkflowRunRecord(
        workflow_name="gate",
        status="resuming",  # the answer route claimed it; the daemon died next
        steps_json=dumps(
            [
                step_to_dict(Step(name="Ask", kind="ask", message="Go?")),
                step_to_dict(Step(name="Tell", kind="notify", message="sent")),
            ]
        ),
        waiting_json=dumps({"index": 0, "step": "Ask", "question": "Go?"}),
    )
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    assert reconcile_interrupted_runs(platform.engine) == 1
    resumed = await engine.resume_interrupted(_row(platform, rec.id))
    assert resumed.status == "waiting"  # asks again — the answer was never folded
    assert json.loads(resumed.waiting_json)["question"] == "Go?"
