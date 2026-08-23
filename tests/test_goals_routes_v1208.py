"""``/goals`` is the GOAL-CONTRACT surface; the motivation intent goals moved
to ``/autonomy/goals`` (v1.208.0).

Pinned here:

* ``build_platform`` constructs ``platform.goal_engine`` (the routes and the
  ``kind="goal"`` schedule dispatch both reach it there);
* CRUD + verbs over the REAL store/engine (mock provider, offline): create
  echoes ``goal_view`` (``trip_reason`` key ALWAYS present), list filters by
  state and refuses an unknown state honestly, delete 404s twice;
* the store's write-time refusals reach the API as 400s with the store's
  sentence VERBATIM — the route must not paraphrase the deny floor or the
  budget rule;
* ``POST /goals/{id}/run`` relays the engine result: a real iteration
  completes end to end, and a budget-exhausted goal answers 200 with
  ``{ok: false, refused: true, reason}`` — an honest refusal is a result,
  not an error;
* verb lifecycle: pause/resume/stop/reopen; transition-guard refusals are
  409s VERBATIM; resume on a TRIPPED goal reopens (breaker cleared — the
  dashboard promises "Resume clears it");
* path separation BOTH ways on one app: the legacy intent endpoints answer
  at ``/autonomy/goals`` (handlers verbatim) and their old shapes no longer
  answer at ``/goals`` (legacy body -> 422, legacy PATCH -> 405), while
  ``GET /goals`` serves only contract goals;
* scheduler dispatch: ``kind="goal"`` fires ``run_iteration``; a fire whose
  goal is GONE is an honest recorded SKIP (loop alive, fire again works);
  a failed iteration records an error without raising into the scheduler.

Registered on a bare FastAPI app (the tests/test_helpdocs_v1198.py /
test_envelope_routes_v1201.py idiom) — the coordinating session owns
daemon/app.py and wires ``_routes.goals.register(app, d)`` after this lands.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.routes import autonomy as autonomy_routes
from iron_jarvis.daemon.routes import goals as goals_routes
from iron_jarvis.goals.engine import GoalEngine
from iron_jarvis.goals.models import (
    GoalContractRecord,
    budget_violation,
    grants_violation,
)
from iron_jarvis.scheduling.models import ScheduledTaskRecord

TOKENS_BUDGET = {"max_tokens": 1_000_000}


# --------------------------------------------------------------------------- #
# fixtures — REAL platform + orchestrator (mock provider, offline)
# --------------------------------------------------------------------------- #


@pytest.fixture
def goal_engine(platform, orchestrator):
    """The engine build_platform constructed, wired to the shared orchestrator
    exactly the way the daemon does (``goal_engine._orch = orchestrator``)."""
    platform.goal_engine._orch = orchestrator
    return platform.goal_engine


@pytest.fixture
def client(platform, goal_engine) -> TestClient:
    app = FastAPI()
    goals_routes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _intent_goal_view(g):
    """The app.py legacy view shape (enough of it to pin the wire)."""
    return {
        "id": g.id,
        "text": g.text,
        "source": g.source,
        "category": g.category,
        "priority": g.priority,
        "autonomy_level": g.autonomy_level,
        "status": g.status,
    }


@pytest.fixture
def dual_client(platform, goal_engine) -> TestClient:
    """BOTH modules on one app — the shape create_app will have — so the
    path separation is pinned independent of registration order games."""
    app = FastAPI()
    d = SimpleNamespace(platform=platform, _goal_view=_intent_goal_view)
    goals_routes.register(app, d)
    autonomy_routes.register(app, d)
    return TestClient(app)


def _create(client, **over):
    body = {
        "name": "inbox zero",
        "contract_text": "Create a file summarizing the task.",
        "budget": TOKENS_BUDGET,
        **over,
    }
    return client.post("/goals", json=body)


def _edit_goal(platform, goal_id, **cols):
    """Hand-edit a row (bypasses store validation — the older-build shape)."""
    with session_scope(platform.engine) as db:
        row = db.get(GoalContractRecord, goal_id)
        for key, val in cols.items():
            setattr(row, key, val)
        db.add(row)
        db.commit()


# --------------------------------------------------------------------------- #
# platform wiring
# --------------------------------------------------------------------------- #


def test_build_platform_constructs_the_goal_engine(platform):
    assert isinstance(platform.goal_engine, GoalEngine)
    # Same DB engine as everything else — one truth, not a parallel store.
    assert platform.goal_engine.store.engine is platform.engine
    assert platform.goal_engine.store.list() == []


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def test_create_list_filter_delete_roundtrip(client):
    r = _create(client)
    assert r.status_code == 200, r.text
    goal = r.json()["goal"]
    assert goal["name"] == "inbox zero"
    assert goal["state"] == "active"
    assert goal["budget"] == TOKENS_BUDGET
    assert goal["verifier"] == {"kind": "manual", "checks": []}
    assert "trip_reason" in goal and goal["trip_reason"] is None  # key ALWAYS present

    listed = client.get("/goals").json()["goals"]
    assert [g["id"] for g in listed] == [goal["id"]]
    assert client.get("/goals", params={"state": "active"}).json()["goals"]
    assert client.get("/goals", params={"state": "paused"}).json()["goals"] == []

    # An unknown state must not silently answer "no goals".
    bad = client.get("/goals", params={"state": "zombie"})
    assert bad.status_code == 400
    assert "unknown goal state" in bad.json()["detail"]

    assert client.delete(f"/goals/{goal['id']}").json() == {"deleted": goal["id"]}
    assert client.get("/goals").json()["goals"] == []
    assert client.delete(f"/goals/{goal['id']}").status_code == 404


def test_create_publishes_goal_created(client, platform):
    goal = _create(client).json()["goal"]
    created = [e for e in platform.event_bus.history if e.type == "goal.created"]
    assert created and created[-1].payload["goal_id"] == goal["id"]


# --------------------------------------------------------------------------- #
# the store's refusals reach the API VERBATIM (no paraphrase, no re-derivation)
# --------------------------------------------------------------------------- #


def test_deny_floor_grant_is_a_400_with_the_stores_words(client):
    r = _create(client, allowed_grants=["shell"])
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == grants_violation(["shell"])
    assert "deny floor" in r.json()["detail"]


def test_budgetless_goal_is_a_400_with_the_stores_words(client):
    body = {"name": "g", "contract_text": "do a thing"}  # no budget at all
    r = client.post("/goals", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == budget_violation(None)


def test_blank_contract_and_empty_checks_are_400s(client):
    r = client.post("/goals", json={"contract_text": "  ", "budget": TOKENS_BUDGET})
    assert r.status_code == 400 and "contract_text" in r.json()["detail"]
    r = _create(client, verifier={"kind": "checks", "checks": []})
    assert r.status_code == 400 and "at least one check" in r.json()["detail"]
    # The legacy intent-goal body has no contract_text: 422 at THIS surface —
    # it belongs to /autonomy/goals now.
    assert client.post("/goals", json={"text": "keep tidy"}).status_code == 422


# --------------------------------------------------------------------------- #
# run now
# --------------------------------------------------------------------------- #


def test_run_now_runs_a_real_iteration(client, goal_engine):
    goal = _create(client).json()["goal"]
    r = client.post(f"/goals/{goal['id']}/run")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["ok"] is True and result["status"] == "completed"
    assert result["session_id"]
    assert result["spent"]["iterations"] == 1
    fresh = client.get("/goals").json()["goals"][0]
    assert fresh["last_run_at"]  # accounted on the row, not just in the reply


def test_run_refusal_is_a_200_result_not_an_error(client, goal_engine):
    goal = _create(client, budget={"max_tokens": 5}).json()["goal"]
    goal_engine.store.add_spend(goal["id"], tokens=5, iterations=0)
    r = client.post(f"/goals/{goal['id']}/run")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["ok"] is False and result["refused"] is True
    assert "max_tokens" in result["reason"]
    assert result["state"] == "active"  # a budget is a boundary, not a failure


# --------------------------------------------------------------------------- #
# verbs + guards
# --------------------------------------------------------------------------- #


def test_pause_resume_stop_reopen_lifecycle(client, platform):
    gid = _create(client).json()["goal"]["id"]

    assert client.post(f"/goals/{gid}/pause").json()["goal"]["state"] == "paused"
    refused = client.post(f"/goals/{gid}/run").json()
    assert refused["refused"] is True and "paused" in refused["reason"]
    assert client.post(f"/goals/{gid}/resume").json()["goal"]["state"] == "active"

    assert client.post(f"/goals/{gid}/stop").json()["goal"]["state"] == "stopped"
    stopped = [e for e in platform.event_bus.history if e.type == "goal.stopped"]
    assert stopped and stopped[-1].payload["goal_id"] == gid  # through the ENGINE

    assert client.post(f"/goals/{gid}/reopen").json()["goal"]["state"] == "active"
    again = client.post(f"/goals/{gid}/reopen")
    assert again.status_code == 409
    assert again.json()["detail"] == "goal is already active"


def test_transition_guard_refusals_are_409s_verbatim(client, goal_engine):
    gid = _create(client).json()["goal"]["id"]
    goal_engine.store.transition(gid, "satisfied")
    r = client.post(f"/goals/{gid}/pause")
    assert r.status_code == 409
    assert r.json()["detail"] == "a satisfied goal cannot become paused"
    r = client.post(f"/goals/{gid}/stop")
    assert r.status_code == 409
    assert r.json()["detail"] == "a satisfied goal cannot become stopped"
    # resume on satisfied stays behind the explicit reopen door.
    r = client.post(f"/goals/{gid}/resume")
    assert r.status_code == 409
    assert "reopen" in r.json()["detail"]


def test_resume_on_a_tripped_goal_reopens_and_clears_the_breaker(client, platform):
    gid = _create(client).json()["goal"]["id"]
    _edit_goal(
        platform,
        gid,
        state="tripped",
        breaker_json=json.dumps(
            {"failures": ["2026-08-22T00:00:00+00:00"] * 3, "last_reason": "it broke"}
        ),
    )
    shown = client.get("/goals").json()["goals"][0]
    assert shown["state"] == "tripped" and shown["trip_reason"] == "it broke"

    resumed = client.post(f"/goals/{gid}/resume").json()["goal"]
    assert resumed["state"] == "active"
    assert resumed["breaker"]["failures"] == []  # Resume clears it — the promise
    assert resumed["trip_reason"] is None


def test_unknown_goal_is_a_404_on_every_verb(client):
    for verb in ("pause", "resume", "stop", "reopen"):
        r = client.post(f"/goals/goal_missing/{verb}")
        assert r.status_code == 404, verb
        assert r.json()["detail"] == "goal not found"
    assert client.delete("/goals/goal_missing").status_code == 404


# --------------------------------------------------------------------------- #
# path separation, both ways (one app carrying BOTH modules)
# --------------------------------------------------------------------------- #


def test_legacy_intent_goals_answer_at_autonomy_goals(dual_client, platform):
    r = dual_client.post(
        "/autonomy/goals", json={"text": "keep things tidy", "priority": 4}
    )
    assert r.status_code == 200, r.text
    legacy = r.json()
    assert legacy["status"] == "active" and legacy["autonomy_level"] == "suggest"

    listed = dual_client.get("/autonomy/goals").json()["goals"]
    assert [g["id"] for g in listed] == [legacy["id"]]

    patched = dual_client.patch(
        f"/autonomy/goals/{legacy['id']}", json={"autonomy_level": "act_low"}
    ).json()
    assert patched["autonomy_level"] == "act_low"
    assert (
        dual_client.patch("/autonomy/goals/ghost", json={"priority": 1}).status_code
        == 404
    )


def test_the_two_goal_surfaces_never_leak_into_each_other(dual_client):
    legacy = dual_client.post("/autonomy/goals", json={"text": "keep tidy"}).json()
    contract = _create(dual_client).json()["goal"]

    new_ids = [g["id"] for g in dual_client.get("/goals").json()["goals"]]
    assert new_ids == [contract["id"]]  # no intent goals on the public surface
    legacy_ids = [g["id"] for g in dual_client.get("/autonomy/goals").json()["goals"]]
    assert legacy_ids == [legacy["id"]]  # and no contracts on the legacy one

    # The legacy shapes no longer answer at /goals: the intent body is a 422
    # (contract_text is required here) and the intent PATCH has no method —
    # /goals/{id} only takes DELETE on the new surface.
    assert dual_client.post("/goals", json={"text": "keep tidy"}).status_code == 422
    assert (
        dual_client.patch(
            f"/goals/{legacy['id']}", json={"autonomy_level": "act_low"}
        ).status_code
        == 405
    )


# --------------------------------------------------------------------------- #
# scheduler dispatch (kind="goal")
# --------------------------------------------------------------------------- #


def _install_goal_schedule(platform, name: str, goal_id: str) -> None:
    """A kind='goal' row, inserted directly (the coordinator teaches the
    service's KINDS vocabulary; the dispatcher reads the row either way)."""
    with session_scope(platform.engine) as db:
        db.add(
            ScheduledTaskRecord(
                name=name,
                cron="0 9 * * *",
                trigger_type="cron",
                kind="goal",
                payload_json=json.dumps({"goal_id": goal_id, "notify": False}),
            )
        )
        db.commit()


def _sched_row(platform, name: str) -> ScheduledTaskRecord:
    from sqlmodel import select

    with session_scope(platform.engine) as db:
        return db.exec(
            select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == name)
        ).first()


