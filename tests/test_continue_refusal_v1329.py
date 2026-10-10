"""v1.329.0 calm chat wave 8 (J5) -- the /continue refusal is plain sentences.

``POST /sessions/{id}/continue`` refused a second follow-up in a busy
workspace with one clause hung on a dash ("a continuation is already running
or queued in this workspace — wait for it to finish before continuing
again"). The calm copy rule has no dash asides; the refusal is now two plain
sentences, and it still says "running or queued" and "wait for it to finish"
(the words older tests and readers match).

Driven through the REAL app factory: a finished parent and a follow-up still
ACTIVE in the same folder, the way a user meets it (press Continue twice).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app

DASHES = ("—", "–")
EXPECTED = (
    "A follow-up is already running or queued in this folder. "
    "Wait for it to finish before continuing again."
)


def _sessions(c: TestClient) -> int:
    return len(c.get("/sessions").json()["sessions"])


def test_a_busy_folder_refuses_a_second_follow_up_in_plain_sentences(tmp_path):
    app = create_app(str(tmp_path))
    folder = tmp_path / "shared-folder"
    folder.mkdir()
    with TestClient(app) as c:
        with session_scope(app.state.platform.engine) as db:
            parent = Session(
                task="tidy the folder", agent_type=AgentType.BUILDER,
                status=SessionStatus.COMPLETED, workspace_path=str(folder),
                provider="mock", model="mock-1",
            )
            busy = Session(
                task="carry on", agent_type=AgentType.BUILDER,
                status=SessionStatus.ACTIVE, workspace_path=str(folder),
                provider="mock", model="mock-1",
            )
            db.add(parent)
            db.add(busy)
            db.commit()
            pid = parent.id
        before = _sessions(c)
        res = c.post(f"/sessions/{pid}/continue", json={"message": "and again"})
        after = _sessions(c)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail == EXPECTED
    assert not any(d in detail for d in DASHES), detail
    # The words other readers match are kept.
    assert "running or queued" in detail
    assert "wait for it to finish" in detail.lower()
    assert after == before, "a refused continue must not start a session"


def test_control_a_free_folder_continues(tmp_path):
    """Anti-vacuity: the same parent with nothing else in its folder is
    accepted, so the 409 above is the busy check and nothing else."""
    app = create_app(str(tmp_path))
    folder = tmp_path / "free-folder"
    folder.mkdir()
    with TestClient(app) as c:
        with session_scope(app.state.platform.engine) as db:
            parent = Session(
                task="tidy the folder", agent_type=AgentType.BUILDER,
                status=SessionStatus.COMPLETED, workspace_path=str(folder),
                provider="mock", model="mock-1",
            )
            db.add(parent)
            db.commit()
            pid = parent.id
        res = c.post(f"/sessions/{pid}/continue", json={"message": "and again", "wait": False})
        if res.status_code == 200:
            c.post(f"/sessions/{res.json()['id']}/cancel")
    assert res.status_code == 200, res.text
