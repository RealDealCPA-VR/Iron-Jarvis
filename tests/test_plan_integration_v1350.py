"""v1.350.0 integrate — the joins between lane C1 (the dashboard's plan
checklist) and lane C2 (GET /sessions/{id}/plan and the optional plan
approval), and the follow-ups both lanes' reviewers left.

* A session's plan is its NEWEST run's: a run that made no plan (yet) never
  shows an older run's plan as its own (``transcript.latest_plan``).
* ``approve_plan`` is the daemon's own ask, so no custom tool may take the
  name: the runtime would give such a call the plan ask's rules and every card
  would word a command as "follow this plan".
* The pending listing carries ``once_only`` (a FLAG, never args), so a page
  that reloads mid-ask offers the same two buttons the live event did.
* The mission screen and the desktop toast say the plan ask in words.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.agents import transcript as tr
from iron_jarvis.agents.mission import mission_view
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import PLAN_APPROVAL_TOOL
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools import dynamic
from tests.test_desktop_reliability_v1249 import _asks, requires_node
from tests.test_plan_checklist_v1350 import _seed
from tests.test_ux_wave4_desktop_words_v1316 import _tool_words

_THREE = ["read the ledger", "fix the totals", "write the note"]


# --------------------------------------------------------------------------- #
# 1. the newest run's plan, or none
# --------------------------------------------------------------------------- #
def _older(engine, run_id: str, minutes: int = 5) -> None:
    with session_scope(engine) as db:
        row = db.get(AgentRun, run_id)
        row.created_at = row.created_at - timedelta(minutes=minutes)
        db.add(row)
        db.commit()


def test_a_newer_run_without_a_plan_hides_the_older_runs_plan(tmp_path):
    """A run restarted under the same session id (the rare same-id rerun)
    has not planned yet: the checklist route answers 404, never the previous
    run's plan read as this one's (it would show live and be stamped on the
    hand-off)."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    sid, old = _seed(engine, status=SessionStatus.ACTIVE, run_state=AgentState.FAILED,
                     goals=_THREE, results=[])
    _older(engine, old)
    client = TestClient(app)
    # Anti-vacuity: with only the planned run, the plan is there.
    assert client.get(f"/sessions/{sid}/plan").status_code == 200
    with session_scope(engine) as db:
        newer = AgentRun(session_id=sid, state=AgentState.RUNNING)
        db.add(newer)
        db.commit()
    r = client.get(f"/sessions/{sid}/plan")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "No plan for this session: its run has not split the work into steps."
    assert tr.latest_plan(engine, sid) is None