async def test_scheduler_dispatches_kind_goal_to_run_iteration(platform, monkeypatch):
    calls: list[str] = []

    async def fake_run_iteration(goal_id):
        calls.append(goal_id)
        return {
            "ok": True,
            "goal_id": goal_id,
            "session_id": "sess_x",
            "iteration": 3,
            "status": "completed",
            "state": "active",
            "satisfied": True,
            "spent": {},
        }

    monkeypatch.setattr(platform.goal_engine, "run_iteration", fake_run_iteration)
    _install_goal_schedule(platform, "goal-fire", "goal_abc123")

    await platform.scheduler.run_now("goal-fire")

    assert calls == ["goal_abc123"]
    row = _sched_row(platform, "goal-fire")
    assert row.last_status == "ok"
    assert row.last_session_id == "sess_x"  # the row links straight to the work
    assert "satisfied" in row.last_detail


async def test_goal_schedule_survives_a_deleted_goal_as_an_honest_skip(platform):
    """The goal is GONE (deleted after scheduling): the fire must record WHY
    nothing ran — never raise into the scheduler, never error forever."""
    _install_goal_schedule(platform, "goal-orphan", "goal_deleted")

    await platform.scheduler.run_now("goal-orphan")  # must not raise

    row = _sched_row(platform, "goal-orphan")
    assert row.last_status == "ok"  # a skip is not a scheduler malfunction
    assert "skipped" in row.last_detail
    assert "unknown goal" in row.last_detail

    # The loop is genuinely alive: the same row fires again without drama.
    await platform.scheduler.run_now("goal-orphan")
    assert _sched_row(platform, "goal-orphan").last_status == "ok"


