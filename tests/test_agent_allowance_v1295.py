"""v1.295.0, wave 1 — a custom agent is an EMPLOYEE: a job card, a monthly
allowance, a day off. Offline, deterministic.

What is guarded, each with its silent failure mode:
  - the job-card columns reconcile onto a DB created BEFORE the wave and an
    old row reads the defaults (a NULL string would reach every door);
  - ``register`` keeps every field a PATCH did not send (a re-post that
    clobbers ``skills`` while raising ``allowance_usd`` is the v1.164.0 trap);
  - ``month_spend`` sums THIS agent's rows in THIS calendar month only;
  - ``allowance_state`` thresholds: 79 ok / 80 warning / 100 exhausted,
    both-zero unlimited, the usd bound alone, the HIGHER ratio wins;
  - ``refusal_for`` wording for paused and exhausted;
  - ``after_run`` pauses ONCE and publishes ``agent.paused`` ONCE; the
    warning goes out once per month and again in a new month;
  - every door — ``spawn_agent``, ``delegate``, the schedule fire — refuses a
    paused agent with the reason and creates NO session row;
  - the doors pass the record's ``max_steps`` / ``approval_mode`` when the
    caller states none;
  - the auto-pause fires from the post-run tail after a REAL offline run that
    pushes spend over the allowance (the child's tokens land on its row);
  - ``deny_tools`` reaches ``permission_overrides`` as "deny" and never as
    anything that could widen (deny floor);
  - skills + the manager line reach the system prompt the model receives;
  - the Claude CLI argv carries ``--max-budget-usd`` only when the run budget
    is armed (and above the CLI's floor); never for codex; the prompt still
    rides stdin.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import allowance
from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.dynamic import DynamicAgentRegistry
from iron_jarvis.agents.roster import build_roster, resolve_target
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import EventRecord, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.pricing import cost_for
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import budget
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.skills.loader import Skill
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS, PermissionEngine
from iron_jarvis.core.models import PermissionMode

PROMPT = "You are Scout, a focused research helper. Be concise."
TOOLS = ["read_file", "write_file", "list_files"]
NAME = "scout"
ROSTER = "custom:scout"

OCT = datetime(2026, 10, 15, 12, 0, 0)
NOV = datetime(2026, 11, 3, 9, 0, 0)


# --------------------------------------------------------------------------- #
# fixtures (mirroring tests/test_dynamic_agents.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine(
        {
            **p.config.permissions,
            "create_agent": "allow",
            "list_agents": "allow",
            "spawn_agent": "allow",
            "delegate": "allow",
        }
    )
    return p


@pytest.fixture
def registry(platform) -> DynamicAgentRegistry:
    # THE platform's registry: the doors (spawn_agent, delegate, the fire) read
    # this one, so a pause set here is the pause they see.
    return platform.agents_registry


def _ctx(platform, tmp_path, agent_run_id="parent1"):
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _seed(
    engine,
    name: str,
    in_tok: int,
    out_tok: int = 0,
    *,
    created_at: datetime | None = None,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> None:
    row = Session(
        task="seeded",
        agent_name=name,
        provider=provider,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )
    if created_at is not None:
        row.created_at = created_at
    with session_scope(engine) as db:
        db.add(row)
        db.commit()


def _sessions(engine) -> list[Session]:
    with session_scope(engine) as db:
        return list(db.exec(select(Session)))


def _events(engine, type_: str) -> list[dict]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(EventRecord).where(EventRecord.type == type_)))
    return [json.loads(r.payload_json or "{}") for r in rows]


class _Bus:
    """A recording bus with the real bus's ASYNC publish shape."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, type_, payload=None, session_id=None):
        self.events.append((str(type_), dict(payload or {})))


def _spy_complete(platform, seen: dict, usage: dict | None = None):
    """Capture every system prompt (test_profile_v1144's spy) and, when asked,
    stamp a token usage onto the mock's answers so an offline run SPENDS."""
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen.setdefault("systems", []).append(system)
            seen.setdefault("budgets", []).append(budget.run_budget())
            resp = await real_complete(system=system, messages=messages, tools=tools, **kw)
            if usage is not None:
                resp.usage = dict(usage)
            return resp

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get
    return seen


# --------------------------------------------------------------------------- #
# 1. additive columns reconcile onto an OLD database
# --------------------------------------------------------------------------- #

_OLD_COLUMNS = (
    "id", "name", "system_prompt", "tools_json", "base_type",
    "description", "provider", "model", "created_at",
)
_NEW_COLUMNS = (
    "approval_mode", "max_steps", "allowance_tokens", "allowance_usd",
    "allowance_warned_month", "paused_reason", "paused_at", "reports_to",
    "skills_json", "deny_tools_json",
)


