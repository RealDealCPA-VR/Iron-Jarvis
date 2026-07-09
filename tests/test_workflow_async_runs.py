"""Async workflow runs + honest lifecycle (T-A round).

``POST /workflows/run`` must return IMMEDIATELY with a ``running`` record and
execute in the background; each step updates the same record; later steps get
prior steps' summaries chained into their task; zero-step runs 400; saved
workflows can be deleted; and a crash-interrupted ``running`` row reconciles to
``interrupted`` on boot.
"""

from __future__ import annotations

import json
import time

# Register the WorkflowRunRecord table on SQLModel.metadata before a platform is
# built (create_app -> init_db creates the tables). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.models import (
    WorkflowRunRecord,
    reconcile_interrupted_runs,
)


def _poll_run(client, run_id, seconds=20):
    """Poll a run to a terminal status (mock provider is fast)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        rec = client.get(f"/workflows/runs/{run_id}").json()
        if rec.get("status") in ("completed", "failed", "cancelled", "interrupted"):
            return rec
        time.sleep(0.1)
    return client.get(f"/workflows/runs/{run_id}").json()


def test_run_returns_immediately_then_completes_in_background(tmp_path):
    # `with` keeps the app loop alive so the _spawn_bg run actually executes.
    with TestClient(create_app(str(tmp_path))) as client:
        body = {
            "name": "two-step",
            "steps": [
                {"name": "s1", "agent": "builder", "task": "first thing"},
                {"name": "s2", "agent": "builder", "task": "second thing"},
            ],
        }
        r = client.post("/workflows/run", json=body)
        assert r.status_code == 200
        rec = r.json()
        # Returns AT ONCE as a running record with the ordered step plan.
        assert rec["status"] == "running"
        assert rec["finished_at"] is None
        steps = json.loads(rec["steps_json"])
        assert [s["name"] for s in steps] == ["s1", "s2"]

        final = _poll_run(client, rec["id"])
        assert final["status"] == "completed"
        assert final["finished_at"]
        outputs = json.loads(final["outputs_json"])
        assert set(outputs) == {"s1", "s2"}
        for name in ("s1", "s2"):
            assert "summary" in outputs[name]
            assert outputs[name]["session_id"]


def test_later_step_receives_earlier_step_context(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        body = {
            "name": "chained",
            "steps": [
                {"name": "gather", "agent": "builder", "task": "gather facts"},
                {"name": "summarize", "agent": "builder", "task": "summarize them"},
            ],
        }
        run_id = client.post("/workflows/run", json=body).json()["id"]
        final = _poll_run(client, run_id)
        assert final["status"] == "completed"
        outputs = json.loads(final["outputs_json"])
        # The second step's session task must carry the chained context block.
        sid = outputs["summarize"]["session_id"]
        sess = client.get(f"/sessions/{sid}").json()["session"]
        assert "# Context from earlier steps" in sess["task"]
        assert "## gather" in sess["task"]


def test_zero_step_run_is_rejected(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/workflows/run", json={"name": "empty", "steps": []})
        assert r.status_code == 400


def test_delete_workflow_and_404(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        client.post(
            "/workflows",
            json={"name": "saved", "steps": [{"name": "s", "agent": "builder", "task": "t"}]},
        )
        r = client.delete("/workflows/saved")
        assert r.status_code == 200 and r.json() == {"deleted": "saved"}
        # Second delete: gone -> 404.
        assert client.delete("/workflows/saved").status_code == 404


def test_reconcile_interrupted_runs_flips_running_row(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        db.add(WorkflowRunRecord(id="wfrun_ghost", workflow_name="w", status="running"))
        db.commit()

    marked = reconcile_interrupted_runs(engine)
    assert marked == 1
    with session_scope(engine) as db:
        row = db.get(WorkflowRunRecord, "wfrun_ghost")
    assert row.status == "interrupted"
    assert row.finished_at is not None
