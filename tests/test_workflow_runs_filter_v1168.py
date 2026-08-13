"""GET /workflows/runs — the v1.168.0 additive `status` + `slim` params.

The notification bell polls this route from EVERY page. Before the filter, it
fetched a newest-first page and filtered client-side — chunk-blind: a parked
(waiting) run older than the page silently fell out of the very badge that
promises to count it. And every poll carried steps_json/outputs_json (all
step outputs) app-wide. `status` filters server-side; `slim` drops the heavy
blobs while keeping waiting_json (the question the bell renders).
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

from fastapi.testclient import TestClient

from iron_jarvis.core.db import dumps, session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.models import WorkflowRunRecord


def _seed(client, status: str, name: str, waiting: dict | None = None) -> str:
    rec = WorkflowRunRecord(
        workflow_name=name,
        status=status,
        steps_json=dumps([{"name": "Approve", "kind": "ask"}]),
        outputs_json=dumps({"Approve": "big blob " * 50}),
        session_ids_json="[]",
        waiting_json=dumps(waiting) if waiting else "",
    )
    with session_scope(client.app.state.platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def test_status_filter_returns_only_that_status(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _seed(client, "completed", "done-one")
    parked = _seed(
        client, "waiting", "gated", {"index": 0, "step": "Approve", "question": "Go?"}
    )
    _seed(client, "running", "live-one")

    body = client.get("/workflows/runs", params={"status": "waiting"}).json()
    assert [r["id"] for r in body["runs"]] == [parked]
    assert body["runs"][0]["status"] == "waiting"
    # The question the bell renders survives.
    assert "Go?" in body["runs"][0]["waiting_json"]


def test_slim_drops_heavy_blobs_but_keeps_waiting_json(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _seed(client, "waiting", "gated", {"index": 0, "step": "Approve", "question": "Go?"})

    slim = client.get(
        "/workflows/runs", params={"status": "waiting", "slim": "true"}
    ).json()["runs"][0]
    assert "steps_json" not in slim and "outputs_json" not in slim
    assert "Go?" in slim["waiting_json"]  # the one blob the bell needs

    # Defaults unchanged: without slim, the full record still flows.
    full = client.get("/workflows/runs").json()["runs"][0]
    assert "steps_json" in full and "outputs_json" in full


def test_no_params_behaves_exactly_as_before(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a = _seed(client, "completed", "one")
    b = _seed(client, "running", "two")
    body = client.get("/workflows/runs").json()
    assert {r["id"] for r in body["runs"]} == {a, b}  # unfiltered