def test_old_db_gains_the_job_card_columns_and_an_old_row_reads_defaults(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    db_path = home / "ironjarvis.db"
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE dynamicagentrecord (id VARCHAR PRIMARY KEY, name VARCHAR, "
        "system_prompt VARCHAR, tools_json VARCHAR, base_type VARCHAR, "
        "description VARCHAR, provider VARCHAR, model VARCHAR, created_at DATETIME)"
    )
    con.execute(
        "INSERT INTO dynamicagentrecord VALUES ('dyn_old', 'oldie', 'Old prompt.', "
        "'[\"read_file\"]', 'builder', 'from before', '', '', '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    p = build_platform(str(tmp_path))  # init_db -> _reconcile_additive_columns
    assert p.config.db_path == db_path

    con = sqlite3.connect(str(db_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(dynamicagentrecord)")}
    con.close()
    assert set(_OLD_COLUMNS) <= cols
    missing = set(_NEW_COLUMNS) - cols
    assert not missing, f"reconciler did not add {sorted(missing)}"

    # The old row reads the DEFAULTS through every registry door (the DB holds
    # NULL in the widened columns; a None string must never reach a door).
    rec = p.agents_registry.get("oldie")
    assert rec is not None
    assert rec.approval_mode == ""
    assert rec.allowance_tokens == 0
    assert rec.allowance_usd == 0.0
    assert rec.paused_reason == ""
    assert rec.reports_to == ""
    assert rec.skills_json == "[]" and rec.deny_tools_json == "[]"
    assert rec.max_steps is None and rec.paused_at is None
    assert allowance.refusal_for(rec, p.engine) == ""
    assert allowance.state_for(rec, p.engine)["status"] == "unlimited"
    d = p.agents_registry.definition("oldie")
    assert d.permission_overrides == {} and d.skills == [] and d.reports_to == ""
    entry = next(e for e in build_roster(p) if e.name == "custom:oldie")
    assert entry.paused is False and entry.healthy is True
    # …and the old row can be given a job card like any other.
    p.agents_registry.register("oldie", "Old prompt.", ["read_file"], allowance_tokens=50)
    fresh = DynamicAgentRegistry(p.engine).load().get("oldie")
    assert fresh.allowance_tokens == 50 and fresh.description == ""


# --------------------------------------------------------------------------- #
# 2. register(): partial update keeps what was not sent
# --------------------------------------------------------------------------- #


def test_register_partial_update_keeps_unspecified_job_card_fields(platform, registry):
    registry.register(
        NAME, PROMPT, TOOLS,
        approval_mode="always_ask", max_steps=7, allowance_tokens=5000,
        allowance_usd=12.5, reports_to="builder",
        skills=["tidy-notes", "tidy-notes", " "], deny_tools=["shell"],
    )
    # One field PATCHed — everything else must survive.
    rec = registry.register(NAME, PROMPT, TOOLS, allowance_usd=9.0)
    assert rec.allowance_usd == 9.0
    assert rec.approval_mode == "always_ask"
    assert rec.max_steps == 7
    assert rec.allowance_tokens == 5000
    assert rec.reports_to == "builder"
    assert json.loads(rec.skills_json) == ["tidy-notes"]  # de-duplicated, blanks dropped
    assert json.loads(rec.deny_tools_json) == ["shell"]
    # A fresh registry recovers it purely from persistence.
    fresh = DynamicAgentRegistry(platform.engine).load().get(NAME)
    assert fresh.approval_mode == "always_ask" and fresh.max_steps == 7
    assert json.loads(fresh.skills_json) == ["tidy-notes"]
    # Explicit values still land; yolo never does.
    rec = registry.register(NAME, PROMPT, TOOLS, approval_mode="yolo", max_steps=0, skills=[])
    assert rec.approval_mode == ""
    assert rec.max_steps is None
    assert json.loads(rec.skills_json) == []
    assert rec.allowance_tokens == 5000  # untouched by that call


def test_set_paused_resume_and_warned_month(platform, registry):
    registry.register(NAME, PROMPT, TOOLS)
    rec = registry.set_paused(NAME, "  day   off ")
    assert rec.paused_reason == "day off" and rec.paused_at is not None
    assert DynamicAgentRegistry(platform.engine).load().get(NAME).paused_reason == "day off"
    rec = registry.resume(NAME)
    assert rec.paused_reason == "" and rec.paused_at is None
    rec = registry.set_warned_month(NAME, "2026-10")
    assert rec.allowance_warned_month == "2026-10"
    assert registry.set_paused("nobody", "x") is None
    assert registry.resume("nobody") is None
    assert registry.set_warned_month("nobody", "2026-10") is None


def test_event_kinds_exist():
    assert EventType.AGENT_PAUSED == "agent.paused"
    assert EventType.AGENT_ALLOWANCE_WARNING == "agent.allowance_warning"


# --------------------------------------------------------------------------- #
# 3. month_spend / allowance_state / refusal_for
# --------------------------------------------------------------------------- #


def test_month_key():
    assert allowance.month_key(OCT) == "2026-10"
    assert allowance.month_key(datetime(2026, 1, 1)) == "2026-01"


def test_month_spend_sums_only_this_agent_this_month(platform):
    e = platform.engine
    _seed(e, ROSTER, 1000, 200, created_at=OCT)
    _seed(e, ROSTER, 300, 100, created_at=datetime(2026, 10, 31, 23, 59))
    _seed(e, ROSTER, 9999, 0, created_at=datetime(2026, 9, 30, 23, 59))  # last month
    _seed(e, ROSTER, 9999, 0, created_at=datetime(2026, 11, 1, 0, 0))  # next month
    _seed(e, "custom:other", 7777, 0, created_at=OCT)  # another agent
    _seed(e, "", 5555, 0, created_at=OCT)  # un-stamped builtin run
    spend = allowance.month_spend(e, ROSTER, OCT)
    assert spend["tokens"] == 1600
    assert spend["runs"] == 2
    expected_usd = cost_for("anthropic", "claude-opus-4-8", 1000, 200) + cost_for(
        "anthropic", "claude-opus-4-8", 300, 100
    )
    assert spend["usd"] == pytest.approx(expected_usd) and expected_usd > 0
    assert allowance.month_spend(e, "custom:nobody", OCT) == {"tokens": 0, "usd": 0.0, "runs": 0}
    assert allowance.month_spend(None, ROSTER, OCT) == {"tokens": 0, "usd": 0.0, "runs": 0}


def _rec(tokens=0, usd=0.0, **kw):
    return SimpleNamespace(name=NAME, allowance_tokens=tokens, allowance_usd=usd,
                           paused_reason="", allowance_warned_month="", **kw)


def test_allowance_state_thresholds():
    st = allowance.allowance_state(_rec(1000), {"tokens": 790, "usd": 0.0, "runs": 3}, OCT)
    assert (st["status"], st["pct"], st["left_tokens"], st["runs"]) == ("ok", 79, 210, 3)
    assert st["month"] == "2026-10" and st["left_usd"] is None
    assert allowance.allowance_state(_rec(1000), {"tokens": 800}, OCT)["status"] == "warning"
    assert allowance.allowance_state(_rec(1000), {"tokens": 1000}, OCT)["status"] == "exhausted"
    assert allowance.allowance_state(_rec(1000), {"tokens": 1500}, OCT)["left_tokens"] == 0
    unl = allowance.allowance_state(_rec(0, 0.0), {"tokens": 10**9, "usd": 999.0}, OCT)
    assert unl["status"] == "unlimited" and unl["pct"] is None
    assert unl["left_tokens"] is None and unl["left_usd"] is None
    usd_only = allowance.allowance_state(_rec(0, 10.0), {"tokens": 10**9, "usd": 8.0}, OCT)
    assert (usd_only["status"], usd_only["pct"], usd_only["left_usd"]) == ("warning", 80, 2.0)
    # The HIGHER ratio wins when both bounds are set.
    both = allowance.allowance_state(_rec(1000, 10.0), {"tokens": 500, "usd": 9.0}, OCT)
    assert (both["status"], both["pct"]) == ("warning", 90)
    # Garbage never raises.
    assert allowance.allowance_state(_rec("x", None), {"tokens": "y"}, OCT)["status"] == "unlimited"


def test_refusal_wording_for_paused_and_exhausted(platform, registry):
    e = platform.engine
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=100, allowance_usd=2.0)
    rec = registry.get(NAME)
    assert allowance.refusal_for(rec, e, OCT) == ""
    registry.set_paused(NAME, "day off")
    assert allowance.refusal_for(registry.get(NAME), e, OCT) == "scout is paused: day off"
    registry.set_paused(NAME, "")
    assert allowance.refusal_for(registry.get(NAME), e, OCT) == "scout is paused"
    registry.resume(NAME)
    _seed(e, ROSTER, 100, 0, created_at=OCT, provider="mock", model="mock-1")
    text = allowance.refusal_for(registry.get(NAME), e, OCT)
    assert text == (
        "scout has used its monthly allowance (100 of 100 tokens / $0.00 of $2.00) "
        "— raise it on the Agents page or wait for November"
    )
    # December rolls to January; unlimited never refuses; None never raises.
    dec = datetime(2026, 12, 2)
    _seed(e, ROSTER, 100, 0, created_at=dec, provider="mock", model="mock-1")
    assert allowance.refusal_for(registry.get(NAME), e, dec).endswith("wait for January")
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=0, allowance_usd=0.0)
    assert allowance.refusal_for(registry.get(NAME), e, OCT) == ""
    assert allowance.refusal_for(None, e, OCT) == ""


# --------------------------------------------------------------------------- #
# 4. after_run: pause once, warn once per month
# --------------------------------------------------------------------------- #


async def test_after_run_pauses_once_and_publishes_agent_paused_once(platform, registry):
    e = platform.engine
    bus = _Bus()
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=100)
    _seed(e, ROSTER, 120, 0, created_at=OCT, provider="mock", model="mock-1")
    state = await allowance.after_run(registry, e, bus, ROSTER, OCT)
    assert state["status"] == "exhausted" and state["action"] == "paused"
    rec = registry.get(NAME)
    assert rec.paused_reason == "monthly allowance used up (120 of 100 tokens)"
    assert bus.events == [(
        "agent.paused",
        {
            "name": NAME, "spent_tokens": 120, "spent_usd": 0.0,
            "allowance_tokens": 100, "allowance_usd": 0.0, "month": "2026-10",
            "reason": "monthly allowance used up (120 of 100 tokens)",
        },
    )]
    # A second run while paused publishes NOTHING more and keeps the reason.
    again = await allowance.after_run(registry, e, bus, ROSTER, OCT)
    assert again["action"] == "" and len(bus.events) == 1
    assert registry.get(NAME).paused_reason == rec.paused_reason
    # A user pause set by hand is also respected (not overwritten).
    registry.set_paused(NAME, "day off")
    await allowance.after_run(registry, e, bus, ROSTER, OCT)
    assert registry.get(NAME).paused_reason == "day off" and len(bus.events) == 1
    # Not a custom name / unknown slug / unmetered → None, nothing published.
    assert await allowance.after_run(registry, e, bus, "builder", OCT) is None
    assert await allowance.after_run(registry, e, bus, "custom:nobody", OCT) is None
    registry.register("free", PROMPT, TOOLS)
    assert await allowance.after_run(registry, e, bus, "custom:free", OCT) is None
    assert len(bus.events) == 1


async def test_after_run_warns_once_per_month_and_again_next_month(platform, registry):
    e = platform.engine
    bus = _Bus()
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=1000)
    _seed(e, ROSTER, 850, 0, created_at=OCT, provider="mock", model="mock-1")
    state = await allowance.after_run(registry, e, bus, ROSTER, OCT)
    assert state["action"] == "warned"
    assert bus.events == [(
        "agent.allowance_warning",
        {
            "name": NAME, "spent_tokens": 850, "spent_usd": 0.0,
            "allowance_tokens": 1000, "allowance_usd": 0.0, "month": "2026-10",
            "pct": 85,
        },
    )]
    assert registry.get(NAME).allowance_warned_month == "2026-10"
    assert registry.get(NAME).paused_reason == ""  # a warning is not a pause
    second = await allowance.after_run(registry, e, bus, ROSTER, OCT)
    assert second["action"] == "" and len(bus.events) == 1
    # A new month: fresh spend, fresh warning.
    _seed(e, ROSTER, 820, 0, created_at=NOV, provider="mock", model="mock-1")
    third = await allowance.after_run(registry, e, bus, ROSTER, NOV)
    assert third["action"] == "warned" and len(bus.events) == 2
    assert bus.events[1][1]["month"] == "2026-11" and bus.events[1][1]["pct"] == 82
    assert registry.get(NAME).allowance_warned_month == "2026-11"


