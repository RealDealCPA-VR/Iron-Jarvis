"""v1.329.0 calm chat wave 7, I1 -- the daemon says its mission refusals plainly.

``POST /missions/{id}/retry-failed`` refused with two clauses joined by a
dash ("this mission is still running — wait for it to finish before
retrying"). The calm copy rule has no dash asides, so the dashboard has
``plainSentences`` turn each dash into a full stop; that stays as a guard for
an OLDER daemon, and this daemon now says it as sentences at the source.

Driven through the REAL app factory and the real orchestrator; the two 409s
are reached the way a user reaches them (a mission still running, and a
follow-up already working in the mission's folder).
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.sessions as sessions_routes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

MISSION = "job:mission"
DASHES = ("\u2014", "\u2013")


class _Quick(MockLLMAdapter):
    async def complete(self, *, system, messages, tools, **kw):
        return LLMResponse(text="# Report\n\nall done", finish_reason="stop")


def _app(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: _Quick())
    return app


def _wait(c: TestClient, sid: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if c.get(f"/sessions/{sid}").json()["session"]["status"] in ("completed", "failed", "cancelled"):
            return
        time.sleep(0.05)
    raise AssertionError(f"session {sid} never finished")


def _count_missions(c: TestClient) -> int:
    return len(c.get("/missions").json()["missions"])


def test_a_running_mission_is_refused_in_plain_sentences(tmp_path):
    """The mission itself is still at work: two sentences, no dash."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        with session_scope(app.state.platform.engine) as db:
            row = Session(
                task="file the 1099s", agent_type=AgentType.SUPERVISOR,
                status=SessionStatus.ACTIVE, origin=MISSION, provider="mock",
                model="mock-1", options_json=json.dumps({"deliverable": True}),
            )
            db.add(row)
            db.commit()
            sid = row.id
        before = _count_missions(c)
        res = c.post(f"/missions/{sid}/retry-failed")
        after = _count_missions(c)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail == "This mission is still running. Wait for it to finish before retrying."
    assert not any(d in detail for d in DASHES), detail
    assert after == before, "a refused retry must not start a mission"


def test_a_follow_up_already_working_is_refused_in_plain_sentences(tmp_path, monkeypatch):
    """A follow-up already holds the mission's folder: two sentences, no
    dash, and nothing is started."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
        _wait(c, sid)
        store = app.state.platform.worklist
        board = store.root_session_for(sid)
        store.add(board, [("a.pdf", "a.pdf"), ("b.pdf", "b.pdf")])
        store.finish(board, "a.pdf", status="done")
        store.finish(board, "b.pdf", status="failed")
        folder = str(tmp_path / "mission-folder")
        # The folder this mission reuses is busy with another run: the same
        # two helpers the route asks, answered as a live follow-up answers.
        monkeypatch.setattr(sessions_routes, "_managed_reuse_path", lambda d, prev: folder)
        monkeypatch.setattr(sessions_routes, "_folder_busy", lambda d, ws: ws == folder)
        before = _count_missions(c)
        res = c.post(f"/missions/{sid}/retry-failed")
        after = _count_missions(c)
        items = {i.key: i.status for i in store.items(board)}
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail == (
        "A follow-up of this mission is already running or queued. "
        "Wait for it to finish before retrying."
    )
    assert not any(d in detail for d in DASHES), detail
    assert after == before, "a refused retry must not start a mission"
    assert items["b.pdf"] == "failed", "a refused retry must not re-open anything"


def test_the_route_holds_no_dash_aside_in_any_refusal():
    """Every ``detail=`` string the retry route can answer is free of a
    spaced dash (read off the route's own source)."""
    import inspect
    import re

    src = inspect.getsource(sessions_routes.register)
    start = src.index('"/missions/{session_id}/retry-failed"')
    body = src[start:src.index("@app.", start + 10)]
    details = re.findall(r'detail=(\(?\s*(?:f?"[^"]*"\s*)+\)?)', body)
    assert len(details) >= 5, details  # anti-vacuity: the route's refusals were read
    for d in details:
        assert not re.search("\\s[\u2014\u2013]\\s", d), d
