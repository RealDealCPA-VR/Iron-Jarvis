"""v1.295.0 reviewer pins — the seams between the three doers.

* A database created BEFORE the wave (the dynamic-agent table without the ten
  job-card columns, a row inserted with raw SQL) must reach EVERY door the
  wave touched without a 500: GET /agents, GET /agents/roster, PATCH, the
  spawn route, the delegate tool and the schedule fire. The doers pinned the
  registry readers; this drives the product.
* ``deny_tools`` is proven at the REAL ``registry.invoke`` through the
  runtime path (the offline MockLLM calls ``write_file`` first whenever it is
  armed): the call is refused, the file is never written, and the ledger row
  says deny — plus the control without a deny list.
* The allowance pct FLOORS: 995 of 1,000 is 99% (warning), never "used up".
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import allowance
from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import PermissionMode, Session, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

OLD_DDL = (
    "CREATE TABLE dynamicagentrecord (id VARCHAR PRIMARY KEY, name VARCHAR, "
    "system_prompt VARCHAR, tools_json VARCHAR, base_type VARCHAR, "
    "description VARCHAR, provider VARCHAR, model VARCHAR, created_at DATETIME)"
)
OLD_ROW = (
    "INSERT INTO dynamicagentrecord VALUES ('dyn_old', 'oldie', 'Old prompt.', "
    "'[\"read_file\"]', 'builder', 'from before', '', '', '2026-01-01 00:00:00')"
)


def _old_home(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    con = sqlite3.connect(str(home / "ironjarvis.db"))
    con.execute(OLD_DDL)
    con.execute(OLD_ROW)
    con.commit()
    con.close()


def _ctx(platform, tmp_path, run_id="parent1"):
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def test_a_pre_wave_row_reaches_every_door_without_a_500(tmp_path):
    _old_home(tmp_path)
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        p.permissions = PermissionEngine({**p.config.permissions, "delegate": "allow"})

        # GET /agents — the row reads defaults, never None.
        rows = {r["name"]: r for r in client.get("/agents").json()["dynamic"]}
        row = rows["oldie"]
        assert row["approval_mode"] == "" and row["max_steps"] is None
        assert row["skills"] == [] and row["deny_tools"] == []
        assert row["reports_to"] == "" and row["paused"] is None
        assert row["allowance"]["status"] == "unlimited"
        assert row["tools"] == ["read_file"] and row["effective_tools"]

        # GET /agents/roster — healthy, not paused, no allowance figure.
        entry = {e["name"]: e for e in client.get("/agents/roster").json()["roster"]}["custom:oldie"]
        assert entry["healthy"] is True and entry["paused"] is False
        assert entry["pause_reason"] == "" and entry["allowance"]["status"] == "unlimited"

        # PATCH one job-card field; the old row takes it and keeps the rest.
        r = client.patch("/agents/oldie", json={"allowance_tokens": 500})
        assert r.status_code == 200, r.text
        assert r.json()["allowance"]["tokens"] == 500 and r.json()["description"] == "from before"

        # The spawn route runs it (offline mock), credited to custom:oldie.
        r = client.post("/agents/oldie/spawn", json={"task": "summarize", "wait": True})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        with session_scope(p.engine) as db:
            credited = list(db.exec(select(Session).where(Session.agent_name == "custom:oldie")))
        assert len(credited) == 1 and credited[0].max_steps is None

        # The delegate tool, both spellings.
        tool = DelegateTool(p)
        for target in ("custom:oldie", "oldie"):
            result = client.portal.call(
                tool.execute, {"agent_type": target, "task": "x"}, _ctx(p, tmp_path, target)
            )
            assert result.ok, result.error

        # The schedule fire.
        r = client.post(
            "/schedules",
            json={"name": "old-rounds", "cron": "0 9 * * *", "kind": "task",
                  "payload": {"task": "Rounds.", "agent_type": "oldie"}},
        )
        assert r.status_code in (200, 201), r.text
        ran = client.post("/schedules/old-rounds/run").json()
        assert ran["last_status"] == "ok", ran

        # Pause/resume on the old row, and the refusal reaches the spawn route.
        assert client.post("/agents/oldie/pause", json={"reason": "day off"}).status_code == 200
        r = client.post("/agents/oldie/spawn", json={"task": "again", "wait": True})
        assert r.status_code == 409 and "oldie is paused: day off" == r.json()["detail"]
        assert client.post("/agents/oldie/resume").status_code == 200


async def test_deny_tools_refuse_the_call_at_the_real_invoke(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine({**p.config.permissions, "spawn_agent": "allow", "write_file": "allow"})
    reg = p.agents_registry

    async def run(name: str) -> tuple[Session, list[ToolInvocation]]:
        result = await p.registry.invoke(
            "spawn_agent", {"agent": name, "task": "write the summary"},
            _ctx(p, tmp_path, name), p.permissions,
        )
        assert result.ok, result.error
        with session_scope(p.engine) as db:
            child = list(db.exec(select(Session).where(Session.agent_name == f"custom:{name}")))[-1]
            rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == child.id)))
        return child, rows

    # Control: the mock's write_file lands.
    reg.register("writer", "You write.", ["read_file", "write_file"])
    child, rows = await run("writer")
    writes = [r for r in rows if r.tool == "write_file"]
    assert writes and writes[0].ok is True
    assert (tmp_path / "workspaces" / child.id / "RESULT.md").exists() or any(
        pth.name == "RESULT.md" for pth in tmp_path.rglob("RESULT.md")
    )

    # The deny list: same roster, write_file denied — refused at invoke,
    # nothing written, the ledger row says deny.
    reg.register("denied", "You write.", ["read_file", "write_file"], deny_tools=["write_file"])
    before = {pth for pth in tmp_path.rglob("RESULT.md")}
    child, rows = await run("denied")
    writes = [r for r in rows if r.tool == "write_file"]
    assert writes, "the mock still CALLED write_file — the deny is enforced at invoke, not by hiding the tool"
    assert all(r.ok is False for r in writes)
    assert all(r.verdict is PermissionMode.DENY for r in writes), [r.verdict for r in writes]
    assert {pth for pth in tmp_path.rglob("RESULT.md")} == before, "a denied write must not land"


def test_pct_floors_so_a_near_miss_is_not_used_up():
    rec = type("R", (), {"name": "x", "allowance_tokens": 1000, "allowance_usd": 0.0,
                         "paused_reason": "", "allowance_warned_month": ""})()
    st = allowance.allowance_state(rec, {"tokens": 995})
    assert (st["pct"], st["status"]) == (99, "warning")
    st = allowance.allowance_state(rec, {"tokens": 1000})
    assert (st["pct"], st["status"]) == (100, "exhausted")
    usd = type("R", (), {"name": "x", "allowance_tokens": 0, "allowance_usd": 10.0,
                         "paused_reason": "", "allowance_warned_month": ""})()
    st = allowance.allowance_state(usd, {"usd": 7.996})
    assert (st["pct"], st["status"]) == (79, "ok")
    st = allowance.allowance_state(usd, {"usd": 8.0})
    assert (st["pct"], st["status"]) == (80, "warning")


def test_the_spawn_route_run_reaches_the_allowance_tail(tmp_path):
    """The Agents-page job (POST /agents/{name}/spawn) and the schedule fire
    both end in ``Orchestrator.run_session`` — its tail must auto-pause too,
    not only the spawn_agent/delegate child paths (reviewer mutation M14)."""
    from iron_jarvis.core.models import EventRecord

    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        p.agents_registry.register("scout", "You scout.", ["read_file"], allowance_tokens=1000)
        with session_scope(p.engine) as db:
            db.add(Session(task="seeded", agent_name="custom:scout", provider="mock",
                           model="mock-1", input_tokens=500, output_tokens=0))
            db.commit()
        real_get = p.providers.get

        def spy_get(prov, model=None):
            adapter = real_get(prov, model)
            real_complete = adapter.complete

            async def spy(*, system, messages, tools, **kw):
                resp = await real_complete(system=system, messages=messages, tools=tools, **kw)
                resp.usage = {"input_tokens": 600, "output_tokens": 0}
                return resp

            adapter.complete = spy
            return adapter

        p.providers.get = spy_get
        r = client.post("/agents/scout/spawn", json={"task": "scout the project", "wait": True})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        rec = p.agents_registry.get("scout")
        assert rec.paused_reason.startswith("monthly allowance used up ("), rec.paused_reason
        with session_scope(p.engine) as db:
            paused = list(db.exec(select(EventRecord).where(EventRecord.type == "agent.paused")))
        assert len(paused) == 1
        r = client.post("/agents/scout/spawn", json={"task": "again", "wait": True})
        assert r.status_code == 409 and "scout is paused: monthly allowance used up" in r.json()["detail"]
