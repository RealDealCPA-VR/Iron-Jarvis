"""v1.310.0 (wave 2, track D) — wave-1 mission carry-overs, backend half.

Reviewer notes from v1.309.0, each pinned through the REAL app factory
(``create_app`` + ``TestClient`` on an isolated ``tmp_path`` home) with
scripted adapters only:

W2-5  ``GET /chat/approvals/pending`` rows carry additive ``mission_id`` (the
      mission ROOT session id when the asking session is a mission or a
      mission teammate, via ``agents/team.mission_root``; else null) and
      ``project_id`` (the asking session's project, else its mission root's;
      else null) — so the bell can open the mission and the user answers the
      ask IN CONTEXT. Never args (the v1.200.0 hygiene rule still holds).

retry  ``POST /missions/{id}/retry-failed`` starts a rerun with the SAME task
      (the worklist board is keyed by it), so nothing told the MODEL that only
      the failed items remain — it was handed the whole original job again.
      The retry run's model input must carry one short model-facing line that
      only the failed items remain; the user-facing objective stays "Retry the
      N failed items: <objective>" and the task stays byte-identical.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    EventRecord,
    Session,
    SessionStatus,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

MISSION = "job:mission"
MEMBER = "job:mission-member"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _Quick(MockLLMAdapter):
    async def complete(self, *, system, messages, tools, **kw):
        return LLMResponse(text="# Report\n\nall done", finish_reason="stop")


def _app(tmp_path, adapter=_Quick):
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: adapter())
    return app


def _wait(c: TestClient, sid: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = c.get(f"/sessions/{sid}").json()["session"]
        if row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"session {sid} never finished")


def _seed_mission(engine, *, project_id: str | None = None) -> tuple[str, str]:
    """A mission root (+ its coordinator run) and ONE teammate linked by
    ``AgentRun.parent_id`` exactly as ``delegate`` links it. The teammate has
    no project of its own (children never inherit run options)."""
    with session_scope(engine) as db:
        root = Session(
            task="file the 1099s", agent_type=AgentType.SUPERVISOR,
            status=SessionStatus.ACTIVE, origin=MISSION, project_id=project_id,
            provider="mock", model="mock-1",
            options_json=json.dumps({"deliverable": True}),
        )
        db.add(root)
        db.commit()
        root_id = root.id
        root_run = AgentRun(
            session_id=root_id, agent_type=AgentType.SUPERVISOR, provider="mock",
            model="mock-1", state=AgentState.RUNNING,
        )
        db.add(root_run)
        db.commit()
        member = Session(
            task="read b.pdf", agent_type=AgentType.BUILDER, agent_name="builder",
            status=SessionStatus.ACTIVE, origin=MEMBER, provider="mock", model="mock-1",
        )
        db.add(member)
        db.commit()
        member_id = member.id
        db.add(AgentRun(
            session_id=member_id, parent_id=root_run.id, agent_type=AgentType.BUILDER,
            provider="mock", model="mock-1", state=AgentState.WAITING, steps=1,
        ))
        db.commit()
    return root_id, member_id


def _seed_plain(engine, *, project_id: str | None) -> str:
    with session_scope(engine) as db:
        s = Session(
            task="rename my files", agent_type=AgentType.BUILDER,
            status=SessionStatus.ACTIVE, origin="job:agents", project_id=project_id,
        )
        db.add(s)
        db.commit()
        return s.id


def _ask(platform, session_id: str, tool: str = "shell") -> str:
    """A REAL pending ask in the platform's one approvals registry (the way the
    runtime's pause files it) AND its announcing ``approval.requested`` row —
    the listing shows only announced asks. The payload carries args on
    purpose: the listing must still never echo them."""

    async def _go() -> str:
        approval_id, _fut = platform.approvals.request(tool, {}, session_id=session_id)
        return approval_id

    loop = asyncio.new_event_loop()
    try:
        aid = loop.run_until_complete(_go())
    finally:
        loop.close()
    with session_scope(platform.engine) as db:
        db.add(EventRecord(
            id=new_id("evt"), type=str(getattr(EventType.APPROVAL_REQUESTED, "value", EventType.APPROVAL_REQUESTED)),
            session_id=session_id,
            payload_json=json.dumps({
                "approval_id": aid, "tool": tool,
                "args": {"command": "echo SECRET-TOKEN-123"},
            }),
            created_at=utcnow(),
        ))
        db.commit()
    return aid


def _listed(c: TestClient) -> dict[str, dict]:
    res = c.get("/chat/approvals/pending")
    assert res.status_code == 200, res.text
    return {row["id"]: row for row in res.json()["approvals"]}


# --------------------------------------------------------------------------- #
# W2-5 — a pending ask names its mission and its project
# --------------------------------------------------------------------------- #


def test_a_teammates_ask_names_the_mission_root_and_its_project(tmp_path):
    """The bell's "Open the mission" needs the mission ROOT (the teammate is
    not a mission of its own) and the project the mission screen lives in —
    the teammate has no project of its own, so it is the root's."""
    app = _app(tmp_path)
    p = app.state.platform
    with TestClient(app) as c:
        root, member = _seed_mission(p.engine, project_id="proj_m")
        aid = _ask(p, member)
        rows = _listed(c)
    assert aid in rows, rows
    row = rows[aid]
    assert row["session_id"] == member, row
    assert row.get("mission_id") == root, f"mission_id must be the ROOT: {row}"
    assert row.get("project_id") == "proj_m", row
    assert "args" not in row and "SECRET-TOKEN-123" not in json.dumps(row), row


