"""Motivation Layer ("the pulse") tests — fully offline with the mock model.

Safety is the whole point, so the suite proves the off-by-default + suggest +
budget + kill-switch + dry-run guarantees, plus the event->backlog mapping and
the approve->session path. Nothing here waits on a real cron tick or network.
"""

from __future__ import annotations

# Register the Motivation tables on SQLModel.metadata BEFORE init_db. Top of file.
import iron_jarvis.motivation.models  # noqa: F401

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.events import EventType
from iron_jarvis.motivation.engine import IntentEngine
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


@pytest.fixture
def engine(platform):
    """IntentEngine wired with a real orchestrator (the executor)."""
    eng = IntentEngine(platform, orchestrator=Orchestrator(platform))
    platform.intent = eng  # so the platform.intent.on_event handler uses this one
    return eng


def _enable(platform, **over):
    platform.config.autonomy_enabled = True
    for k, v in over.items():
        setattr(platform.config, k, v)


# -- 1. OFF by default: nothing runs -----------------------------------------


async def test_off_by_default_deliberate_noop(platform, engine):
    engine.add_goal("keep the docs current", priority=5)
    out = await engine.deliberate()
    assert out == {"ran": False, "reason": "autonomy_disabled"}
    assert engine.list_proposals() == []


def test_build_platform_creates_no_proposals(platform):
    # A freshly built platform (autonomy off) holds nothing — no boot-time pulse.
    assert platform.intent is not None
    assert platform.intent.list_proposals() == []
    assert platform.intent.list_goals() == []


# -- 2. SUGGEST by default: a proposal, never a session ----------------------


async def test_suggest_mode_creates_proposal_not_session(platform, engine):
    _enable(platform)  # global level stays "suggest"
    engine.add_goal("write a weekly summary", priority=4)
    out = await engine.deliberate()
    assert out["ran"] is True
    assert out["executed"] is False
    props = engine.list_proposals(status="pending")
    assert len(props) == 1
    assert props[0].status == "pending"
    # No session was spawned by a suggest-mode pulse.
    assert engine.orchestrator.list_sessions() == []


async def test_deliberate_offline_is_deterministic_and_safe(platform, engine):
    _enable(platform)
    engine.add_goal("tidy the backlog")
    out = await engine.deliberate()
    p = engine.get_proposal(out["proposal_id"])
    assert p is not None and p.title and p.decoded_action()["task"]
    assert p.risk in ("low", "med", "high")


# -- 3. Auto-execute only when dial + risk + budget all permit ---------------