# --------------------------------------------------------------------------- #
# 5. the doors refuse with the reason and create NO session
# --------------------------------------------------------------------------- #


async def test_spawn_agent_refuses_a_paused_agent_with_the_reason(platform, registry, tmp_path):
    registry.register(NAME, PROMPT, TOOLS)
    registry.set_paused(NAME, "day off")
    before = len(_sessions(platform.engine))
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok is False
    assert result.error == "scout is paused: day off"
    assert len(_sessions(platform.engine)) == before
    # Exhausted refuses too, in the allowance words.
    registry.resume(NAME)
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=10)
    _seed(platform.engine, ROSTER, 10, 0, provider="mock", model="mock-1")
    before = len(_sessions(platform.engine))
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok is False and result.error.startswith(
        "scout has used its monthly allowance (10 of 10 tokens)"
    )
    assert len(_sessions(platform.engine)) == before
    # Resumed and under the allowance, it runs.
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=0)
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error


async def test_delegate_refuses_a_paused_dynamic_agent_with_the_reason(platform, registry, tmp_path):
    registry.register(NAME, PROMPT, TOOLS)
    registry.set_paused(NAME, "day off")
    tool = DelegateTool(platform)
    before = len(_sessions(platform.engine))
    result = await tool.execute(
        {"agent_type": ROSTER, "task": "summarize"}, _ctx(platform, tmp_path)
    )
    assert result.ok is False
    assert result.error == "scout is paused: day off"
    assert len(_sessions(platform.engine)) == before
    # The bare slug resolves the same way.
    result = await tool.execute({"agent_type": NAME, "task": "x"}, _ctx(platform, tmp_path))
    assert result.ok is False and result.error == "scout is paused: day off"
    # Exhausted (still healthy on the roster) refuses in the allowance words.
    registry.resume(NAME)
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=10)
    _seed(platform.engine, ROSTER, 10, 0, provider="mock", model="mock-1")
    before = len(_sessions(platform.engine))
    result = await tool.execute({"agent_type": ROSTER, "task": "x"}, _ctx(platform, tmp_path))
    assert result.ok is False
    assert result.error.startswith("scout has used its monthly allowance (10 of 10 tokens)")
    assert len(_sessions(platform.engine)) == before


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _add_schedule(client, name, payload):
    r = client.post(
        "/schedules",
        json={"name": name, "cron": "0 9 * * *", "kind": "task", "payload": payload},
    )
    assert r.status_code in (200, 201), r.text