def test_the_coordinators_own_ask_names_its_own_mission(tmp_path):
    app = _app(tmp_path)
    p = app.state.platform
    with TestClient(app) as c:
        root, _member = _seed_mission(p.engine, project_id=None)
        aid = _ask(p, root)
        row = _listed(c)[aid]
    assert row.get("mission_id") == root, row
    assert "project_id" in row and row["project_id"] is None, (
        f"project_id is additive and present (null) on every row: {row}"
    )


def test_control_a_plain_jobs_ask_has_no_mission(tmp_path):
    """Anti-vacuity: a job that is not a mission gets ``mission_id: null`` —
    the key is present (additive) but never invents a mission — and its own
    project when it has one."""
    app = _app(tmp_path)
    p = app.state.platform
    with TestClient(app) as c:
        in_project = _seed_plain(p.engine, project_id="proj_p")
        loose = _seed_plain(p.engine, project_id=None)
        a1 = _ask(p, in_project)
        a2 = _ask(p, loose)
        rows = _listed(c)
    assert "mission_id" in rows[a1] and rows[a1]["mission_id"] is None, rows[a1]
    assert rows[a1].get("project_id") == "proj_p", rows[a1]
    assert "mission_id" in rows[a2] and rows[a2]["mission_id"] is None, rows[a2]
    assert "project_id" in rows[a2] and rows[a2]["project_id"] is None, rows[a2]


# --------------------------------------------------------------------------- #
# retry-failed — the MODEL is told only the failed items remain
# --------------------------------------------------------------------------- #


class _Recorder(MockLLMAdapter):
    """Answers at once and records every model input (system + messages)."""

    seen: list[str] = []

    async def complete(self, *, system, messages, tools, **kw):
        parts = [str(system or "")]
        for m in messages:
            content = getattr(m, "content", "")
            parts.append(content if isinstance(content, str) else json.dumps(content, default=str))
        _Recorder.seen.append("\n".join(parts))
        return LLMResponse(text="# Report\n\nall done", finish_reason="stop")


def _board_with(app, sid: str, statuses: dict[str, str]) -> str:
    store = app.state.platform.worklist
    board = store.root_session_for(sid)
    store.add(board, [(k, k) for k in statuses])
    for key, status in statuses.items():
        if status != "pending":
            store.finish(board, key, status=status)
    return board


def _lines(texts: list[str]) -> set[str]:
    return {ln.strip() for t in texts for ln in t.splitlines() if ln.strip()}


