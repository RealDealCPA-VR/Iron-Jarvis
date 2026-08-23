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
from types import SimpleNamespace

from iron_jarvis.goals.engine import (
    _JUDGE_SYSTEM,
    GOAL_ITERATION_COMPLETED,
    GOAL_ITERATION_REFUSED,
    GOAL_ITERATION_STARTED,
    GOAL_SATISFIED,
    GOAL_TRIPPED,
    VERIFICATION_PENDING_NOTE,
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


def _script_judge(platform, monkeypatch, text, provider="anthropic"):
    """Script the G2 judge's one-shot, PASSING EVERY OTHER CALL THROUGH.

    The doer session's own perceive→act loop rides the same
    ``platform.router.complete``, so a blanket patch would sabotage the very
    run under verification — the fake dispatches on the judge's exact system
    prompt and delegates everything else to the real router. Returns the list
    of judge calls (kwargs) for briefing assertions.
    """
    calls: list[dict] = []
    real_complete = platform.router.complete

    async def fake_complete(**kwargs):
        if kwargs.get("system") == _JUDGE_SYSTEM:
            calls.append(kwargs)
            return SimpleNamespace(
                response=SimpleNamespace(text=text),
                provider=provider,
                model="judge-model",
            )
        return await real_complete(**kwargs)

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    return calls


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
    assert refusals[-1].payload["name"] == "g"  # the notifier's phone lines


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
    satisfied_events = _events(platform, GOAL_SATISFIED, goal.id)
    assert satisfied_events and satisfied_events[-1].payload["name"] == "g"


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
    assert tripped_events[-1].payload["name"] == "g"  # the notifier's phone lines

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
    # The fire-and-forget trip event carries the NAME too (rehydrate path).
    for _ in range(3):
        await asyncio.sleep(0)  # let _publish_bg's task run
    tripped_events = _events(platform, GOAL_TRIPPED, goal2.id)
    assert tripped_events and tripped_events[-1].payload["name"] == "g2"


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


# ---------------------------------------------------------------------------
# G2 (v1.209.0): the verifier ladder — adversarial and judged tiers
# ---------------------------------------------------------------------------

ADVERSARIAL_RESULT = {
    "kind": "adversarial",
    "checks": [{"files": ["RESULT.md"], "summary_contains": ["RESULT.md"]}],
}


async def test_adversarial_refute_gate_flips_the_outcome(
    engine, platform, monkeypatch
):
    """Mutation-check of the refute gate: two byte-identical goals, the ONLY
    difference is the scripted judge's verdict text — and the outcome flips.
    That pins the gate on the judge's answer, not on the checks alone."""
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier=ADVERSARIAL_RESULT,
    )
    calls = _script_judge(
        platform,
        monkeypatch,
        "VERDICT: REFUTED — the ledger shows no verification artifact",
    )
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True and result["satisfied"] is False
    assert "judge refuted satisfaction" in result["unmet"]
    assert "no verification artifact" in result["unmet"]  # the judge's reason
    assert engine.store.get(goal.id).state == "active"
    # The briefing is the contract + the deterministic checkpoint's evidence,
    # asked on the SESSION'S OWN provider (never-auto-switch) with no tools.
    assert calls, "the judge was never consulted"
    briefing = calls[0]["messages"][0].content
    assert "Create a file summarizing the task." in briefing
    assert "RESULT.md" in briefing
    assert calls[0]["provider"] == "mock"  # the session ran on mock
    assert calls[0]["tools"] == []
    done = _events(platform, GOAL_ITERATION_COMPLETED, goal.id)
    assert "judge refuted satisfaction" in done[-1].payload["unmet"]

    # Same setup — only the verdict text differs — and it satisfies.
    goal2 = engine.store.create(
        name="g2",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier=ADVERSARIAL_RESULT,
    )
    _script_judge(
        platform, monkeypatch, "VERDICT: SATISFIED — evidence matches the contract"
    )
    result2 = await engine.run_iteration(goal2.id)
    assert result2["satisfied"] is True
    assert engine.store.get(goal2.id).state == "satisfied"
    assert _events(platform, GOAL_SATISFIED, goal2.id)


async def test_adversarial_failed_checks_short_circuit_the_judge(
    engine, platform, monkeypatch
):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "adversarial", "checks": [{"files": ["MISSING.md"]}]},
    )
    calls = _script_judge(platform, monkeypatch, "VERDICT: SATISFIED")
    result = await engine.run_iteration(goal.id)
    assert result["satisfied"] is False
    assert "MISSING.md" in result["unmet"]  # tier 1's own failure wording
    assert calls == []  # the checks gate first — no judge call, no judge spend
    assert engine.store.get(goal.id).state == "active"


@pytest.mark.parametrize(
    "verifier",
    [
        {"kind": "adversarial", "checks": [{"files": ["RESULT.md"]}]},
        {"kind": "judged"},
    ],
)
async def test_no_real_provider_records_pending_never_a_verdict(
    engine, platform, verifier
):
    """HONEST-MOCK: the whole suite runs on the mock provider, so the judge
    CANNOT run — the iteration records the pending note and the goal is
    neither satisfied nor failed. No fabricated verdict, no silent
    fallthrough to checks-only."""
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier=verifier,
    )
    result = await engine.run_iteration(goal.id)  # REAL router; mock route
    assert result["ok"] is True
    assert result["satisfied"] is False
    assert result["pending"] == VERIFICATION_PENDING_NOTE
    assert "unmet" not in result  # pending is not a failed evaluation
    record = engine.store.get(goal.id)
    assert record.state == "active"  # not satisfied, NOT failed
    assert record.decoded_breaker()["failures"] == []
    done = _events(platform, GOAL_ITERATION_COMPLETED, goal.id)
    assert done[-1].payload["pending"] == VERIFICATION_PENDING_NOTE
    assert not _events(platform, GOAL_SATISFIED, goal.id)