def test_schedule_fire_refuses_a_paused_agent_honestly(tmp_path):
    client = _client(tmp_path)
    p = client.app.state.platform
    p.agents_registry.register(NAME, PROMPT, ["read_file"], provider="mock", model="mock-1")
    _add_schedule(client, "scout-rounds", {"task": "Morning rounds.", "agent_type": NAME})
    p.agents_registry.set_paused(NAME, "day off")
    ran = client.post("/schedules/scout-rounds/run").json()
    assert ran["last_status"] == "error", ran
    assert "scout is paused: day off" in ran["last_detail"]
    assert ran["last_session_id"] == ""
    assert client.get("/sessions").json()["sessions"] == []
    # Resumed, the same schedule runs.
    p.agents_registry.resume(NAME)
    ran = client.post("/schedules/scout-rounds/run").json()
    assert ran["last_status"] == "ok", ran


# --------------------------------------------------------------------------- #
# 6. the doors pass the job card's max_steps / approval_mode
# --------------------------------------------------------------------------- #


def _session_row(engine, roster_name: str) -> Session:
    rows = [s for s in _sessions(engine) if s.agent_name == roster_name]
    assert rows, f"no session credited to {roster_name}"
    return rows[-1]


async def test_spawn_and_delegate_pass_the_job_card_max_steps_and_posture(platform, registry, tmp_path):
    registry.register(NAME, PROMPT, TOOLS, max_steps=3, approval_mode="always_ask")
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error
    row = _session_row(platform.engine, ROSTER)
    assert row.max_steps == 3 and row.approval_mode == "always_ask"

    result = await DelegateTool(platform).execute(
        {"agent_type": ROSTER, "task": "summarize"}, _ctx(platform, tmp_path, "parent2")
    )
    assert result.ok, result.error
    row = _session_row(platform.engine, ROSTER)
    assert row.max_steps == 3 and row.approval_mode == "always_ask"