async def test_goal_schedule_records_a_failed_iteration_without_raising(
    platform, monkeypatch
):
    async def failed_iteration(goal_id):
        return {
            "ok": False,
            "goal_id": goal_id,
            "session_id": "sess_dead",
            "iteration": 1,
            "status": "failed",
            "reason": "session sess_dead failed: boom",
            "state": "active",
        }

    monkeypatch.setattr(platform.goal_engine, "run_iteration", failed_iteration)
    _install_goal_schedule(platform, "goal-flaky", "goal_abc123")

    await platform.scheduler.run_now("goal-flaky")  # swallowed by _run_scheduled

    row = _sched_row(platform, "goal-flaky")
    assert row.last_status == "error"  # a session ran and died — that IS an error
    assert row.last_session_id == "sess_dead"
    assert "failed" in row.last_detail


async def test_goal_schedule_without_goal_id_is_a_recorded_error(platform):
    with session_scope(platform.engine) as db:
        db.add(
            ScheduledTaskRecord(
                name="goal-blank",
                cron="0 9 * * *",
                trigger_type="cron",
                kind="goal",
                payload_json=json.dumps({"notify": False}),
            )
        )
        db.commit()
    await platform.scheduler.run_now("goal-blank")
    row = _sched_row(platform, "goal-blank")
    assert row.last_status == "error"
    assert "goal_id" in row.last_detail
