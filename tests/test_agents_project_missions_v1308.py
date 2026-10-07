"""v1.308.0 — a project's MISSION works with that project's team.

The round table is gone from the dashboard; a project is now a mission screen,
and "Edit team" is how the user picks who works on it. That choice must be
REAL, not decoration: ``POST /missions`` with a project snapshots the team
onto the coordinator's run options, the coordinator's roster block lists only
the team, and ``delegate`` / ``spawn_agent`` refuse anyone else — while a
mission outside a project (or in a project with no team) stays unrestricted.

Driven through the real app factory / orchestrator / registry; only the LLM
is scripted.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.roster import roster_block
from iron_jarvis.agents.team import mission_team, off_team_refusal, on_team
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Project, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import ToolContext


class _Coordinator(MockLLMAdapter):
    """Supervisor: delegate to `researcher`, then report what came back."""

    systems: list[str] = []
    results: list[str] = []

    async def complete(self, *, system, messages, tools, **kw):
        tool_msgs = [m for m in messages if m.role == "tool"]
        if "As the Supervisor" in system:
            _Coordinator.systems.append(system)
            if tool_msgs:
                _Coordinator.results.append(tool_msgs[-1].content)
                return LLMResponse(text="# Report\n\ndone", finish_reason="stop")
            return LLMResponse(
                tool_calls=[ToolCall(id="d1", name="delegate", arguments={
                    "agent_type": "researcher", "task": "look into it"})],
                finish_reason="tool_use",
            )
        return LLMResponse(text="researched", finish_reason="stop")


def _project(app, team: list[str]) -> str:
    with session_scope(app.state.platform.engine) as db:
        p = Project(name="Acme", team_json=json.dumps(team))
        db.add(p)
        db.commit()
        return p.id


def _wait(c: TestClient, sid: str) -> dict:
    for _ in range(400):
        row = c.get(f"/sessions/{sid}").json()["session"]
        if row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError("mission never finished")


def _children(app, root_id: str) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        return [s for s in db.query(Session).all() if s.id != root_id]


def test_a_project_mission_hands_work_only_to_the_projects_team(tmp_path):
    _Coordinator.systems, _Coordinator.results = [], []
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: _Coordinator())
    pid = _project(app, ["reviewer", "builder"])
    with TestClient(app) as c:
        row = c.post("/missions", json={"objective": "check the ledger", "project_id": pid}).json()
        assert row["options"] == {"deliverable": True, "team": ["reviewer", "builder"]}
        _wait(c, row["id"])
        listed = c.get(f"/missions?project_id={pid}").json()["missions"]
        other = c.get("/missions?project_id=nope").json()["missions"]
    # the coordinator was SHOWN only the team …
    block = _Coordinator.systems[0].split("# Who can take this work", 1)[1]
    assert "- reviewer" in block and "- builder" in block
    assert "- researcher" not in block
    # … and its delegation outside the team was REFUSED, nothing ran
    assert any("'researcher' is not on this project's team" in r for r in _Coordinator.results)
    assert _children(app, row["id"]) == []
    assert [m["id"] for m in listed] == [row["id"]] and other == []


def test_a_mission_without_a_project_team_is_unrestricted(tmp_path):
    _Coordinator.systems, _Coordinator.results = [], []
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: _Coordinator())
    empty = _project(app, [])
    with TestClient(app) as c:
        a = c.post("/missions", json={"objective": "one"}).json()
        b = c.post("/missions", json={"objective": "two", "project_id": empty}).json()
        _wait(c, a["id"])
        _wait(c, b["id"])
    assert a["options"] == {"deliverable": True}
    assert b["options"] == {"deliverable": True}
    for root in (a["id"], b["id"]):
        kids = _children(app, root)
        assert any(k.agent_name == "researcher" for k in kids), root


async def test_spawn_agent_refuses_off_team_too(tmp_path):
    from iron_jarvis.agents.agent_tools import SpawnAgentTool

    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: _Coordinator())
    orch = Orchestrator(p)
    parent = await orch.create_session(
        "mission", AgentType.SUPERVISOR, provider="mock", model="mock-1",
        options={"deliverable": True, "team": ["reviewer"]},
    )
    ctx = ToolContext(workspace=tmp_path, session_id=parent.id, agent_run_id="r",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    res = await SpawnAgentTool(p, p.agents_registry).execute(
        {"agent": "researcher", "task": "x"}, ctx)
    assert res.ok is False and "not on this project's team" in (res.error or "")
    ok = await SpawnAgentTool(p, p.agents_registry).execute(
        {"agent": "reviewer", "task": "x"}, ctx)
    assert ok.ok is True, ok.error


def test_team_helpers():
    s = Session(options_json=json.dumps({"team": ["custom:Writer", " ", 3, "reviewer"]}))
    assert mission_team(s) == ["custom:Writer", "reviewer"]
    assert mission_team(Session()) == []
    assert on_team(["custom:Writer"], "custom : writer")
    assert not on_team(["custom:Writer"], "writer")
    assert on_team([], "anyone")
    assert "hand this part to one of: reviewer" in off_team_refusal(["reviewer"], "builder")


def test_roster_block_only(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    block = roster_block(p, only=["reviewer"])
    assert "- reviewer" in block and "- builder" not in block
    assert "- builder" in roster_block(p)


def test_team_suggestions_never_offer_a_coordinator(tmp_path):
    """The coordinator ran IN the project (it is the mission's supervisor),
    but it cannot be handed a mission's part — only the teammates it
    delegated to are suggested."""
    _Coordinator.systems, _Coordinator.results = [], []
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: _Coordinator())
    pid = _project(app, [])
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "x", "project_id": pid}).json()["id"]
        _wait(c, sid)
        names = [s["name"] for s in c.get(f"/projects/{pid}/team").json()["suggestions"]]
    assert "researcher" in names
    assert "supervisor" not in names and "planner" not in names