async def test_judged_satisfaction_is_loudly_labeled(engine, platform, monkeypatch):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "judged"},
    )
    _script_judge(
        platform, monkeypatch, "VERDICT: SATISFIED — the recorded summary matches"
    )
    result = await engine.run_iteration(goal.id)
    assert result["satisfied"] is True
    view = goal_view(engine.store.get(goal.id))
    assert view["state"] == "satisfied"
    assert (
        view["verifier"]["judged_note"]
        == "satisfied by model judgment — no deterministic checks"
    )

    # An adversarial satisfaction WITH passing checks carries NO such label —
    # its checks are deterministic evidence anchoring the verdict.
    goal2 = engine.store.create(
        name="g2",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier=ADVERSARIAL_RESULT,
    )
    _script_judge(platform, monkeypatch, "VERDICT: SATISFIED — fine")
    await engine.run_iteration(goal2.id)
    view2 = goal_view(engine.store.get(goal2.id))
    assert view2["state"] == "satisfied"
    assert "judged_note" not in view2["verifier"]
    # And an UNSATISFIED judged goal is not labeled either.
    goal3 = engine.store.create(
        name="g3", contract_text="do", budget=TOKENS_BUDGET, verifier={"kind": "judged"}
    )
    assert "judged_note" not in goal_view(engine.store.get(goal3.id))["verifier"]


async def test_adversarial_with_zero_checks_is_labeled_like_judged(
    engine, platform, monkeypatch
):
    """D4: `adversarial` with ZERO checks is evidentially identical to
    `judged` — the judge is the only gate — so a satisfaction earned that way
    must carry the loud label too. The label follows the EVIDENCE, not the
    kind string: 'a model said so' must never wear the ledger's clothes."""
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "adversarial"},  # checks-optional tier, none given
    )
    _script_judge(platform, monkeypatch, "VERDICT: SATISFIED — the summary matches")
    result = await engine.run_iteration(goal.id)
    assert result["satisfied"] is True
    view = goal_view(engine.store.get(goal.id))
    assert view["state"] == "satisfied"
    assert (
        view["verifier"]["judged_note"]
        == "satisfied by model judgment — no deterministic checks"
    )
    # Not yet satisfied -> not yet labeled (the label reports an earned
    # satisfaction, never a prediction about one).
    goal2 = engine.store.create(
        name="g2", contract_text="do", budget=TOKENS_BUDGET,
        verifier={"kind": "adversarial"},
    )
    assert "judged_note" not in goal_view(engine.store.get(goal2.id))["verifier"]


async def test_unreadable_judge_reply_fails_closed(engine, platform, monkeypatch):
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "judged"},
    )
    _script_judge(platform, monkeypatch, "Looks good to me, ship it.")
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True and result["satisfied"] is False
    assert "unreadable" in result["unmet"]
    assert engine.store.get(goal.id).state == "active"


async def test_checks_tier_is_untouched_and_never_consults_the_judge(
    engine, platform, monkeypatch
):
    """Regression pin for tier 1: a REFUTING judge sits scripted and armed,
    and a plain checks goal satisfies without ever asking it — byte-identical
    G1 behavior."""
    goal = engine.store.create(
        name="g",
        contract_text="Create a file summarizing the task.",
        budget=TOKENS_BUDGET,
        verifier={"kind": "checks", "checks": [{"files": ["RESULT.md"]}]},
    )
    calls = _script_judge(
        platform, monkeypatch, "VERDICT: REFUTED — should never be read"
    )
    result = await engine.run_iteration(goal.id)
    assert result["satisfied"] is True
    assert engine.store.get(goal.id).state == "satisfied"
    assert calls == []


def test_verifier_tier_vocabulary_validation(platform):
    store = GoalStore(platform.engine)

    def make(verifier):
        return store.create(
            name="g", contract_text="do a thing", budget=TOKENS_BUDGET, verifier=verifier
        )

    # adversarial: checks OPTIONAL — but a carried check must still coerce.
    assert make({"kind": "adversarial"}).decoded_verifier()["kind"] == "adversarial"
    assert make({"kind": "adversarial", "checks": [{"files": ["a.md"]}]}).state == "active"
    with pytest.raises(ValueError):
        make({"kind": "adversarial", "checks": [{"bogus": 1}]})
    # judged: the judge alone — checks are refused, not silently ignored.
    assert make({"kind": "judged"}).decoded_verifier()["kind"] == "judged"
    with pytest.raises(ValueError, match="adversarial"):
        make({"kind": "judged", "checks": [{"files": ["a.md"]}]})
    # unknown kinds still refused, with the whole vocabulary named.
    with pytest.raises(ValueError, match="checks, adversarial, judged, manual"):
        make({"kind": "vibes"})
