"""Goals you can trust (v1.231.0, audit Wave 5, AE5 + AE13).

Converted from ``tests/_audit_20260904/test_a6_goals.py``.

* AE5 — the breaker also trips on ``BREAKER_MAX_FAILURES`` CONSECUTIVE
  failures at any spacing (``breaker_json.consecutive``), so a goal on the
  nightly cadence the product sells trips on the third bad night instead of
  failing forever because each failure aged out of the 30-minute window. A
  successful iteration resets the run; the window and its test
  (``test_goals_v1208.py::test_breaker_window_prunes_stale_failures``) stand.
* AE13 — an iteration's wall-clock charge EXCLUDES the time its session sat
  parked on an approval (``ChatApprovals.waited_s``): an unattended ask is
  still the conservative timeout receipt, but it is not billed as work.
"""

from __future__ import annotations

import time
from datetime import timedelta

from iron_jarvis.agents import runtime as rt
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.goals import store as goal_store
from iron_jarvis.goals.engine import GoalEngine
from iron_jarvis.goals.models import BREAKER_MAX_FAILURES, BREAKER_WINDOW_S
from iron_jarvis.goals.store import goal_view
from iron_jarvis.providers.adapters.base import ToolCall


async def _failing_run(session):
    session.status = SessionStatus.FAILED
    session.summary = "provider fleet-custom: HTTP 500 Cannot connect to host spark-049d:8888"
    return session


async def _completed_run(session):
    session.status = SessionStatus.COMPLETED
    session.summary = "closed the books"
    session.finished_at = utcnow()
    return session