def test_two_runs_in_one_clock_tick_never_hide_a_plan(tmp_path):
    """Strictly NEWER only: a second row stamped in the same tick as the
    planned run (Windows' 15.6 ms clock) leaves the plan shown."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    sid, rid = _seed(engine, status=SessionStatus.ACTIVE, run_state=AgentState.RUNNING,
                     goals=_THREE, results=[])
    with session_scope(engine) as db:
        planned = db.get(AgentRun, rid)
        twin = AgentRun(session_id=sid, state=AgentState.RUNNING, created_at=planned.created_at)
        db.add(twin)
        db.commit()
    body = TestClient(app).get(f"/sessions/{sid}/plan").json()
    assert body["run_id"] == rid
    assert [s["goal"] for s in body["steps"]] == _THREE


def test_an_older_run_without_a_plan_leaves_the_newest_plan_shown(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    sid, rid = _seed(engine, status=SessionStatus.ACTIVE, run_state=AgentState.RUNNING,
                     goals=_THREE, results=[])
    with session_scope(engine) as db:
        earlier = AgentRun(session_id=sid, state=AgentState.FAILED)
        db.add(earlier)
        db.commit()
        earlier_id = earlier.id
    _older(engine, earlier_id)
    body = TestClient(app).get(f"/sessions/{sid}/plan").json()
    assert body["run_id"] == rid
    assert [s["state"] for s in body["steps"]] == ["running", "waiting", "waiting"]


# --------------------------------------------------------------------------- #
# 2. approve_plan is reserved
# --------------------------------------------------------------------------- #
def test_the_plan_asks_name_is_reserved_against_custom_tools():
    assert PLAN_APPROVAL_TOOL in dynamic.RESERVED_TOOL_NAMES


def test_neither_creation_door_makes_a_custom_tool_named_approve_plan(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    client = TestClient(app)
    # A program that exists on every machine the suite runs on, so the
    # "not installed" check passes and the NAME is what is refused.
    import sys

    program = sys.executable
    r = client.post("/tools/custom", json={"name": "approve_plan", "command": [program, "-V"]})
    assert r.status_code == 400, r.text
    assert "keeps for its own approvals" in r.json()["detail"]
    tool = dynamic.ToolCreateTool(platform)
    out = asyncio.run(
        tool.execute({"name": "approve_plan", "command": [program, "-V"]}, SimpleNamespace(session_id="s"))
    )
    assert out.ok is False and "keeps for its own approvals" in (out.error or "")
    assert platform.registry.get("approve_plan") is None
    # Anti-vacuity: the same door makes a tool of any other name.
    ok = client.post("/tools/custom", json={"name": "approve_plans", "command": [program, "-V"]})
    assert ok.status_code == 200, ok.text
    assert platform.registry.get("approve_plans") is not None


# --------------------------------------------------------------------------- #
# 3. the listing carries once_only, never args
# --------------------------------------------------------------------------- #
def test_the_pending_listing_says_once_only_for_the_plan_ask_and_nothing_else(tmp_path):
    """Filed exactly as ``runtime._pause_for_approval`` files one: the
    registry, then the matching approval.requested event."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    client = TestClient(app)

    async def scenario():
        plan_id, _f1 = platform.approvals.request(
            PLAN_APPROVAL_TOOL, {"steps": ["SECRET-GOAL-ONE"]}, session_id="s-plan"
        )
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": plan_id, "tool": PLAN_APPROVAL_TOOL, "args": {"steps": ["SECRET-GOAL-ONE"]},
             "timeout_s": 0, "can_always": False, "once_only": True},
            session_id="s-plan",
        )
        shell_id, _f2 = platform.approvals.request("shell", {"command": "dir"}, session_id="s-shell")
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": shell_id, "tool": "shell", "args": {"command": "dir"}, "timeout_s": 0},
            session_id="s-shell",
        )
        r = client.get("/chat/approvals/pending")
        platform.approvals.pop(plan_id)  # tidy: no unresolved future left behind
        platform.approvals.pop(shell_id)
        return plan_id, shell_id, r

    plan_id, shell_id, r = asyncio.run(scenario())
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()["approvals"]}
    assert rows[plan_id]["once_only"] is True
    assert rows[plan_id]["tool"] == PLAN_APPROVAL_TOOL
    assert "once_only" not in rows[shell_id]  # an ordinary ask is unchanged
    assert "SECRET-GOAL-ONE" not in r.text  # still never args


# --------------------------------------------------------------------------- #
# 4. the words: mission screen, desktop toast
# --------------------------------------------------------------------------- #
def test_the_mission_screen_says_the_plan_ask_in_words(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    root = asyncio.run(
        Orchestrator(p).create_session("objective", AgentType.SUPERVISOR, provider="mock", origin="job:mission")
    )
    asyncio.run(p.event_bus.publish(
        EventType.APPROVAL_REQUESTED, {"approval_id": "a1", "tool": PLAN_APPROVAL_TOOL, "args": {}},
        session_id=root.id,
    ))
    asyncio.run(p.event_bus.publish(
        EventType.APPROVAL_REQUESTED, {"approval_id": "a2", "tool": "write_file", "args": {}},
        session_id=root.id,
    ))
    texts = [line["text"] for line in mission_view(p, root.id)["activity"]]
    assert any(t.endswith("is waiting for you to approve the plan") for t in texts), texts
    assert not any("approve plan" in t for t in texts), texts
    # Anti-vacuity: any other ask keeps its words — since v1.352.0 the
    # approval card's own ("save a file", moved on purpose: one vocabulary).
    assert any(t.endswith("is waiting for your OK to save a file") for t in texts), texts


@requires_node
def test_the_desktop_toast_and_the_card_say_the_plan_ask_alike(tmp_path):
    card = _tool_words(PLAN_APPROVAL_TOOL)
    assert card == "follow this plan", card
    out = _asks({"steps": [{"approvals": [
        {"id": "p", "tool": PLAN_APPROVAL_TOOL, "count": 1},
        {"id": "q", "tool": PLAN_APPROVAL_TOOL, "count": 2},
    ], "now": 0}]}, tmp_path)["out"]
    bodies = [n["body"] for n in out[0]["notify"]]
    assert bodies == [
        "follow a plan — click to open the run.",
        "follow 2 plans — click to open the run.",
    ], bodies


def test_anti_vacuity_the_shared_seed_still_seeds(tmp_path):
    """`_seed` (lane C2's) writes the plan this file's route pins read."""
    app = create_app(str(tmp_path))
    sid, rid = _seed(app.state.platform.engine, status=SessionStatus.COMPLETED,
                     run_state=AgentState.COMPLETED, goals=_THREE, results=[])
    stored = tr.latest_plan(app.state.platform.engine, sid)
    assert stored is not None and stored["run_id"] == rid
    assert json.loads(json.dumps(stored["goals"])) == _THREE
    assert SimpleNamespace(**stored).run_state == "completed"
