"""v1.424.0 (review): a custom agent's OWN account at the doors that start
its work by themselves.

The lane put every custom agent's own account (``DynamicAgentRecord.account``)
under ``model_team.member_scope`` — the Agents page's Run button, a schedule's
fire, ``delegate`` / ``spawn_agent`` / ``consult`` — but the ASSIGNMENT
dispatcher (the Agents page's "Queue it", ``assign_work``) still ran a custom
agent that is not a team member with no pin: its runs leased Iron-Proxy's
first free account, an account the user did not choose. Now the dispatcher
runs it inside ``run_as_member`` like a member, so every lease is pinned to
its own account; an agent with no account of its own runs exactly as before.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.assignments.dispatcher import AssignmentDispatcher
from iron_jarvis.providers import router as router_mod
from tests._fakes.team_world_v1395 import make_world, member


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def w(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


async def _drain(d: AssignmentDispatcher) -> None:
    await d.tick()
    await d.drain()


async def test_a_queued_custom_agents_own_account_pins_its_run(w):
    w.p.orchestrator = Orchestrator(w.p)
    # NOT a team member (no roles) — an ordinary custom agent with its own account.
    member(w.p, "scout", "claude-cli", "", roles=(), account="c-spare")
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    w.p.assignments.create("custom:scout", "Read the notes.")
    await _drain(d)
    calls = [c for c in w.log if c["task"].startswith("Read the notes")]
    assert calls, w.log
    assert all(c["pin"] is not None and c["pin"].profile_id == "c-spare" for c in calls), calls


async def test_a_queued_custom_agent_with_no_account_runs_as_before(w):
    w.p.orchestrator = Orchestrator(w.p)
    member(w.p, "scout", "claude-cli", "", roles=())
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    w.p.assignments.create("custom:scout", "Read the notes.")
    await _drain(d)
    calls = [c for c in w.log if c["task"].startswith("Read the notes")]
    assert calls and all(c["pin"] is None for c in calls), calls
    assert all(c["hold"] is None for c in calls), calls
