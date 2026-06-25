"""Tests for the Evaluation Engine + Observability (SPEC §29, §30).

Fully offline: the MockLLM drives a real BUILDER session (write_file then
finalize), then we score and observe it from the persisted DB rows.
"""

from __future__ import annotations

# Importing the eval models registers the Evaluation table in SQLModel.metadata
# *before* build_platform calls init_db, so create_all includes it.
import iron_jarvis.eval.models  # noqa: F401

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    EventRecord,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.eval.evaluation import Evaluator
from iron_jarvis.eval.models import Evaluation
from iron_jarvis.eval.observability import Observability
from iron_jarvis.platform import build_platform


async def test_evaluator_scores_completed_session(tmp_path):
    platform = build_platform(str(tmp_path))
    session = await Orchestrator(platform).run("make a file")
    assert session.status is SessionStatus.COMPLETED

    evaluation = Evaluator(platform.engine).evaluate(session.id)

    assert isinstance(evaluation, Evaluation)
    assert evaluation.completion == 1.0
    assert 0.0 <= evaluation.tool_success_rate <= 1.0
    assert evaluation.tool_success_rate == 1.0  # mock write_file succeeds
    assert evaluation.step_count >= 1
    assert evaluation.latency_s >= 0.0
    assert evaluation.tool_calls >= 1
    assert evaluation.agent_run_id != ""

    # The row is persisted and retrievable via latest().
    latest = Evaluator(platform.engine).latest(session.id)
    assert latest is not None
    assert latest.id == evaluation.id


async def test_observability_traces_and_metrics(tmp_path):
    platform = build_platform(str(tmp_path))
    session = await Orchestrator(platform).run("make a file")
    Evaluator(platform.engine).evaluate(session.id)

    obs = Observability(platform.engine)

    traces = obs.traces(session.id)
    assert traces  # non-empty
    assert all({"type", "ts", "payload"} <= set(t) for t in traces)
    assert any(t["type"] == "session.created" for t in traces)
    # Ordered by time (ISO timestamps are lexicographically sortable).
    timestamps = [t["ts"] for t in traces]
    assert timestamps == sorted(timestamps)

    metrics = obs.metrics()
    expected_keys = {
        "sessions_evaluated",
        "avg_completion",
        "avg_tool_success_rate",
        "avg_latency_s",
        "total_tool_invocations",
        "event_count",
    }
    assert expected_keys <= set(metrics)
    assert metrics["sessions_evaluated"] >= 1
    assert metrics["avg_completion"] == 1.0
    assert metrics["total_tool_invocations"] >= 1
    assert metrics["event_count"] >= 1


async def test_evaluator_on_hand_built_rows(tmp_path):
    """Fallback path: score directly from manually inserted rows (§29)."""
    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)  # Evaluation table is registered via the top-level import.

    session_id = "session_manual"
    run = AgentRun(session_id=session_id, state=AgentState.COMPLETED, steps=3)
    with session_scope(engine) as db:
        run.finished_at = run.created_at  # zero latency, still >= 0.0
        db.add(run)
        db.add(
            ToolInvocation(
                session_id=session_id, agent_run_id=run.id, tool="write_file", ok=True
            )
        )
        db.add(
            ToolInvocation(
                session_id=session_id, agent_run_id=run.id, tool="shell", ok=False
            )
        )
        db.add(
            EventRecord(
                id="evt_manual",
                type="session.created",
                session_id=session_id,
                payload_json="{}",
            )
        )
        db.commit()

    evaluation = Evaluator(engine).evaluate(session_id)
    assert evaluation.completion == 1.0
    assert evaluation.tool_calls == 2
    assert evaluation.tool_success_rate == 0.5  # 1 of 2 ok
    assert evaluation.step_count == 3
    assert evaluation.latency_s >= 0.0

    traces = Observability(engine).traces(session_id)
    assert any(t["type"] == "session.created" for t in traces)
