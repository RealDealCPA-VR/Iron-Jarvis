"""Calm UI redesign S8 — the home's one conditional line reads ONE endpoint.

``GET /ui/status-line`` answers numbers only (jobs a restart cut off, failing
background loops and tool packs, work running now) so the chat home never
polls sessions; the detail lives in Everything › Status. A count it cannot
read is zero, never an error.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app


def test_status_line_counts_interrupted_failing_and_running(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    assert client.get("/ui/status-line").json() == {
        "interrupted": 0,
        "failing_loops": 0,
        "failing_packs": 0,
        "running": 0,
    }
    d = app.state.d
    with session_scope(app.state.platform.engine) as db:
        db.add(Session(task="cut off", status=SessionStatus.FAILED, interrupted_at=utcnow()))
        db.add(Session(task="plain failure", status=SessionStatus.FAILED))
        # Cut off once, then continued to completion: no longer offered.
        db.add(Session(task="came back", status=SessionStatus.COMPLETED, interrupted_at=utcnow()))
        db.commit()
    d.loop_health["sampler"] = {"ok": False, "last_error": "boom"}
    d.loop_health["backup"] = {"ok": True}
    d.orchestrator._running["s1"] = object()
    try:
        body = client.get("/ui/status-line").json()
    finally:
        d.orchestrator._running.pop("s1", None)
    assert body["interrupted"] == 1
    assert body["failing_loops"] == 1
    assert body["running"] == 1
    # The same rows /sessions/interrupted offers Continue for.
    assert len(client.get("/sessions/interrupted").json()["sessions"]) == 1


def test_status_line_never_raises(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    d = app.state.d
    monkeypatch.setattr(d, "loop_health", None, raising=False)
    monkeypatch.setattr(d.orchestrator, "_running", None, raising=False)
    r = TestClient(app).get("/ui/status-line")
    assert r.status_code == 200 and r.json()["failing_loops"] == 0
