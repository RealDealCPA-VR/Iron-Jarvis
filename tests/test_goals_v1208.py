"""Goals (G1, v1.208.0): contract + hard budget + verifier + restart-surviving
lifecycle — offline, mock provider throughout.

What is pinned here, in the order the module docstrings promise it:

* the deny floor is refused in ``allowed_grants`` at WRITE time (and again at
  spawn time for a hand-edited row);
* a fresh budget requires an explicit bound or an explicit ``unlimited: true``;
* the budget gate refuses BEFORE spawning, for all three bounds, with an
  honest event, and the state stays active;
* ``kind:"checks"`` satisfaction runs through the WORKFLOWS verified-steps
  machinery — mutation-pinned: poisoning ``WorkflowEngine._expect_failure``
  changes the goal verdict, proving that implementation is THE one called;
* ``kind:"manual"`` never auto-satisfies;
* the breaker trips on 3 failures in the window, blocks further iterations,
  and prunes stale failures out of the window;
* the checkpoint carries deterministically into the next iteration's task;
* ``rehydrate()`` reconciles a goal stranded mid-iteration by a restart;
* state transitions are guarded (no satisfied→active without ``reopen``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.goals.engine import (
    GOAL_ITERATION_COMPLETED,
    GOAL_ITERATION_REFUSED,
    GOAL_ITERATION_STARTED,
    GOAL_SATISFIED,
    GOAL_TRIPPED,
    GoalEngine,
)
from iron_jarvis.goals.models import GoalContractRecord
from iron_jarvis.goals.store import GoalStore, goal_view

TOKENS_BUDGET = {"max_tokens": 1_000_000}


@pytest.fixture
def engine(platform, orchestrator):
    return GoalEngine(platform, orchestrator)


def _events(platform, type_, goal_id=None):
    out = []
    for e in platform.event_bus.history:
        if e.type != type_:
            continue
        if goal_id is not None and e.payload.get("goal_id") != goal_id:
            continue
        out.append(e)
    return out


def _edit_goal(platform, goal_id, **cols):
    """Simulate a hand-edited / older-build row (bypasses store validation)."""
    with session_scope(platform.engine) as db:
        row = db.get(GoalContractRecord, goal_id)
        for key, val in cols.items():
            setattr(row, key, val)
        db.add(row)
        db.commit()


async def _failing_run(session):
    session.status = SessionStatus.FAILED
    session.summary = "boom"
    return session


# ---------------------------------------------------------------------------
# write-time validation
# ---------------------------------------------------------------------------


def test_deny_floor_tools_refused_in_allowed_grants_at_write(platform):
    store = GoalStore(platform.engine)
    for tool in ("shell", "repl", "browser_use", "web_action", "mcp_call"):
        with pytest.raises(ValueError, match="deny floor"):
            store.create(
                name="g",
                contract_text="do a thing",
                allowed_grants=[tool],
                budget=TOKENS_BUDGET,
            )
    # A benign grant is fine — the floor refuses the floor, not the feature.
    goal = store.create(
        name="g",
        contract_text="do a thing",
        allowed_grants=["list_folder"],
        budget=TOKENS_BUDGET,
    )
    assert goal.decoded_grants() == ["list_folder"]


def test_budget_requires_an_explicit_bound_or_explicit_unlimited(platform):
    store = GoalStore(platform.engine)

    def make(budget):
        return store.create(name="g", contract_text="do a thing", budget=budget)

    with pytest.raises(ValueError, match="budget"):
        make(None)  # forgot entirely
    with pytest.raises(ValueError, match="budget"):
        make({})  # empty is not a choice
    with pytest.raises(ValueError, match="positive"):
        make({"max_tokens": -1})
    with pytest.raises(ValueError, match="contradicts"):
        make({"unlimited": True, "max_tokens": 5})  # never both
    assert make({"unlimited": True}).state == "active"  # deliberate unlimited
    assert make({"max_dollars": 2.5}).state == "active"


def test_checks_verifier_must_actually_check_something(platform):
    store = GoalStore(platform.engine)
    for bad in ([], [{"bogus": 1}], "not-a-list"):
        with pytest.raises(ValueError):
            store.create(
                name="g",
                contract_text="do a thing",
                budget=TOKENS_BUDGET,
                verifier={"kind": "checks", "checks": bad},
            )
    with pytest.raises(ValueError, match="kind"):
        store.create(
            name="g",
            contract_text="do a thing",
            budget=TOKENS_BUDGET,
            verifier={"kind": "vibes"},
        )


def test_goal_table_is_registered_in_metadata(platform):
    # The coordinator adds "..goals.models" to core.db._LATE_MODEL_MODULES;
    # this pins that importing the module is sufficient for the reconciler
    # (and _ensure_table already built it on this fresh engine).
    from sqlmodel import SQLModel

    import iron_jarvis.goals.models  # noqa: F401

    # NOT "goalrecord": the Motivation Layer owns that name for its own,
    # different GoalRecord — the explicit __tablename__ is what keeps the two
    # from colliding in one MetaData.
    assert "goalcontract" in SQLModel.metadata.tables


# ---------------------------------------------------------------------------
# the hard budget gate (before spawn, honest event, all three bounds)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("budget", "seed", "bound_name"),
    [
        ({"max_tokens": 5}, {"tokens": 5}, "max_tokens"),
        ({"max_dollars": 0.01}, {"dollars": 0.01}, "max_dollars"),
        ({"max_wallclock_s": 1}, {"wallclock_s": 2.0}, "max_wallclock_s"),
    ],
)
async def test_budget_gate_refuses_before_spawning(
    engine, platform, monkeypatch, budget, seed, bound_name
):
    goal = engine.store.create(name="g", contract_text="do a thing", budget=budget)
    engine.store.add_spend(goal.id, iterations=0, **seed)

    async def _never_spawn(*a, **k):  # the gate must fire BEFORE create_session
        raise AssertionError("a budget-exhausted goal must not spawn a session")

    monkeypatch.setattr(engine.orch, "create_session", _never_spawn)
    result = await engine.run_iteration(goal.id)

    assert result["refused"] is True and result["ok"] is False
    assert bound_name in result["reason"]  # names the exact exhausted bound
    assert engine.store.get(goal.id).state == "active"  # a budget is not a failure
    refusals = _events(platform, GOAL_ITERATION_REFUSED, goal.id)
    assert refusals and bound_name in refusals[-1].payload["reason"]


# ---------------------------------------------------------------------------
# a real iteration: spend accounting, origin, project threading, checkpoint
# ---------------------------------------------------------------------------


async def test_iteration_accounts_spend_from_the_session_row(engine, platform):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        project_id="project_abc123",
        budget=TOKENS_BUDGET,
    )
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True and result["status"] == "completed"

    with session_scope(platform.engine) as db:
        session = db.get(Session, result["session_id"])
    assert session.origin == f"goal:{goal.id}"
    assert session.project_id == "project_abc123"  # context spine threaded

    spent = engine.store.get(goal.id).decoded_spent()
    assert spent["iterations"] == 1
    assert spent["tokens"] == int(session.input_tokens) + int(session.output_tokens)
    assert spent["wallclock_s"] > 0

    checkpoint = engine.store.get(goal.id).decoded_checkpoint()
    assert checkpoint["last_session_id"] == session.id
    assert "running_session_id" not in checkpoint  # marker cleared on completion
    assert "RESULT.md" in checkpoint["files"]  # from the ledger, not prose
    started = _events(platform, GOAL_ITERATION_STARTED, goal.id)
    assert started and started[0].session_id == session.id  # session-tagged


async def test_checkpoint_carries_into_the_next_iterations_task(engine, platform):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,  # manual verifier: stays active after success
    )
    first = await engine.run_iteration(goal.id)
    second = await engine.run_iteration(goal.id)
    assert first["ok"] and second["ok"]

    with session_scope(platform.engine) as db:
        task2 = db.get(Session, second["session_id"]).task
    assert "Progress so far (deterministic checkpoint)" in task2
    assert first["session_id"] in task2  # the previous session, by id
    assert "RESULT.md" in task2  # the ledger's files
    assert "report precisely what remains" in task2


# ---------------------------------------------------------------------------
# the verifier
# ---------------------------------------------------------------------------


async def test_satisfied_via_checks_using_the_workflows_machinery(engine, platform):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={
            "kind": "checks",
            "checks": [{"files": ["RESULT.md"], "summary_contains": ["RESULT.md"]}],
        },
    )
    result = await engine.run_iteration(goal.id)
    assert result["satisfied"] is True
    assert engine.store.get(goal.id).state == "satisfied"
    assert _events(platform, GOAL_SATISFIED, goal.id)


async def test_the_workflows_expect_implementation_is_the_one_called(
    engine, platform, monkeypatch
):
    """Mutation-style pin: poisoning WorkflowEngine._expect_failure flips the
    goal verdict — so the verifier cannot silently become a re-implementation
    that drifts from the workflows vocabulary."""
    from iron_jarvis.workflows.engine import WorkflowEngine

    monkeypatch.setattr(
        WorkflowEngine, "_expect_failure", lambda self, step, out: "mutation-detector"
    )
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "checks", "checks": [{"files": ["RESULT.md"]}]},
    )
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True  # the session completed…
    assert result["satisfied"] is False  # …but the poisoned checker's verdict ruled
    assert result["unmet"] == "mutation-detector"
    assert engine.store.get(goal.id).state == "active"


async def test_manual_verifier_never_auto_satisfies(engine, platform):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "manual"},
    )
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True and result["satisfied"] is False
    assert engine.store.get(goal.id).state == "active"
    assert not _events(platform, GOAL_SATISFIED, goal.id)


# ---------------------------------------------------------------------------
# the circuit breaker
# ---------------------------------------------------------------------------


async def test_breaker_trips_on_three_failures_and_blocks_iterations(
    engine, platform, monkeypatch
):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    monkeypatch.setattr(engine, "_run_session", _failing_run)

    assert (await engine.run_iteration(goal.id))["state"] == "active"
    assert (await engine.run_iteration(goal.id))["state"] == "active"
    third = await engine.run_iteration(goal.id)
    assert third["state"] == "tripped"

    record = engine.store.get(goal.id)
    assert record.state == "tripped"
    breaker = record.decoded_breaker()
    assert len(breaker["failures"]) == 3 and breaker.get("tripped_at")
    tripped_events = _events(platform, GOAL_TRIPPED, goal.id)
    assert tripped_events and "failed" in tripped_events[-1].payload["reason"]

    # Tripped BLOCKS further iterations, honestly.
    fourth = await engine.run_iteration(goal.id)
    assert fourth["refused"] is True and "tripped" in fourth["reason"]
    refusals = _events(platform, GOAL_ITERATION_REFUSED, goal.id)
    assert "reopen" in refusals[-1].payload["reason"]


async def test_goal_view_serves_trip_reason_canonically(engine, platform, monkeypatch):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    view = goal_view(engine.store.get(goal.id))
    assert view["trip_reason"] is None  # the key is ALWAYS present; None until tripped
    assert view["state"] == "active" and view["budget"] == TOKENS_BUDGET

    monkeypatch.setattr(engine, "_run_session", _failing_run)
    for _ in range(3):
        await engine.run_iteration(goal.id)

    view = goal_view(engine.store.get(goal.id))
    assert view["state"] == "tripped"
    assert view["trip_reason"] and "failed" in view["trip_reason"]
    assert view["breaker"]["reason"] == view["trip_reason"]  # the fallback address
    assert len(view["breaker"]["failures"]) == 3 and view["breaker"]["tripped_at"]


async def test_breaker_window_prunes_stale_failures(engine, platform, monkeypatch):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    stale = (utcnow() - timedelta(hours=2)).isoformat()
    _edit_goal(
        platform, goal.id, breaker_json=json.dumps({"failures": [stale, stale]})
    )
    monkeypatch.setattr(engine, "_run_session", _failing_run)
    result = await engine.run_iteration(goal.id)
    assert result["state"] == "active"  # 2 stale + 1 fresh is NOT 3-in-window
    assert len(engine.store.get(goal.id).decoded_breaker()["failures"]) == 1


# ---------------------------------------------------------------------------
# spawn-time floor re-check (a hand-edited row must not run)
# ---------------------------------------------------------------------------


async def test_hand_edited_floor_grant_is_refused_at_spawn_time(engine, platform):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    _edit_goal(platform, goal.id, allowed_grants_json=json.dumps(["shell"]))
    result = await engine.run_iteration(goal.id)
    assert result["refused"] is True and "deny floor" in result["reason"]
    assert _events(platform, GOAL_ITERATION_REFUSED, goal.id)


# ---------------------------------------------------------------------------
# state-transition guards
# ---------------------------------------------------------------------------


async def test_state_transitions_are_guarded(engine, platform):
    store = engine.store
    goal = store.create(name="g", contract_text="do a thing", budget=TOKENS_BUDGET)

    assert store.transition(goal.id, "paused").state == "paused"
    paused = await engine.run_iteration(goal.id)  # paused refuses to iterate
    assert paused["refused"] is True and "paused" in paused["reason"]
    assert store.transition(goal.id, "active").state == "active"

    store.transition(goal.id, "satisfied")
    with pytest.raises(ValueError, match="reopen"):
        store.transition(goal.id, "active")  # no silent resurrection
    assert store.reopen(goal.id).state == "active"  # the explicit door

    store.transition(goal.id, "stopped")
    with pytest.raises(ValueError):
        store.transition(goal.id, "paused")  # stopped is terminal via transition
    assert store.reopen(goal.id).state == "active"

    # Reopen clears the breaker so a revived goal doesn't instantly re-trip.
    _edit_goal(
        platform,
        goal.id,
        state="tripped",
        breaker_json=json.dumps({"failures": [utcnow().isoformat()] * 3}),
    )
    with pytest.raises(ValueError, match="reopen"):
        store.transition(goal.id, "active")
    assert store.reopen(goal.id).decoded_breaker()["failures"] == []

    with pytest.raises(ValueError, match="unknown goal state"):
        store.transition(goal.id, "zombie")


# ---------------------------------------------------------------------------
# restart survival
# ---------------------------------------------------------------------------


async def test_rehydrate_reconciles_a_goal_stranded_mid_iteration(
    engine, platform, orchestrator
):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    # Simulate the crash: a session was created for the goal and never ran.
    session = await orchestrator.create_session(
        "do a thing", origin=f"goal:{goal.id}"
    )
    engine.store.set_checkpoint(goal.id, {"running_session_id": session.id})

    # Boot order: the session layer reconciles first (marks it FAILED)…
    orchestrator.reconcile_interrupted_sessions()
    with session_scope(platform.engine) as db:
        assert db.get(Session, session.id).status is SessionStatus.FAILED

    # …then the goal layer records ONE honest breaker failure and stays active.
    assert engine.rehydrate() == 1
    record = engine.store.get(goal.id)
    assert record.state == "active"
    breaker = record.decoded_breaker()
    assert len(breaker["failures"]) == 1
    assert breaker["last_reason"] == "interrupted by a daemon restart"
    checkpoint = record.decoded_checkpoint()
    assert "running_session_id" not in checkpoint
    assert checkpoint["last_session_id"] == session.id

    # A goal already carrying 2 recent failures TRIPS on the interruption.
    goal2 = engine.store.create(
        name="g2", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    session2 = await orchestrator.create_session("do", origin=f"goal:{goal2.id}")
    orchestrator.reconcile_interrupted_sessions()
    recent = utcnow().isoformat()
    _edit_goal(
        platform,
        goal2.id,
        breaker_json=json.dumps({"failures": [recent, recent]}),
        checkpoint_json=json.dumps({"running_session_id": session2.id}),
    )
    assert engine.rehydrate() == 1
    assert engine.store.get(goal2.id).state == "tripped"


async def test_rehydrate_accounts_a_session_that_finished_before_the_crash(
    engine, platform, orchestrator
):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    session = await orchestrator.run("Create a file summarizing the task.")
    assert session.status is SessionStatus.COMPLETED
    engine.store.set_checkpoint(goal.id, {"running_session_id": session.id})

    assert engine.rehydrate() == 1
    record = engine.store.get(goal.id)
    assert record.state == "active"
    assert record.decoded_breaker()["failures"] == []  # finishing is not a failure
    spent = record.decoded_spent()
    assert spent["iterations"] == 1
    assert spent["tokens"] == int(session.input_tokens) + int(session.output_tokens)
    assert record.decoded_checkpoint()["last_session_id"] == session.id


async def test_rehydrate_never_double_bills_an_accounted_session(engine, platform):
    """D7: a crash BETWEEN add_spend and set_checkpoint leaves the running
    marker up with the spend already committed. The billed-flag travels in the
    SAME write as the balance (spent_json.last_session_id), so rehydrate can
    tell 'billed, marker stranded' from 'crash beat the billing' — and never
    charges the same session twice."""
    goal = engine.store.create(
        name="g", contract_text="Create a file summarizing the task.", budget=TOKENS_BUDGET
    )
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True
    spent_before = engine.store.get(goal.id).decoded_spent()

    # Simulate the crash window: the marker is back up, the spend already in.
    checkpoint = engine.store.get(goal.id).decoded_checkpoint()
    checkpoint["running_session_id"] = result["session_id"]
    engine.store.set_checkpoint(goal.id, checkpoint)

    assert engine.rehydrate() == 1
    record = engine.store.get(goal.id)
    assert record.decoded_spent() == spent_before  # billed ONCE, not twice
    assert record.decoded_breaker()["failures"] == []  # and not a "failure"
    assert "running_session_id" not in record.decoded_checkpoint()


# ---------------------------------------------------------------------------
# the schedule is a kept promise (D2)
# ---------------------------------------------------------------------------


async def test_schedule_lifecycle_is_owned_end_to_end(engine, platform):
    goal = await engine.create_goal(
        name="g",
        contract_text="do a thing",
        budget=TOKENS_BUDGET,
        schedule="0 9 * * *",
    )
    name = f"goal:{goal.id}"
    row = platform.scheduler.get(name)
    assert row is not None, "a goal sold with a cron must own a REAL scheduler row"
    assert row.kind == "goal"
    assert row.decoded_payload() == {"goal_id": goal.id}
    assert row.cron == "0 9 * * *" and row.enabled is True

    # Enabled IFF active: pause/stop disable, resume/reopen re-enable.
    engine.store.transition(goal.id, "paused")
    assert platform.scheduler.get(name).enabled is False
    engine.store.transition(goal.id, "active")  # the resume route's exact path
    assert platform.scheduler.get(name).enabled is True

    await engine.stop_goal(goal.id)
    assert platform.scheduler.get(name).enabled is False
    engine.store.reopen(goal.id)
    assert platform.scheduler.get(name).enabled is True

    # A trip silences the cron too — reopen brings both back.
    recent = utcnow().isoformat()
    _edit_goal(
        platform, goal.id, breaker_json=json.dumps({"failures": [recent, recent]})
    )
    _, tripped = engine.store.record_failure(goal.id, "boom")
    assert tripped and platform.scheduler.get(name).enabled is False

    # Deleting the goal deletes the row — no orphan cron firing for a ghost.
    assert engine.store.remove(goal.id) is True
    assert platform.scheduler.get(name) is None


async def test_a_rejected_cron_refuses_the_whole_create(engine, platform):
    with pytest.raises(ValueError, match="not accepted"):
        await engine.create_goal(
            name="g",
            contract_text="do a thing",
            budget=TOKENS_BUDGET,
            schedule="definitely not a cron",
        )
    assert engine.store.list() == []  # the goal row was undone, not stranded
    assert not [t for t in platform.scheduler.list() if t.name.startswith("goal:")]


# ---------------------------------------------------------------------------
# stopped/paused DURING the run (D3) and stop cancels the run (D5/D8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interrupt_state", ["stopped", "paused"])
async def test_state_change_during_the_run_records_but_never_transitions(
    engine, platform, monkeypatch, interrupt_state
):
    """D3: the user stops/pauses WHILE the session runs and the checks
    verifier then PASSES — the old code transitioned blind and crashed on the
    guard. Now: result recorded (spend, checkpoint, event with the note),
    state untouched, satisfied stays false."""
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "checks", "checks": [{"files": ["RESULT.md"]}]},
    )
    orig = GoalEngine._run_session

    async def mid_run(session):
        engine.store.transition(goal.id, interrupt_state)
        return await orig(engine, session)

    monkeypatch.setattr(engine, "_run_session", mid_run)
    result = await engine.run_iteration(goal.id)

    assert result["ok"] is True and result["satisfied"] is False
    assert interrupt_state in result["note"] and "state unchanged" in result["note"]
    record = engine.store.get(goal.id)
    assert record.state == interrupt_state  # NOT satisfied, and no crash
    spent = record.decoded_spent()
    assert spent["iterations"] == 1  # the work is still on the books
    assert record.decoded_checkpoint()["last_session_id"] == result["session_id"]
    done = _events(platform, GOAL_ITERATION_COMPLETED, goal.id)
    assert done and "state unchanged" in done[-1].payload["note"]
    assert not _events(platform, GOAL_SATISFIED, goal.id)


async def test_stop_goal_cancels_the_running_iteration(
    engine, platform, orchestrator, monkeypatch
):
    """D5: 'Stop always works' — a running iteration's session is cancelled,
    not merely barred from future runs. D8: tokens the run recorded before
    the stop are read off the session row and billed. Cancelled-by-stop is
    NOT a breaker failure."""
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )
    started = asyncio.Event()

    async def hang(session_id, definition=None):
        # The run recorded usage on its row before the user hit Stop (D8).
        with session_scope(platform.engine) as db:
            row = db.get(Session, session_id)
            row.input_tokens, row.output_tokens = 7, 5
            db.add(row)
            db.commit()
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(orchestrator, "run_session", hang)
    iteration = asyncio.create_task(engine.run_iteration(goal.id))
    await asyncio.wait_for(started.wait(), 5)

    stopped = await engine.stop_goal(goal.id)
    assert stopped.state == "stopped"
    result = await asyncio.wait_for(iteration, 5)

    assert result["ok"] is False and result["status"] == "cancelled"
    assert result["state"] == "stopped"
    record = engine.store.get(goal.id)
    assert record.decoded_breaker()["failures"] == []  # the user's stop, not a failure
    spent = record.decoded_spent()
    assert spent["tokens"] == 12 and spent["iterations"] == 1  # D8
    assert "running_session_id" not in record.decoded_checkpoint()


# ---------------------------------------------------------------------------
# a crash that ESCAPES the session layer (D6)
# ---------------------------------------------------------------------------


async def test_provider_crash_is_a_breaker_failure_with_the_real_reason(
    engine, platform, monkeypatch
):
    goal = engine.store.create(
        name="g", contract_text="do a thing", budget=TOKENS_BUDGET
    )

    async def blow_up(session):
        raise RuntimeError("provider down")

    monkeypatch.setattr(engine, "_run_session", blow_up)
    result = await engine.run_iteration(goal.id)

    assert result["ok"] is False and result["status"] == "failed"
    assert "provider down" in result["reason"]  # the REAL reason, relayed
    record = engine.store.get(goal.id)
    breaker = record.decoded_breaker()
    assert len(breaker["failures"]) == 1
    assert "provider down" in breaker["last_reason"]
    spent = record.decoded_spent()
    assert spent["iterations"] == 1  # the attempt is on the books
    checkpoint = record.decoded_checkpoint()
    assert "running_session_id" not in checkpoint  # no stranded marker…
    assert checkpoint["last_session_id"] == result["session_id"]
    assert engine.rehydrate() == 0  # …so no FALSE "interrupted" record later
    done = _events(platform, GOAL_ITERATION_COMPLETED, goal.id)
    assert done and "provider down" in done[-1].payload["error"]

    # Three crashes in the window trip the breaker like any other failure.
    await engine.run_iteration(goal.id)
    third = await engine.run_iteration(goal.id)
    assert third["state"] == "tripped"
    assert _events(platform, GOAL_TRIPPED, goal.id)
