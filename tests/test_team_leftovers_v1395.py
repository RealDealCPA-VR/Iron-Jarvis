"""v1.395.0: three items the Phase 5 review left open (the long-jobs plan).

1. ASSIGNMENTS and SCHEDULE FIRES ran a team member as any custom agent: its
   pin, but NO HOLD — so a failing member could fall back or fail over, and a
   job naming another model silently ran there. Now both run the member under
   ``model_team.run_as_member`` (its own model held, entered INSIDE the run's
   own task, so a run the governor parks keeps it) and refuse it in words
   BEFORE any session row (``model_team.queued_refusal``): a job naming
   another model, and Local only work for a member whose model leaves the
   network.
2. ``HeldProviderRefused`` said "This Build session runs on …" even when the
   hold was a MEMBER's own (an objective's teammate, a plain agent run, a
   member's helper). The words now fit where it happened.
3. The ``create_agent`` tool's upsert reset an existing agent's model,
   description and base type (its roles and account were already kept). A
   field the call does not name is kept now.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import model_team as T
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.assignments.dispatcher import AssignmentDispatcher
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import helpers as H
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.tools.base import ToolContext
from tests._fakes.team_world_v1395 import LOCAL, make_world, member, wire_platform


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def w(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


def _sessions(p) -> list[Session]:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(Session)))
        for r in rows:
            db.expunge(r)
        return rows


# --------------------------------------------------------------------------- #
# 1a. an ASSIGNMENT for a member runs held to its model
# --------------------------------------------------------------------------- #


async def _drain(d: AssignmentDispatcher) -> None:
    await d.tick()
    await d.drain()


async def test_a_queued_member_runs_held_to_its_own_model_and_account(w):
    w.p.orchestrator = Orchestrator(w.p)
    member(w.p, "codex", "codex-cli", "", roles=("code",), account="x-plus")
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    w.p.assignments.create("custom:codex", "Write util.py.")
    await _drain(d)
    calls = [c for c in w.log if c["task"].startswith("Write util.py")]
    assert calls, w.log
    # Its OWN model (the provider's default — never the configured default
    # model of another provider), HELD there, on its own account.
    assert all(c["provider"] == "codex-cli" for c in calls)
    assert all(c["hold"] == ("codex-cli", "default") for c in calls)
    assert all(c["pin"] is not None and c["pin"].profile_id == "x-plus" for c in calls)


async def test_a_queued_member_whose_model_fails_is_never_moved(w):
    w.p.orchestrator = Orchestrator(w.p)
    member(w.p, "codex", "codex-cli", "", roles=("code",))
    w.fail.add("codex-cli")
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    w.p.assignments.create("custom:codex", "Write util.py.")
    await _drain(d)
    assert w.log and {c["provider"] for c in w.log} == {"codex-cli"}, w.log


async def test_a_queued_job_naming_another_model_is_held_in_words_before_any_session(w):
    w.p.orchestrator = Orchestrator(w.p)
    member(w.p, "codex", "codex-cli", "", roles=("code",))
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    rec, _ = w.p.assignments.create("custom:codex", "Write it.", payload={"provider": "google"})
    out = await d.tick()
    assert out["held"] == 1 and not out["started"]
    row = w.p.assignments.get(rec.id)
    assert row.held_reason.startswith("'codex' is on your model team and runs on its own model (Codex), "
                                      "but this task names google.")
    assert not _sessions(w.p) and not w.log


async def test_a_local_only_parents_job_never_starts_a_cloud_member(w):
    orch = Orchestrator(w.p)
    w.p.orchestrator = orch
    member(w.p, "codex", "codex-cli", "", roles=("code",))
    parent = await orch.create_session("parent", AgentType.BUILDER, provider=LOCAL[0], model=LOCAL[1],
                                       options={"local_only": True})
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    rec, _ = w.p.assignments.create("custom:codex", "Write it.", source=f"agent:{parent.id}")
    out = await d.tick()
    assert out["held"] == 1
    assert "this work is Local only" in w.p.assignments.get(rec.id).held_reason
    assert len(_sessions(w.p)) == 1 and not w.log


# --------------------------------------------------------------------------- #
# 1b. a SCHEDULE FIRE for a member runs held to its model
# --------------------------------------------------------------------------- #


def _add_schedule(client, name, payload):
    r = client.post("/schedules", json={"name": name, "cron": "0 9 * * *", "kind": "task", "payload": payload})
    assert r.status_code in (200, 201), r.text


def test_a_scheduled_member_runs_held_to_its_own_model(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    world = wire_platform(client.app.state.platform, monkeypatch)
    member(world.p, "codex", "codex-cli", "", roles=("code",), account="x-plus")
    _add_schedule(client, "nightly", {"task": "Nightly sweep.", "agent_type": "codex"})
    ran = client.post("/schedules/nightly/run").json()
    assert ran["last_status"] == "ok", ran
    calls = [c for c in world.log if c["task"].startswith("Nightly sweep")]
    assert calls and all(c["hold"] == ("codex-cli", "default") for c in calls), world.log
    assert all(c["pin"] is not None and c["pin"].profile_id == "x-plus" for c in calls)


def test_a_schedule_naming_another_model_for_a_member_is_refused_in_words(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    world = wire_platform(client.app.state.platform, monkeypatch)
    member(world.p, "codex", "codex-cli", "", roles=("code",))
    _add_schedule(client, "odd", {"task": "Odd job.", "agent_type": "codex", "provider": "google"})
    ran = client.post("/schedules/odd/run").json()
    assert ran["last_status"] == "error", ran
    assert "'codex' is on your model team and runs on its own model" in ran["last_detail"]
    assert ran["last_session_id"] == "" and not world.log


def test_run_as_member_enters_the_hold_inside_the_run_it_wraps(w):
    """The scope is entered when the run STARTS (a parked run promoted later in
    a fresh context still gets it) and builds the run inside it."""
    import asyncio

    rec = member(w.p, "codex", "codex-cli", "", roles=("code",))
    seen = []

    async def run():
        hold = HOLD.current_hold()
        seen.append((hold.provider, hold.model, hold.who) if hold else None)
        return "done"

    made = []
    coro = T.run_as_member(w.p, rec, lambda: made.append(1) or run())
    assert made == []  # nothing is built until the run starts
    assert asyncio.run(coro) == "done"
    assert seen == [("codex-cli", "default", "member:codex")]
    assert HOLD.current_hold() is None


# --------------------------------------------------------------------------- #
# 2. the hold's refusal says where it happened
# --------------------------------------------------------------------------- #


async def _ask_google(p):
    return await p.router.complete(provider="google", system="s",
                                   messages=[LLMMessage(role="user", content="hi")], tools=[])


async def test_a_members_hold_names_the_member_not_a_build_session(w):
    rec = member(w.p, "codex", "codex-cli", "", roles=("code",))
    with T.member_scope(w.p, rec):
        with pytest.raises(HOLD.HeldProviderRefused) as caught:
            await _ask_google(w.p)
    words = str(caught.value)
    assert words == ("'codex' runs on codex-cli, its own model on your team, so its work is never sent to "
                     "google. Change its model in Settings › Model team if you want that.")
    assert "Build session" not in words


async def test_a_member_helper_in_a_coding_session_is_named_too_and_the_session_keeps_its_words(w, tmp_path):
    rec = member(w.p, "codex", "codex-cli", "", roles=("code",))
    parent = H.HelperParent(session_id="build:x", folder=str(tmp_path), provider="claude-cli",
                            model="claude-sonnet-4-5")
    with H.pinned(H.for_member(parent, w.p, rec)):
        with pytest.raises(HOLD.HeldProviderRefused) as caught:
            await _ask_google(w.p)
    assert str(caught.value).startswith("'codex' runs on codex-cli, its own model on your team")
    with H.pinned(parent):
        with pytest.raises(HOLD.HeldProviderRefused) as caught:
            await _ask_google(w.p)
    assert str(caught.value) == ("This Build session runs on claude-cli, so its work is never sent to "
                                 "google. Choose another model for the session if you want that.")


def test_the_hold_still_equals_by_provider_and_model():
    a = HOLD.ProviderHold("codex-cli", "m", "member:x")
    assert a == HOLD.ProviderHold("codex-cli", "m")
    assert HOLD.refusal_words("a", "b") == HOLD.refusal_words("a", "b", "member:")


# --------------------------------------------------------------------------- #
# 3. create_agent keeps what the call does not name
# --------------------------------------------------------------------------- #


def _ctx(p, tmp_path) -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r", config=p.config,
                       event_bus=p.event_bus, engine=p.engine)


async def test_create_agent_on_an_existing_member_keeps_its_model_roles_and_account(w, tmp_path):
    reg = w.p.agents_registry
    reg.register("codex", "You code.", ["read_file"], base_type="researcher", description="writes code",
                 provider="codex-cli", model="gpt-6", roles=["code", "test"], account="x-plus")
    res = await w.p.registry.get("create_agent").execute(
        {"name": "codex", "system_prompt": "You code, carefully.", "tools": ["read_file", "write_file"]},
        _ctx(w.p, tmp_path))
    assert res.ok, res.error
    rec = reg.get("codex")
    assert (rec.system_prompt, rec.tools_json) == ("You code, carefully.", '["read_file", "write_file"]')
    assert (rec.provider, rec.model, rec.base_type, rec.description) == (
        "codex-cli", "gpt-6", "researcher", "writes code")
    assert T.roles_of(rec) == ["code", "test"] and rec.account == "x-plus"
    # A field the call DOES name is changed.
    res = await w.p.registry.get("create_agent").execute(
        {"name": "codex", "system_prompt": "p", "tools": ["read_file"], "description": "", "base_type": "builder"},
        _ctx(w.p, tmp_path))
    assert res.ok
    rec = reg.get("codex")
    assert (rec.description, rec.base_type, rec.provider) == ("", "builder", "codex-cli")


async def test_create_agent_makes_a_new_agent_as_before(w, tmp_path):
    res = await w.p.registry.get("create_agent").execute(
        {"name": "fresh", "system_prompt": "p", "tools": ["read_file"]}, _ctx(w.p, tmp_path))
    assert res.ok
    rec = w.p.agents_registry.get("fresh")
    assert (rec.provider, rec.model, rec.base_type, rec.description) == ("", "", "builder", "")
    assert P.current() is None