def test_schedule_fire_passes_the_job_card_max_steps_and_posture(tmp_path):
    client = _client(tmp_path)
    p = client.app.state.platform
    p.agents_registry.register(
        NAME, PROMPT, ["read_file"], provider="mock", model="mock-1",
        max_steps=4, approval_mode="approve_for_me",
    )
    _add_schedule(client, "scout-rounds", {"task": "Morning rounds.", "agent_type": NAME})
    ran = client.post("/schedules/scout-rounds/run").json()
    assert ran["last_status"] == "ok", ran
    row = next(s for s in _sessions(p.engine) if s.id == ran["last_session_id"])
    assert row.max_steps == 4
    assert row.approval_mode == "approve_for_me"
    # A builtin fire passes nothing (the absent-agent path is byte-identical).
    _add_schedule(client, "plain", {"task": "As before."})
    ran = client.post("/schedules/plain/run").json()
    assert ran["last_status"] == "ok", ran
    row = next(s for s in _sessions(p.engine) if s.id == ran["last_session_id"])
    assert row.max_steps is None and row.approval_mode == ""


# --------------------------------------------------------------------------- #
# 7. the auto-pause fires from the post-run tail after a REAL offline run
# --------------------------------------------------------------------------- #


async def test_auto_pause_fires_after_a_real_run_pushes_spend_over_the_allowance(platform, registry, tmp_path):
    e = platform.engine
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=1000)
    _seed(e, ROSTER, 500, 0, provider="mock", model="mock-1")  # this month, 50%
    seen = _spy_complete(platform, {}, usage={"input_tokens": 600, "output_tokens": 0})
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize the project"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error  # the door let it through at 50%
    assert seen["systems"], "the mock was really called"
    # The child's spend landed on ITS row…
    child = _session_row(e, ROSTER)
    assert child.input_tokens >= 600
    # …the tail paused the agent and said so in the ledger, exactly once.
    rec = registry.get(NAME)
    assert rec.paused_reason.startswith("monthly allowance used up (")
    paused = _events(e, "agent.paused")
    assert len(paused) == 1 and paused[0]["name"] == NAME
    assert paused[0]["allowance_tokens"] == 1000 and paused[0]["spent_tokens"] >= 1100
    assert _events(e, "agent.allowance_warning") == []  # exhausted outranks warning
    # The NEXT spawn is refused in the pause words; no new session.
    before = len(_sessions(e))
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "again"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok is False and result.error.startswith("scout is paused: monthly allowance used up")
    assert len(_sessions(e)) == before


async def test_warning_fires_from_the_tail_once(platform, registry, tmp_path):
    e = platform.engine
    registry.register(NAME, PROMPT, TOOLS, allowance_tokens=1000)
    _seed(e, ROSTER, 700, 0, provider="mock", model="mock-1")
    _spy_complete(platform, {}, usage={"input_tokens": 60, "output_tokens": 0})
    for _ in range(2):
        result = await platform.registry.invoke(
            "spawn_agent", {"agent": NAME, "task": "summarize"},
            _ctx(platform, tmp_path), platform.permissions,
        )
        assert result.ok, result.error
    warnings = _events(e, "agent.allowance_warning")
    assert len(warnings) == 1 and warnings[0]["name"] == NAME and warnings[0]["pct"] >= 80
    assert registry.get(NAME).paused_reason == ""
    assert registry.get(NAME).allowance_warned_month == allowance.month_key()


