"""Readiness 12A (0f.3) — a scheduled workflow STOPPED mid-run reads
Stopped on its Schedules row, never "ok — workflow ran".

THE LEFTOVER (docs/PRODUCT-READINESS-RESULTS.md 0f.3, "Cancelled scheduled
workflow"): ``platform._dispatch_scheduled``'s workflow branch handled a run
that FAILED (raise → "error") and one PARKED on the user ("needs_you"), and
fell through to ``detail = "workflow “X” ran"`` with no outcome for every
other status — so a run a Cancel on the Workflows page (or Emergency stop)
ended mid-way read a green "ok" and "workflow “X” ran", and was announced
"done" on every destination.

NOW it records ``cancelled`` with ``run_outcome.workflow_cancelled_detail``
and — the stop being the user's — delivers nothing, exactly like a stopped
task fire.

Driven through the REAL app factory and the REAL workflow engine: the run's
first step is a read-only tool that asks its own run to stop (what the
cancel route writes: ``status = "cancelling"``); the engine sees it before
the next step, as it does for a real Cancel press.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.models import WorkflowRunRecord
from tests.test_schedule_outcomes_v13510 import _mock_channel, _schedule_messages

_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = _ROOT / "dashboard" / "__tests__" / "fixtures" / "cancelled-scheduled-workflow-v1387.json"


class _StopsItsRun(Tool):
    """A read-only step that presses Cancel on its own run (the cancel route's
    write), so the engine stops before the next step."""

    name = "stop_this_run_fake"
    description = "fake: ask the workflow run to stop"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, engine) -> None:
        self._engine = engine

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        with session_scope(self._engine) as db:
            rec = db.get(WorkflowRunRecord, ctx.session_id)
            assert rec is not None, "the step runs inside its own run"
            rec.status = "cancelling"
            db.add(rec)
            db.commit()
        return ToolResult(ok=True, output="asked to stop")


def _fire(tmp_path) -> dict:
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    platform.registry.register(_StopsItsRun(platform.engine))
    r = client.post(
        "/workflows",
        json={
            "name": "nightly-tidy",
            "steps": [
                {"name": "Stop", "kind": "tool", "tool": "stop_this_run_fake", "args": {}},
                {"name": "Never", "kind": "tool", "tool": "list_folder", "args": {"path": str(tmp_path)}},
            ],
        },
    )
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "tidy", "cron": "0 3 * * *", "kind": "workflow", "payload": {"workflow": "nightly-tidy"}},
    )
    assert r.status_code == 200, r.text
    mock = _mock_channel(app)
    before = len(mock.sent)
    body = client.post("/schedules/tidy/run").json()
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_name == "nightly-tidy")))
        statuses = [r.status for r in runs]
        outputs = [json.loads(r.outputs_json or "{}") for r in runs]
    return {"body": body, "sent": _schedule_messages(mock, before, "tidy"), "statuses": statuses, "outputs": outputs}


def test_a_scheduled_workflow_stopped_mid_run_reads_stopped_never_ok(tmp_path):
    got = _fire(tmp_path)
    # Anti-vacuity: the REAL engine really stopped the run before step 2.
    assert got["statuses"] == ["cancelled"], got
    assert got["outputs"][0].get("Never", {}).get("status") == "skipped", got["outputs"]
    body = got["body"]
    # RED on the old code: ("ok", "workflow “nightly-tidy” ran").
    assert body["last_status"] == ro.STATUS_CANCELLED, body
    assert body["last_detail"] == "Your workflow “nightly-tidy” was stopped before it finished.", body
    assert "ran" not in body["last_detail"]
    # The stop was the user's: nothing is announced (a stopped task fire's rule).
    assert got["sent"] == [], got["sent"]


def test_the_contract_fixture_is_the_real_daemons_row(tmp_path):
    body = _fire(tmp_path)["body"]
    data = {
        "about": (
            "Readiness 12A (0f.3): the REAL daemon's Schedules row after a scheduled workflow was "
            "stopped mid-run. Written by tests/test_cancelled_scheduled_workflow_v1387.py "
            "(IJ_REGEN_CANCELLED_WORKFLOW=1); read by dashboard/__tests__/engine-room-words-v1387.test.tsx."
        ),
        "row": {"name": "tidy", "last_status": body["last_status"], "last_detail": body["last_detail"]},
    }
    if os.environ.get("IJ_REGEN_CANCELLED_WORKFLOW") == "1":
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == data


def test_the_sentence_passes_the_retired_word_scan():
    from tests.test_vocabulary_beyond_dashboard_v13580 import _hits

    assert _hits(ro.workflow_cancelled_detail("x")) == []
    assert ro.workflow_cancelled_detail("") == "Your workflow “inline” was stopped before it finished."