class _NightlyClock:
    """A store clock the test advances one night per iteration, so every
    failure is a day older than the next — the cadence the window never fits."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self):
        return self.now

    def next_night(self) -> None:
        self.now = self.now + timedelta(days=1)


# --------------------------------------------------------------------------- #
# AE5 — three nights in a row trips; a success between them does not.
# --------------------------------------------------------------------------- #


async def test_breaker_trips_a_goal_that_fails_three_nights_in_a_row(platform, orchestrator, monkeypatch):
    clock = _NightlyClock()
    monkeypatch.setattr(goal_store, "utcnow", clock)
    engine = GoalEngine(platform, orchestrator)
    goal = engine.store.create(
        name="nightly-close", contract_text="close the books", budget={"max_tokens": 1_000_000}
    )
    monkeypatch.setattr(engine, "_run_session", _failing_run)
    assert BREAKER_WINDOW_S == 30 * 60  # the window the nightly cadence never fits
    assert BREAKER_MAX_FAILURES == 3

    first = await engine.run_iteration(goal.id)
    assert first["status"] == "failed" and first["state"] == "active"
    clock.next_night()
    second = await engine.run_iteration(goal.id)
    assert second["status"] == "failed" and second["state"] == "active"
    breaker = engine.store.get(goal.id).decoded_breaker()
    assert breaker["consecutive"] == 2
    assert len(breaker["failures"]) == 1  # the window pruned last night, as designed
    clock.next_night()

    third = await engine.run_iteration(goal.id)
    assert third["status"] == "failed"
    assert third["state"] == "tripped", third
    record = engine.store.get(goal.id)
    assert record.state == "tripped"
    breaker = record.decoded_breaker()
    assert breaker["consecutive"] == 3 and breaker["tripped_at"]
    assert len(breaker["failures"]) == 1  # the window alone could never have tripped this
    view = goal_view(record)
    assert view["breaker"]["consecutive"] == 3
    assert view["trip_reason"] and "failed" in view["trip_reason"]
    tripped = [e for e in platform.event_bus.history if e.type == "goal.tripped"]
    assert len(tripped) == 1 and tripped[0].payload["name"] == "nightly-close"
    # A tripped goal refuses to iterate until reopened — unchanged.
    refused = await engine.run_iteration(goal.id)
    assert refused.get("refused") is True


async def test_a_successful_night_resets_the_run_of_failures(platform, orchestrator, monkeypatch):
    clock = _NightlyClock()
    monkeypatch.setattr(goal_store, "utcnow", clock)
    engine = GoalEngine(platform, orchestrator)
    goal = engine.store.create(
        name="nightly-close", contract_text="close the books", budget={"max_tokens": 1_000_000}
    )
    outcomes = iter([_failing_run, _failing_run, _completed_run, _failing_run, _failing_run])

    async def scripted(session):
        return await next(outcomes)(session)

    monkeypatch.setattr(engine, "_run_session", scripted)
    states = []
    for _ in range(5):
        result = await engine.run_iteration(goal.id)
        states.append((result["status"], result["state"]))
        clock.next_night()
    assert states == [
        ("failed", "active"),
        ("failed", "active"),
        ("completed", "active"),  # manual verifier: never auto-satisfies
        ("failed", "active"),
        ("failed", "active"),  # two in a row since the success — not three
    ]
    breaker = engine.store.get(goal.id).decoded_breaker()
    assert breaker["consecutive"] == 2
    # ...and the third consecutive one trips.
    monkeypatch.setattr(engine, "_run_session", _failing_run)
    sixth = await engine.run_iteration(goal.id)
    assert sixth["state"] == "tripped"


async def test_reopen_clears_the_consecutive_counter(platform, orchestrator, monkeypatch):
    clock = _NightlyClock()
    monkeypatch.setattr(goal_store, "utcnow", clock)
    engine = GoalEngine(platform, orchestrator)
    goal = engine.store.create(name="g", contract_text="x", budget={"max_tokens": 1_000_000})
    monkeypatch.setattr(engine, "_run_session", _failing_run)
    for _ in range(3):
        await engine.run_iteration(goal.id)
        clock.next_night()
    assert engine.store.get(goal.id).state == "tripped"
    engine.store.reopen(goal.id)
    breaker = engine.store.get(goal.id).decoded_breaker()
    assert int(breaker.get("consecutive") or 0) == 0
    after = await engine.run_iteration(goal.id)
    assert after["state"] == "active"  # one failure after a reopen is one, not four


# --------------------------------------------------------------------------- #
# AE13 — the approval wait is not billed as work.
# --------------------------------------------------------------------------- #


async def test_unattended_ask_timeout_is_a_timeout_receipt_and_is_not_billed_as_work(
    platform, orchestrator, monkeypatch
):
    wait_s = 0.5
    monkeypatch.setattr(rt, "SESSION_APPROVAL_TIMEOUT_S", wait_s)
    engine = GoalEngine(platform, orchestrator)
    goal = engine.store.create(
        name="g", contract_text="tidy the intake folder", budget={"max_wallclock_s": 60}
    )
    runtime = rt.AgentRuntime(platform)
    agent_def = get_agent_definition(AgentType.BUILDER)
    seen: dict = {}

    async def pause_then_finish(session):
        assert session.origin == f"goal:{goal.id}"  # the branch that pauses
        tc = ToolCall(id="t1", name="shell", arguments={"command": "dir"})
        reason, extra = await runtime._pause_for_approval(session, tc, agent_def, set())
        seen["reason"] = reason
        session.status = SessionStatus.COMPLETED
        session.summary = "could not run the command: approval timed out"
        session.finished_at = utcnow()
        orchestrator._save(session)
        return session

    monkeypatch.setattr(engine, "_run_session", pause_then_finish)
    t0 = time.monotonic()
    result = await engine.run_iteration(goal.id)
    total = time.monotonic() - t0
    assert result["ok"] is True and result["status"] == "completed"
    assert "timed out" in seen["reason"]  # the conservative timeout receipt, unchanged
    resolved = [e for e in platform.event_bus.history if e.type == "approval.resolved"]
    assert resolved and resolved[-1].payload["decision"] == "timeout"
    spent = engine.store.get(goal.id).decoded_spent()
    assert spent["iterations"] == 1
    # The iteration waited `wait_s` for a human who was asleep. The charge is
    # the work around the wait, never the wait: a defect that bills the
    # whole elapsed time charges >= total - (tiny pre-iterate overhead), so
    # this bound is the defect boundary, not a hardware speed.
    assert result["waited_s"] >= wait_s * 0.9
    assert spent["wallclock_s"] <= total - wait_s + 0.05, (spent, total, result)
    assert spent["wallclock_s"] >= 0.0
    assert platform.approvals.waited_s(result["session_id"]) >= wait_s * 0.9
    assert platform.approvals.waited_s("never-asked") == 0.0


async def test_an_iteration_with_no_ask_is_billed_in_full(platform, orchestrator, monkeypatch):
    engine = GoalEngine(platform, orchestrator)
    goal = engine.store.create(name="g", contract_text="x", budget={"max_wallclock_s": 60})

    async def slow_run(session):
        import asyncio

        await asyncio.sleep(0.05)
        return await _completed_run(session)

    monkeypatch.setattr(engine, "_run_session", slow_run)
    result = await engine.run_iteration(goal.id)
    assert "waited_s" not in result
    assert engine.store.get(goal.id).decoded_spent()["wallclock_s"] >= 0.05