def test_retry_failed_tells_the_model_only_the_failed_items_remain(tmp_path):
    """The rerun keeps the task (the board key), so the model was handed the
    whole original job with no word that most of it is done. The retry run's
    model input must carry a NEW line (absent from the first run's input)
    saying only the failed items remain - while the task stays identical and
    the user-facing objective stays "Retry the N failed items: ..."."""
    _Recorder.seen = []
    app = _app(tmp_path, _Recorder)
    engine = app.state.platform.engine
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
        _wait(c, sid)
        first_inputs = list(_Recorder.seen)
        assert first_inputs, "the first run never reached the model"
        _board_with(app, sid, {"a.pdf": "done", "b.pdf": "failed", "c.pdf": "failed"})
        res = c.post(f"/missions/{sid}/retry-failed")
        assert res.status_code in (200, 201), res.text
        rid = res.json()["id"]
        _wait(c, rid)
        retry_inputs = _Recorder.seen[len(first_inputs):]
        view = c.get(f"/sessions/{rid}/mission").json()
        with session_scope(engine) as db:
            first_task = db.get(Session, sid).task
            retry_task = db.get(Session, rid).task
    assert retry_inputs, "the retry never reached the model"
    new_lines = _lines(retry_inputs) - _lines(first_inputs)
    told = [ln for ln in new_lines if "only" in ln.lower() and "failed" in ln.lower()]
    assert told, (
        "the retry's model input never says only the failed items remain; "
        f"lines new to the retry: {sorted(new_lines)[:20]}"
    )
    assert retry_task == first_task, "the task is the worklist board's key: it must not change"
    assert view["session"].get("objective") == "Retry the 2 failed items: file the 1099s", (
        view["session"].get("objective")
    )


# --------------------------------------------------------------------------- #
# the retry note is bound to the retry row — a rerun / continue never repeats it
# --------------------------------------------------------------------------- #


RETRY_MARK = "THIS RUN IS A RETRY"


def _retry_of(c: TestClient, app) -> tuple[str, int]:
    """A finished mission, a board with one failed item, and its finished
    retry run. Returns the retry's id and how many model inputs were seen so
    far (the rerun / continue inputs are the ones after it)."""
    sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
    _wait(c, sid)
    _board_with(app, sid, {"a.pdf": "done", "b.pdf": "failed"})
    res = c.post(f"/missions/{sid}/retry-failed")
    assert res.status_code in (200, 201), res.text
    rid = res.json()["id"]
    _wait(c, rid)
    assert any(RETRY_MARK in t for t in _Recorder.seen), "anti-vacuity: the retry itself carries the note"
    return rid, len(_Recorder.seen)


def test_run_it_again_of_a_retry_does_not_repeat_the_retry_note(tmp_path):
    """``orchestrator.rerun_session`` copies the stored run options verbatim
    (it never goes through ``_stamp_continuation``), so ``retry_failed`` rides
    onto the rerun. Only ``mission_retry_note``'s bound-to-its-own-row check
    keeps "only the N failed items remain" out of a plain "Run it again" —
    which has no such claim to make. Mutation-checked: drop the id check and
    this goes red."""
    _Recorder.seen = []
    app = _app(tmp_path, _Recorder)
    with TestClient(app) as c:
        rid, n = _retry_of(c, app)
        res = c.post(f"/sessions/{rid}/rerun", params={"wait": "false"})
        assert res.status_code in (200, 201), res.text
        rr = res.json()["id"]
        assert rr != rid
        _wait(c, rr)
        after = _Recorder.seen[n:]
    assert after, "the rerun never reached the model"
    assert not any(RETRY_MARK in t for t in after), "a rerun of a retry inherited the retry note"


def test_a_follow_up_on_a_retry_drops_the_retry_stamp(tmp_path):
    """A follow-up ("Continue") on a retry is a new job with its own task:
    ``_stamp_continuation`` pops the carried ``retry_failed`` stamp off the
    continuation's options, and the model is never told it is a retry."""
    _Recorder.seen = []
    app = _app(tmp_path, _Recorder)
    engine = app.state.platform.engine
    with TestClient(app) as c:
        rid, n = _retry_of(c, app)
        res = c.post(f"/sessions/{rid}/continue", json={"message": "now email the client"})
        assert res.status_code in (200, 201), res.text
        cid = res.json()["id"]
        assert cid != rid
        _wait(c, cid)
        after = _Recorder.seen[n:]
        with session_scope(engine) as db:
            opts = json.loads(db.get(Session, cid).options_json or "{}")
    assert after, "the follow-up never reached the model"
    assert not any(RETRY_MARK in t for t in after), "a follow-up on a retry inherited the retry note"
    assert "retry_failed" not in opts, f"the carried retry stamp must be dropped: {opts}"