# --------------------------------------------------------------------------- #
# 8. deny_tools → "deny" overrides, never wider; skills + manager → the prompt
# --------------------------------------------------------------------------- #


def test_deny_tools_reach_permission_overrides_as_deny_and_cannot_widen(platform, registry):
    registry.register(NAME, PROMPT, TOOLS, deny_tools=["shell", "write_file", "pane_send"])
    d = registry.definition(NAME)
    assert d.permission_overrides == {"shell": "deny", "write_file": "deny", "pane_send": "deny"}
    assert set(d.permission_overrides.values()) == {"deny"}
    engine = PermissionEngine({"write_file": "allow", "shell": "ask", "pane_send": "ask"})
    assert engine.mode_for("write_file", d.permission_overrides) is PermissionMode.DENY
    assert engine.mode_for("shell", d.permission_overrides) is PermissionMode.DENY
    assert engine.mode_for("pane_send", d.permission_overrides) is PermissionMode.DENY
    # Nothing on the record can RAISE a floor tool: every override the
    # registry ever composes is "deny", so the floor's own rule (an "allow"
    # override is dropped) is never even reached.
    for tool in DENY_FLOOR_TOOLS:
        registry.register(NAME, PROMPT, TOOLS, deny_tools=[tool])
        assert registry.definition(NAME).permission_overrides == {tool: "deny"}
    # Control: with no deny list the base policy stands.
    registry.register(NAME, PROMPT, TOOLS, deny_tools=[])
    assert registry.definition(NAME).permission_overrides == {}
    assert engine.mode_for("write_file", {}) is PermissionMode.ALLOW


async def test_skills_and_manager_reach_the_system_prompt(platform, registry, tmp_path):
    platform.skills._skills["tidy-notes"] = Skill(
        name="tidy-notes", description="tidy", instructions="ALWAYS-TIDY-MARK-1295",
        dir=Path(tmp_path),
    )
    registry.register(NAME, PROMPT, TOOLS, skills=["tidy-notes", "no-such-skill"], reports_to="builder")
    d = registry.definition(NAME)
    assert d.skills == ["tidy-notes", "no-such-skill"] and d.reports_to == "builder"
    seen = _spy_complete(platform, {})
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error
    system = seen["systems"][0]
    assert "ALWAYS-TIDY-MARK-1295" in system
    assert (
        "Your manager is builder. When you are blocked or finished, say so plainly "
        "— builder and the user read your result." in system
    )
    # Control: no card → no skill block for it, no manager line.
    registry.register(NAME, PROMPT, TOOLS, skills=[], reports_to="")
    seen["systems"].clear()
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path, "parent2"), platform.permissions,
    )
    assert result.ok, result.error
    assert "ALWAYS-TIDY-MARK-1295" not in seen["systems"][0]
    assert "Your manager is" not in seen["systems"][0]


# --------------------------------------------------------------------------- #
# 9. the per-run CLI budget
# --------------------------------------------------------------------------- #


def _msgs():
    return [LLMMessage(role="user", content="hello")]


def _claude(seen: list):
    def runner(argv, stdin=None):
        seen.append((list(argv), stdin))
        return 0, json.dumps({"result": "answer", "usage": {}}), ""

    return sc.ClaudeCliAdapter(model="claude-opus-4-8", runner=runner, which=lambda b: "claude.exe")


def test_claude_argv_carries_the_budget_flag_only_when_armed():
    seen: list = []
    a = _claude(seen)
    token = budget.set_run_budget(1.234)
    try:
        asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    finally:
        budget.reset_run_budget(token)
    asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    token = budget.set_run_budget(0.03)  # below the CLI's floor
    try:
        asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
    finally:
        budget.reset_run_budget(token)
    armed, bare, tiny = seen
    argv = armed[0]
    assert argv[argv.index("--max-budget-usd") + 1] == "1.23"
    assert "--max-budget-usd" not in bare[0]
    assert "--max-budget-usd" not in tiny[0]
    # The prompt still rides stdin — no positional prompt in any argv.
    for argv, stdin in seen:
        assert stdin and "hello" in stdin
        assert not any("hello" in part for part in argv)
    assert budget.run_budget() == 0.0  # reset after every arm


def test_codex_argv_never_carries_the_budget_flag():
    token = budget.set_run_budget(5.0)
    try:
        assert "--max-budget-usd" not in sc._codex_argv("p", "m")
        seen: list = []

        def runner(argv, stdin=None):
            seen.append(list(argv))
            return 0, "answer", ""

        a = sc.make_codex_cli(runner=runner, which=lambda b: "codex.exe")
        asyncio.run(a.complete(system="s", messages=_msgs(), tools=[]))
        assert seen and "--max-budget-usd" not in seen[0]
    finally:
        budget.reset_run_budget(token)


