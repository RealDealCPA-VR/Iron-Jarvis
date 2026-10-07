"""v1.307.0 — the mission view, plus three defects the agents review found.

Driven through the REAL app factory / platform / orchestrator / runtime /
registry; only the LLM is scripted.

Fixes:
* ``with_worklist`` dropped the job card (``skills``, ``reports_to``) — a
  custom agent on a bulk task, and every scheduled run carrying skills, ran
  without them.
* a delegated / spawned child's SUCCESS path skipped the finalize contract:
  worklist claims stayed ``doing``, no ``outcome`` was derived, the folder
  note was overwritten (``Orchestrator.settle_child``).
* a delegated child was invisible to roster liveness (``agents/team.py``).

Feature: ``POST /missions`` (one objective → a SUPERVISOR run with the
``deliverable`` option), ``GET /missions``, ``GET /sessions/{id}/mission``
(members, honest progress, plain-words activity, deliverable) and the team
stream mirror (``StreamHub.link``).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import team as team_mod
from iron_jarvis.agents.mission import progress_for, tool_line
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.roster import build_roster
from iron_jarvis.agents.runtime import MISSION_DELIVERABLE
from iron_jarvis.agents.supervisor import with_worklist
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, Session, SessionStatus
from iron_jarvis.core.streams import StreamHub
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.worklist.models import WorklistItem

REPORT = "# Market report\n\n## Findings\n\n- three competitors\n"


class _Team(MockLLMAdapter):
    """Supervisor: delegate one research task, then write the report.
    Researcher: list its folder once, then report. Systems are recorded."""

    systems: list[str] = []
    #: who ``agents/team.py`` said was working, seen from INSIDE the worker
    live_seen: list[list[str]] = []

    async def complete(self, *, system, messages, tools, **kw):
        _Team.systems.append(system)
        if "As the Researcher" in system:
            _Team.live_seen.append(
                [row["agent"] for row in team_mod.live_children().values()]
            )
        tool_msgs = [m for m in messages if m.role == "tool"]
        if "As the Supervisor" in system:
            if tool_msgs:
                return LLMResponse(text=REPORT, finish_reason="stop")
            return LLMResponse(
                tool_calls=[ToolCall(id="d1", name="delegate", arguments={
                    "agent_type": "researcher",
                    "task": "research the three main competitors",
                })],
                finish_reason="tool_use",
            )
        if tool_msgs:
            return LLMResponse(text="found three competitors", finish_reason="stop")
        return LLMResponse(
            tool_calls=[ToolCall(id="l1", name="list_files", arguments={})],
            finish_reason="tool_use",
        )


# --------------------------------------------------------------------------- #
# fix 1: with_worklist keeps the job card
# --------------------------------------------------------------------------- #


def test_with_worklist_keeps_skills_and_manager():
    base = AgentDefinition(
        type=AgentType.BUILDER,
        system_prompt="you are tax-reader",
        tools=["read_file"],
        permission_overrides={"shell": "deny"},
        skills=["k1-reading"],
        reports_to="custom:boss",
    )
    wrapped = with_worklist(base)
    assert wrapped.skills == ["k1-reading"]
    assert wrapped.reports_to == "custom:boss"
    assert wrapped.permission_overrides == {"shell": "deny"}
    # a copy, never the same list
    assert wrapped.skills is not base.skills


# --------------------------------------------------------------------------- #
# fix 2: a delegated child is settled like a solo run
# --------------------------------------------------------------------------- #


async def test_settle_child_releases_claims_and_derives_outcome(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    child = await orch.create_session("child job", AgentType.BUILDER, provider="mock")
    run = AgentRun(
        session_id=child.id, agent_type=AgentType.BUILDER, provider="mock",
        model="mock-1", state=AgentState.COMPLETED, result="did the thing",
    )
    with session_scope(p.engine) as db:
        db.add(run)
        db.commit()
        db.refresh(run)
        run = AgentRun(**run.model_dump())
    board = p.worklist.board_for_root(child.id)
    p.worklist.add(board, [("a.pdf", "a.pdf"), ("b.pdf", "b.pdf")])
    claimed, _ = p.worklist.claim(board, run.id, 2)
    assert len(claimed) == 2

    await orch.settle_child(child, run)

    with session_scope(p.engine) as db:
        items = list(db.exec(select(WorklistItem).where(WorklistItem.board_id == board)))
        row = db.get(Session, child.id)
    assert {i.status for i in items} == {"pending"}, [i.status for i in items]
    assert row.status is SessionStatus.COMPLETED
    assert row.outcome == "completed"
    assert row.summary == "did the thing"
    assert row.finished_at is not None


async def test_settle_child_keeps_the_folder_note(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    child = await orch.create_session("child job", AgentType.BUILDER, provider="mock")
    child.summary = "Folder note: the project folder could not be used"
    run = AgentRun(
        session_id=child.id, agent_type=AgentType.BUILDER, provider="mock",
        model="mock-1", state=AgentState.COMPLETED, result="report written",
    )
    await orch.settle_child(child, run)
    assert child.summary.startswith("report written")
    assert "Folder note: the project folder could not be used" in child.summary


# --------------------------------------------------------------------------- #
# fix 3: a delegated child is VISIBLY busy (display only)
# --------------------------------------------------------------------------- #


async def test_delegated_child_is_busy_on_the_roster_while_it_runs(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.orchestrator = Orchestrator(p)
    orch = p.orchestrator
    child = await orch.create_session(
        "research", AgentType.RESEARCHER, provider="mock", agent_name="researcher"
    )

    def _activity() -> str:
        return next(e.activity for e in build_roster(p, with_health=False) if e.name == "researcher")

    assert _activity() != "busy"
    with team_mod.working(p, child.id, "parent-sid", "researcher", "research"):
        assert _activity() == "busy"
        assert child.id in team_mod.live_children()
        # display only: it holds no governed slot and no cancel handle
        assert child.id not in orch._running
        assert child.id not in orch._governed
    assert _activity() != "busy"
    assert child.id not in team_mod.live_children()


async def test_working_always_leaves_on_error(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    with pytest.raises(RuntimeError):
        with team_mod.working(p, "kid", "parent", "builder"):
            raise RuntimeError("boom")
    assert "kid" not in team_mod.live_children()
    assert p.streams.root_of("kid") == "kid"


# --------------------------------------------------------------------------- #
# the team stream mirror
# --------------------------------------------------------------------------- #


async def test_stream_mirror_wraps_child_frames_and_never_ends_the_root():
    hub = StreamHub()
    root_q = hub.subscribe("root")
    assert hub.link("child", "root", {"agent": "researcher"}) == "root"
    # a grandchild mirrors to the SAME root
    assert hub.link("grand", "child", {"agent": "builder"}) == "root"
    hub.sink("child", "r1").token_delta("hello")
    hub.sink("grand", "r2").done(ok=True, result="x")
    first = root_q.get_nowait()
    second = root_q.get_nowait()
    assert first["event"] == "member"
    assert first["data"]["member"] == {"session_id": "child", "agent": "researcher"}
    assert first["data"]["event"] == "token"
    assert first["data"]["data"] == {"text": "hello"}
    # a member's done is wrapped — it can never close the root's SSE
    assert second["event"] == "member" and second["data"]["event"] == "done"
    hub.unlink("child")
    hub.sink("child", "r1").token_delta("after")
    assert root_q.empty()


# --------------------------------------------------------------------------- #
# progress honesty + the plain-words log
# --------------------------------------------------------------------------- #


def test_progress_is_counted_or_absent():
    assert progress_for("done", 9, (0, 0, ""))["pct"] == 100
    assert progress_for("queued", 0, (0, 0, ""))["pct"] == 0
    working = progress_for("working", 3, (0, 0, ""))
    assert working["pct"] is None and working["basis"] == "steps"
    planned = progress_for("working", 3, (4, 1, "read the ledger"))
    assert planned["pct"] == 25 and planned["basis"] == "plan"
    assert "Step 2 of 4" in planned["label"]


def test_tool_lines_name_a_basename_never_the_arguments():
    assert tool_line("read_file", {"path": "C:/clients/acme/k1.pdf"}, True) == "read k1.pdf"
    assert tool_line("web_search", {"query": "competitors"}, True) == "searched the web for “competitors”"
    assert tool_line("write_document", {}, False) == "could not create a file"
    assert tool_line("some_new_tool", {"secret": "x"}, True) == "used some new tool"


async def test_a_running_mission_shows_no_deliverable_yet(tmp_path):
    """A running session's ``summary`` is only its create-time folder note —
    never the deliverable."""
    from iron_jarvis.agents.mission import mission_view

    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("objective", AgentType.SUPERVISOR, provider="mock")
    with session_scope(p.engine) as db:
        row = db.get(Session, s.id)
        row.summary = "Folder note: project folder could not be used"
        db.add(row)
        db.commit()
    view = mission_view(p, s.id)
    assert view["deliverable"]["text"] == ""
    assert view["coordinator"]["status"] == "queued"
    assert view["members"] == [] and view["progress"] == {"done": 0, "total": 0}


async def test_spawn_agent_registers_its_child_as_working(tmp_path):
    from iron_jarvis.agents.agent_tools import SpawnAgentTool
    from iron_jarvis.tools.base import ToolContext

    _Team.live_seen = []
    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: _Team())
    orch = Orchestrator(p)
    parent = await orch.create_session("job", AgentType.AUTOMATION, provider="mock", model="mock-1")
    res = await SpawnAgentTool(p, p.agents_registry).execute(
        {"agent": "researcher", "task": "look around"},
        ToolContext(workspace=tmp_path, session_id=parent.id, agent_run_id="auto-run",
                    config=p.config, event_bus=p.event_bus, engine=p.engine),
    )
    assert res.ok is True, res.error
    assert _Team.live_seen and all("researcher" in seen for seen in _Team.live_seen)
    assert not team_mod.live_children()
    with session_scope(p.engine) as db:
        kid = db.get(Session, res.data["child_session_id"])
    assert kid.outcome == "completed"


# --------------------------------------------------------------------------- #
# the mission door, end to end
# --------------------------------------------------------------------------- #


def _wait_terminal(c: TestClient, sid: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = c.get(f"/sessions/{sid}").json()["session"]
        if row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"mission {sid} never finished")


def test_mission_door_runs_the_team_and_serves_the_view(tmp_path):
    _Team.systems = []
    _Team.live_seen = []
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: _Team())
    with TestClient(app) as c:
        assert c.post("/missions", json={"objective": "   "}).status_code == 400
        res = c.post("/missions", json={"objective": "write a market report"})
        assert res.status_code == 201, res.text
        row = res.json()
        assert row["agent_type"] == "supervisor"
        assert row["origin"] == "job:mission"
        assert row["options"] == {"deliverable": True}
        sid = row["id"]
        done = _wait_terminal(c, sid)
        assert done["status"] == "completed", done

        view = c.get(f"/sessions/{sid}/mission").json()
        missions = c.get("/missions").json()["missions"]
        assert c.get("/sessions/nope/mission").json() == {"found": False, "session_id": "nope"}

    # the coordinator was told to make its final message the deliverable;
    # the worker was not (the option is not inherited)
    sup = [s for s in _Team.systems if "As the Supervisor" in s]
    worker = [s for s in _Team.systems if "As the Researcher" in s]
    assert sup and all(MISSION_DELIVERABLE in s for s in sup)
    assert worker and not any(MISSION_DELIVERABLE in s for s in worker)
    # the delegate door registered the child as working for the whole run,
    # and it left when the run ended
    assert _Team.live_seen and all("researcher" in seen for seen in _Team.live_seen)
    assert not team_mod.live_children()

    assert view["found"] is True
    assert view["deliverable"]["text"].startswith("# Market report")
    assert view["session"]["task"] == "write a market report"
    assert len(view["members"]) == 1
    m = view["members"][0]
    assert m["agent"] == "researcher" and m["name"] == "Researcher"
    assert m["status"] == "done"
    assert m["progress"] == {"pct": 100, "label": "Done", "basis": "done"}
    assert m["task"] == "research the three main competitors"
    assert view["progress"] == {"done": 1, "total": 1}
    texts = [a["text"] for a in view["activity"]]
    assert "Jarvis gave Researcher a task: research the three main competitors" in texts, texts
    assert "Researcher looked through a folder" in texts, texts
    assert any("Researcher finished" in t for t in texts), texts
    assert texts[-1] == "Jarvis finished the objective", texts
    # delegate bookkeeping never reaches the human log
    assert not any("delegate" in t for t in texts), texts
    assert [x["id"] for x in missions] == [sid]
    assert missions[0]["objective"] == "write a market report"

    # the child row was settled through the one helper (an outcome verdict)
    with session_scope(app.state.platform.engine) as db:
        kid = db.get(Session, m["session_id"])
    assert kid.outcome == "completed"
    assert kid.agent_name == "researcher"
