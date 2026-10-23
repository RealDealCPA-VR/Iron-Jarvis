"""v1.374.0 (IJ harness part 2, Phase 3): the task list a reply keeps.

* a build turn's reply line keeps the turn's OWN task list as its last
  ``plan_update`` left it (``plan`` — the chat checklist's saved form), the
  list the screen folds under the reply — driven through the real lane with
  Claude Code's ``TodoWrite`` (the dialect maps it onto ``plan_update``);
* a helper whose run PLANS its work (the decomposed lane) sends its plan on
  the row's ``helper`` frames each time its run moves on (a phase), and the
  reply keeps it settled (the daemon's own copy, read once more at the end).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, RunPlan, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import helpers as H
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import Script, messages_of, new_session, open_turn, wire


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _todos(a: str, b: str) -> dict:
    return {"todos": [
        {"content": "Read app.py", "status": a, "activeForm": "Reading app.py"},
        {"content": "Fix the total", "status": b, "activeForm": "Fixing the total"},
    ]}


async def test_the_reply_keeps_the_turns_task_list_as_its_last_plan_update_left_it(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([
        [ToolCall(id="c1", name="TodoWrite", arguments=_todos("in_progress", "pending"))],
        [ToolCall(id="c2", name="TodoWrite", arguments=_todos("completed", "in_progress"))],
        "Read it; the fix is next.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "fix the total", "turn_id": "bt-plan"})
    assert status == 200, raw
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant" and m.get("turnId") == "bt-plan"][-1]
    assert reply["plan"] == {
        "steps": [{"goal": "Read app.py", "state": "done"}, {"goal": "Fix the total", "state": "running"}],
        "total": 2,
    }


async def test_a_turn_with_no_plan_keeps_none(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script(["Hello."]))
    status, _ = await open_turn(app, sid, {"text": "hi", "turn_id": "bt-noplan"})
    assert status == 200
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant"][-1]
    assert "plan" not in reply


# --------------------------------------------------------------------------- #
# a helper's own plan (the decomposed lane)
# --------------------------------------------------------------------------- #


def _planned_helper(app, *, done: int, status: SessionStatus = SessionStatus.ACTIVE,
                    state: AgentState = AgentState.RUNNING) -> str:
    """A helper session whose run planned three steps and finished ``done``."""
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        sess = Session(task="search", status=status)
        db.add(sess)
        db.commit()
        sid = sess.id
        run = AgentRun(session_id=sid, state=state)
        db.add(run)
        db.commit()
        db.add(RunPlan(
            run_id=run.id, session_id=sid,
            plan_json=json.dumps([{"goal": g} for g in ("list the files", "read each one", "write it up")]),
            results_json=json.dumps([{"ok": True} for _ in range(done)]),
        ))
        db.commit()
    return sid


async def test_a_helpers_plan_rides_its_row_and_ticks_as_its_run_moves_on(app):
    child = _planned_helper(app, done=1)
    sent: list[dict] = []
    call = H.HelperCall(call_id="h1", agent="researcher", task="search",
                        emit=lambda event, data: sent.append(data), session_id="build:s1")
    call.child = child
    call._platform = app.state.platform
    # A phase is where a planned run moves: its checklist is read again.
    call._take({"event": "phase", "data": {"phase": "running", "detail": "step 2 of 3: read each one"}})
    assert call._plan_due is True
    await call._refresh_plan()
    plans = [f["plan"] for f in sent if "plan" in f]
    assert plans == [{"steps": [
        {"goal": "list the files", "state": "done"},
        {"goal": "read each one", "state": "running"},
        {"goal": "write it up", "state": "waiting"},
    ], "total": 3}]
    # The same plan again is not sent twice.
    await call._refresh_plan()
    assert len([f for f in sent if "plan" in f]) == 1


async def test_the_reply_line_reads_each_helpers_plan_once_more_settled(app):
    child = _planned_helper(app, done=2, status=SessionStatus.COMPLETED, state=AgentState.COMPLETED)
    lines = [{"role": "assistant", "content": "x", "helpers": [
        {"id": "h1", "session": child, "state": "done",
         "plan": {"steps": [{"goal": "list the files", "state": "running"}], "total": 3}},
        {"id": "h2", "state": "stopped"},  # never started: no plan to read
    ]}]
    out = await asyncio.to_thread(build_routes._with_helper_plans, app.state.platform.engine, lines)
    h1, h2 = out[0]["helpers"]
    # The run is over: the daemon's own copy, settled — never the stale row.
    assert h1["plan"] == {"steps": [
        {"goal": "list the files", "state": "done"},
        {"goal": "read each one", "state": "done"},
        {"goal": "write it up", "state": "skipped"},
    ], "total": 3}
    assert "plan" not in h2


def test_a_helper_frame_plan_is_kept_on_the_reply_watch():
    watch = build_routes._ReplyWatch()
    plan = {"steps": [{"goal": "a", "state": "running"}], "total": 1}
    watch.see("helper", json.dumps({"id": "h1", "agent": "researcher", "state": "running", "plan": plan}))
    assert watch.helpers["h1"]["plan"] == plan