async def test_act_all_low_risk_auto_executes(platform, engine):
    _enable(platform, autonomy_level="act_all")
    g = engine.add_goal("draft a note", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "do it", "rationale": "r",
        "agent_type": "builder", "task": "write NOTES.md", "risk": "low",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is True
    assert out["session_id"]
    sess = engine.orchestrator.get_session(out["session_id"])
    assert sess is not None
    # Budget was booked on the goal.
    assert engine.get_goal(g.id).actions_taken == 1


async def test_auto_execute_background_actually_runs(platform, engine):
    # The daemon tick uses wait=False: the session must still RUN (in background).
    _enable(platform, autonomy_level="act_all")
    g = engine.add_goal("bg", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "t", "rationale": "r",
        "agent_type": "builder", "task": "write OUT.md", "risk": "low",
    }
    out = await engine.deliberate(wait=False)
    assert out["executed"] is True
    sid = out["session_id"]
    task = engine.orchestrator._running.get(sid)
    if task is not None:
        await task  # let the background run settle
    sess = engine.orchestrator.get_session(sid)
    assert sess.status.value in ("completed", "failed")


async def test_high_risk_never_auto_executes(platform, engine):
    _enable(platform, autonomy_level="act_all")
    g = engine.add_goal("risky", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "danger", "rationale": "r",
        "agent_type": "builder", "task": "rm -rf things", "risk": "high",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is False
    assert engine.get_proposal(out["proposal_id"]).status == "pending"


async def test_act_low_does_not_execute_med_risk(platform, engine):
    _enable(platform, autonomy_level="act_all")  # ceiling permissive
    g = engine.add_goal("careful", autonomy_level="act_low")  # goal dial is the floor
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "t", "rationale": "r",
        "agent_type": "builder", "task": "do", "risk": "med",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is False


async def test_global_level_caps_goal_dial(platform, engine):
    # Global ceiling "suggest" must override a goal set to act_all.
    _enable(platform, autonomy_level="suggest")
    g = engine.add_goal("eager", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "t", "rationale": "r",
        "agent_type": "builder", "task": "do", "risk": "low",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is False
    assert engine.effective_level(engine.get_goal(g.id)) == "suggest"


# -- 3b. Budget exhaustion blocks auto-execute -------------------------------


async def test_goal_budget_exhaustion_blocks(platform, engine):
    _enable(platform, autonomy_level="act_all")
    g = engine.add_goal("capped", autonomy_level="act_all")
    engine.update_goal(g.id, action_budget=1, actions_taken=1)  # already at cap
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "t", "rationale": "r",
        "agent_type": "builder", "task": "do", "risk": "low",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is False
    assert "budget" in out["auto_reason"]


async def test_global_action_budget_blocks(platform, engine):
    _enable(platform, autonomy_level="act_all", autonomy_max_actions_per_day=0)
    g = engine.add_goal("blocked", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "t", "rationale": "r",
        "agent_type": "builder", "task": "do", "risk": "low",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is False
    assert "global daily action budget" in out["auto_reason"]


# -- 3c. Kill switch + dry-run halt acting -----------------------------------


async def test_kill_switch_halts_everything(platform, engine):
    _enable(platform, autonomy_level="act_all", autonomy_kill_switch=True)
    engine.add_goal("anything", autonomy_level="act_all")
    out = await engine.deliberate(wait=True)
    assert out == {"ran": False, "reason": "kill_switch"}
    assert engine.list_proposals() == []


async def test_dry_run_proposes_but_never_executes(platform, engine):
    _enable(platform, autonomy_level="act_all", autonomy_dry_run=True)
    g = engine.add_goal("dry", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "t", "rationale": "r",
        "agent_type": "builder", "task": "do", "risk": "low",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is False
    assert out["auto_reason"] == "dry_run"
    assert out["dry_run"] is True


# -- 4. EventBus -> suggest-only backlog -------------------------------------


def test_event_backlog_mapping_only_when_enabled(platform, engine):
    fake = type("E", (), {"type": EventType.PROVIDER_FAILED, "payload": {}})()
    # Off: no backlog item created.
    assert engine.on_event(fake) is None
    assert engine.list_proposals() == []
    # On: a suggest-only backlog proposal appears, and it dedupes.
    platform.config.autonomy_enabled = True
    rec = engine.on_event(fake)
    assert rec is not None and rec.source == "event" and rec.status == "pending"
    assert engine.on_event(fake) is None  # non-spammy: deduped
    assert len(engine.list_proposals(status="pending")) == 1


# -- 5. Approve a proposal -> a session is created ---------------------------


async def test_approve_proposal_creates_session(platform, engine):
    _enable(platform)  # suggest: deliberate only proposes
    engine.add_goal("ship it", priority=5)
    out = await engine.deliberate()
    pid = out["proposal_id"]
    assert engine.get_proposal(pid).status == "pending"

    session = await engine.approve(pid, wait=True)
    assert session is not None
    assert engine.orchestrator.get_session(session.id) is not None
    assert engine.get_proposal(pid).status == "executed"


async def test_approve_blocked_by_kill_switch(platform, engine):
    _enable(platform)
    engine.add_goal("x")
    out = await engine.deliberate()
    platform.config.autonomy_kill_switch = True
    with pytest.raises(PermissionError):
        await engine.approve(out["proposal_id"], wait=False)


# -- 6. Goal CRUD + briefing -------------------------------------------------


def test_goal_crud_and_dial(platform, engine):
    g = engine.add_goal("learn my style", category="meta", priority=2)
    assert g.autonomy_level == "suggest" and g.status == "active"
    engine.update_goal(g.id, autonomy_level="act_low", status="paused", priority=5)
    g2 = engine.get_goal(g.id)
    assert g2.autonomy_level == "act_low" and g2.status == "paused" and g2.priority == 5
    assert engine.list_goals(status="active") == []


def test_briefing_summarises(platform, engine):
    engine.add_goal("a")
    b = engine.briefing()
    assert "morning briefing" in b["text"]
    assert b["active_goals"] == 1 and b["pending_proposals"] == 0


# -- 7. HTTP surface (daemon) ------------------------------------------------


def test_http_goals_proposals_and_kill(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    with TestClient(create_app(str(tmp_path))) as client:
        # Autonomy is off by default.
        st = client.get("/autonomy").json()
        assert st["enabled"] is False and st["level"] == "suggest"

        # A manual tick no-ops while off (no proposals created).
        assert client.post("/autonomy/tick").json()["ran"] is False

        g = client.post("/goals", json={"text": "keep things tidy", "priority": 4}).json()
        assert g["status"] == "active" and g["autonomy_level"] == "suggest"

        listed = client.get("/goals").json()["goals"]
        assert any(x["id"] == g["id"] for x in listed)

        # Dial it up via PATCH.
        patched = client.patch(
            f"/goals/{g['id']}", json={"autonomy_level": "act_low"}
        ).json()
        assert patched["autonomy_level"] == "act_low"

        # Kill switch engages + persists.
        assert client.post("/autonomy/kill", json={"enabled": True}).json()["kill_switch"] is True
        assert client.get("/autonomy").json()["kill_switch"] is True

        # Briefing renders.
        assert "briefing" in client.get("/autonomy/briefing").json()["text"]


# -- swarm-review fixes: dedupe + config enum validation ---------------------
async def test_deliberate_dedupes_pending_proposals(platform, engine):
    _enable(platform)  # suggest mode -> proposals only, no execution
    engine.add_goal("keep the docs current", priority=5)
    first = await engine.deliberate()
    second = await engine.deliberate()
    assert first["ran"] and not first.get("deduped")
    assert second["ran"] and second.get("deduped")  # identical action already queued
    assert second["proposal_id"] == first["proposal_id"]
    assert len(engine.list_proposals(status="pending")) == 1


def test_autonomy_level_rejects_bad_value(platform):
    with pytest.raises(Exception):
        platform.config.autonomy_level = "yolo"
    platform.config.autonomy_level = "act_low"  # a valid value still assigns
    assert platform.config.autonomy_level == "act_low"


async def test_approve_maintainer_proposal_fails_closed_without_self_dev(platform, engine):
    # A self-modifying (maintainer) proposal must NOT run when self_dev is off.
    _enable(platform)  # autonomy on; self_dev_enabled stays False (default)
    p = engine._create_proposal(
        goal_id=None, title="Fix a recurring tool failure", rationale="r",
        agent_type="maintainer", task="patch the failing tool", risk="high", source="event",
    )
    with pytest.raises(PermissionError):
        await engine.approve(p.id, wait=True)
    assert engine.get_proposal(p.id).status == "pending"  # fail-closed: not executed