def test_budget_contextvar_helpers():
    assert budget.run_budget() == 0.0
    t = budget.set_run_budget(2.5)
    assert budget.run_budget() == 2.5
    budget.reset_run_budget(t)
    assert budget.run_budget() == 0.0
    t = budget.set_run_budget(-3)
    assert budget.run_budget() == 0.0
    budget.reset_run_budget(t)
    t = budget.set_run_budget("nope")  # type: ignore[arg-type]
    assert budget.run_budget() == 0.0
    budget.reset_run_budget(t)


async def test_runtime_arms_what_is_left_of_the_usd_allowance_for_the_run(platform, registry, tmp_path):
    e = platform.engine
    registry.register(NAME, PROMPT, TOOLS, allowance_usd=5.0)
    # $0.50 spent this month on a priced model → $4.50 left.
    _seed(e, ROSTER, 100_000, 0, provider="anthropic", model="claude-opus-4-8")
    spent = cost_for("anthropic", "claude-opus-4-8", 100_000, 0)
    assert spent == pytest.approx(0.5)
    seen = _spy_complete(platform, {})
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error
    assert seen["budgets"] and all(b == pytest.approx(4.5) for b in seen["budgets"])
    assert budget.run_budget() == 0.0  # reset once the run ended
    # A builtin run arms nothing.
    seen["budgets"].clear()
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": "builder", "task": "summarize"},
        _ctx(platform, tmp_path, "parent2"), platform.permissions,
    )
    assert result.ok, result.error
    assert seen["budgets"] and all(b == 0.0 for b in seen["budgets"])


# --------------------------------------------------------------------------- #
# 10. the roster
# --------------------------------------------------------------------------- #


def test_roster_marks_a_paused_agent_and_carries_the_allowance(platform, registry):
    registry.register(NAME, PROMPT, TOOLS, description="digger", allowance_tokens=1000, reports_to="builder")
    _seed(platform.engine, ROSTER, 840, 0, provider="mock", model="mock-1")
    entry = next(e for e in build_roster(platform) if e.name == ROSTER)
    assert entry.paused is False and entry.healthy is True and entry.pause_reason == ""
    assert entry.reports_to == "builder"
    assert entry.allowance["status"] == "warning" and entry.allowance["pct"] == 84
    assert entry.allowance["spent_tokens"] == 840 and entry.allowance["tokens"] == 1000
    assert entry.line() == "custom:scout — digger (no runs yet)"  # line() unchanged
    assert resolve_target(platform, ROSTER) is not None
    registry.set_paused(NAME, "day off")
    entry = next(e for e in build_roster(platform) if e.name == ROSTER)
    assert entry.paused is True and entry.healthy is False
    assert entry.pause_reason == "day off"
    assert entry.line() == "custom:scout — digger (paused: day off)"
    assert entry.as_dict()["paused"] is True and entry.as_dict()["allowance"]["status"] == "warning"
    assert resolve_target(platform, ROSTER) is None  # nobody hands it work
    builder = next(e for e in build_roster(platform) if e.name == "builder")
    assert builder.paused is False and builder.allowance is None and builder.reports_to == ""


# --------------------------------------------------------------------------- #
# 11. the PHONE door (reviewer blocker): a paused agent is refused to the
#     phone and nothing runs; an exhausted-after-this-run agent is paused
# --------------------------------------------------------------------------- #

from iron_jarvis.agents.consult_tool import ConsultTool  # noqa: E402
from iron_jarvis.agents import threads as threads_mod  # noqa: E402
from iron_jarvis.comm import InboundPoller, TelegramChannel  # noqa: E402
from iron_jarvis.providers.adapters.base import LLMResponse  # noqa: E402

PHONE = 42


class _FakeTelegram:
    """tests/test_phone_job_background_v1291.py's fake phone, trimmed."""

    def __init__(self, updates):
        self.updates = list(updates)
        self.sent = []

    def get(self, url, params):
        offset = int(params.get("offset", 0) or 0)
        if offset:
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
        return {"ok": True, "result": list(self.updates)}

    def post(self, url, payload):
        self.sent.append(payload)
        return {"status_code": 200}

    def texts(self):
        return [str(p.get("text") or "") for p in self.sent]


def _update(uid, text):
    return {
        "update_id": uid,
        "message": {
            "text": text,
            "from": {"id": PHONE, "is_bot": False, "first_name": "V"},
            "chat": {"id": PHONE},
        },
    }


def _phone(fake):
    return TelegramChannel(
        {"token_secret": "tg", "inbound_enabled": True, "allowed_senders": [PHONE],
         "chat_id": PHONE, "chat_enabled": True},
        http_post=fake.post, http_get=fake.get,
        secret_resolver=lambda n: "BOT" if n == "tg" else None,
    )


async def _escalate_to_scout(platform_, personas, body):
    return {
        "reply": "this needs scout", "provider": "mock", "model": "m", "tools_used": [],
        "escalate": True, "escalate_reason": "multi-step", "escalate_agent": ROSTER,
    }


def _final_with_usage(tokens: int):
    async def fake_stream(*, provider=None, model=None, system, messages, tools, **kw):
        resp = LLMResponse(text="Done.")
        resp.usage = {"input_tokens": tokens, "output_tokens": 0}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


async def test_phone_escalation_to_a_paused_agent_is_refused_and_runs_nothing(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    poller: InboundPoller = app.state.inbound_poller
    p.agents_registry.register(NAME, PROMPT, ["read_file"], provider="mock", model="mock-1")
    p.agents_registry.set_paused(NAME, "day off")
    poller.chat_turn = _escalate_to_scout
    fake = _FakeTelegram([_update(1, "scout, do the rounds")])
    p.notifier.add_channel("tg", _phone(fake))
    results = await poller.poll_once()
    await poller.drain()
    assert results and results[0]["status"] == "chat_refused", results
    assert results[0]["reason"] == "scout is paused: day off"
    assert any(text.endswith("scout is paused: day off") for text in fake.texts()), fake.texts()
    assert _sessions(p.engine) == []  # not the supervisor, not anything


async def test_phone_escalation_spend_lands_and_the_tail_pauses_the_agent(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    poller: InboundPoller = app.state.inbound_poller
    p.agents_registry.register(
        NAME, PROMPT, ["read_file"], provider="mock", model="mock-1", allowance_tokens=1000
    )
    _seed(p.engine, ROSTER, 500, 0, provider="mock", model="mock-1")
    p.router.stream = _final_with_usage(600)
    poller.chat_turn = _escalate_to_scout
    fake = _FakeTelegram([_update(1, "scout, do the rounds")])
    p.notifier.add_channel("tg", _phone(fake))
    results = await poller.poll_once()
    assert results and results[0]["status"] == "chat_escalated", results
    await poller.drain()
    row = _session_row(p.engine, ROSTER)
    assert row.id == results[0]["session_id"]
    assert row.input_tokens == 600  # the spend is on the row
    assert p.agents_registry.get(NAME).paused_reason.startswith("monthly allowance used up (")
    paused = _events(p.engine, "agent.paused")
    assert len(paused) == 1 and paused[0]["name"] == NAME
    # The next phone escalation is refused in the pause words; no new row.
    fake.updates.append(_update(2, "scout, again"))
    before = len(_sessions(p.engine))
    results = await poller.poll_once()
    await poller.drain()
    assert results[0]["status"] == "chat_refused" and results[0]["reason"].startswith(
        "scout is paused: monthly allowance used up"
    )
    assert len(_sessions(p.engine)) == before


# --------------------------------------------------------------------------- #
# 12. the round table seat and consult (reviewer should-fix)
# --------------------------------------------------------------------------- #


def test_round_table_seat_of_a_paused_agent_answers_the_pause_sentence_without_a_model(tmp_path):
    client = _client(tmp_path)
    p = client.app.state.platform
    client.post("/agents", json={"name": NAME, "system_prompt": PROMPT})
    p.agents_registry.set_paused(NAME, "day off")
    calls: list = []
    real_get = p.providers.get
    p.providers.get = lambda *a, **k: (calls.append(a), real_get(*a, **k))[1]
    tid = client.post(
        "/agents/threads",
        json={"participants": [{"source": "dynamic", "name": NAME, "role": "lead"}]},
    ).json()["id"]
    body = client.post(f"/agents/threads/{tid}/say", json={"message": "hello"}).json()
    seat = [e for e in body["entries"] if e["who"] != "user"]
    assert len(seat) == 1
    assert seat[0]["content"] == "scout is paused: day off"
    assert not seat[0].get("error")
    assert calls == []  # no model call
    # Resumed, the seat speaks through the model again.
    p.agents_registry.resume(NAME)
    body = client.post(f"/agents/threads/{tid}/say", json={"message": "hello again"}).json()
    seat = [e for e in body["entries"] if e["who"] != "user"]
    assert seat[0]["content"] != "scout is paused: day off" and calls


async def test_consult_refuses_a_paused_agent_with_the_pause_sentence(platform, registry, tmp_path):
    registry.register(NAME, PROMPT, TOOLS)
    registry.set_paused(NAME, "day off")
    seen: list = []
    real = platform.router.complete

    async def spy(**kw):
        seen.append(kw)
        return await real(**kw)

    platform.router.complete = spy
    tool = ConsultTool(platform)
    for target in (ROSTER, NAME):
        res = await tool.execute(
            {"agent": target, "question": "what is a K-1?"}, _ctx(platform, tmp_path)
        )
        assert res.ok is False
        assert res.error == "scout is paused: day off"
    assert seen == []
    assert _sessions(platform.engine) == []
    registry.resume(NAME)
    res = await tool.execute({"agent": ROSTER, "question": "what is a K-1?"}, _ctx(platform, tmp_path))
    assert res.ok, res.error
