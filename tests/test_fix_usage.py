"""Tests for cost/usage analytics: eval.pricing + Observability.usage_summary.

Fully offline. We insert AgentRun rows with known provider/model/tokens across
two days, then assert the aggregation and cost math against the static price
table.
"""

from __future__ import annotations

from datetime import timedelta

# Registers the Evaluation table in SQLModel.metadata before init_db.
import iron_jarvis.eval.models  # noqa: F401

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun
from iron_jarvis.eval import pricing
from iron_jarvis.eval.observability import Observability


# --- pricing.cost_for ----------------------------------------------------


def test_cost_for_known_providers():
    # claude-opus = $5 / $25 per 1M tokens.
    assert pricing.cost_for("anthropic", "claude-opus-4-8", 1_000_000, 0) == 5.0
    assert pricing.cost_for("anthropic", "claude-opus-4-8", 0, 1_000_000) == 25.0
    # gpt-4o prefix match.
    assert pricing.cost_for("openai", "gpt-4o", 1_000_000, 0) == 2.5
    # gemini prefix match.
    assert pricing.cost_for("google", "gemini-1.5-pro", 1_000_000, 0) == 1.25


def test_cost_for_mock_and_unknown_are_zero():
    assert pricing.cost_for("mock", "claude-opus-4-8", 10_000, 10_000) == 0.0
    assert pricing.cost_for("ollama", "llama3", 10_000, 10_000) == 0.0
    assert pricing.cost_for("nobody", "no-such-model", 10_000, 10_000) == 0.0


def test_cost_for_never_raises_on_bad_input():
    # Bad token types degrade to 0.0 rather than raising.
    assert pricing.cost_for("anthropic", "claude-opus-4-8", None, None) == 0.0
    assert pricing.cost_for("anthropic", "claude-opus-4-8", "x", "y") == 0.0


# --- Observability.usage_summary -----------------------------------------


def test_usage_summary_aggregates_across_two_days(tmp_path):
    engine = make_engine(str(tmp_path / "usage.db"))
    init_db(engine)

    now = utcnow()
    day1 = now - timedelta(days=1)
    day2 = now

    rows = [
        # provider, model, in, out, created_at
        ("anthropic", "claude-opus-4-8", 1_000_000, 1_000_000, day1),  # $5 + $25 = 30
        ("anthropic", "claude-opus-4-8", 1_000_000, 0, day2),          # $5
        ("openai", "gpt-4o", 1_000_000, 0, day2),                      # $2.5
        ("mock", "claude-opus-4-8", 1_000_000, 1_000_000, day2),       # $0
    ]
    with session_scope(engine) as db:
        for provider, model, itok, otok, created in rows:
            run = AgentRun(
                session_id="s1",
                provider=provider,
                model=model,
                input_tokens=itok,
                output_tokens=otok,
            )
            run.created_at = created
            db.add(run)
        db.commit()

    summary = Observability(engine).usage_summary(since_days=30)

    # Totals.
    t = summary["totals"]
    assert t["runs"] == 4
    assert t["input_tokens"] == 4_000_000
    assert t["output_tokens"] == 2_000_000
    assert t["cost_usd"] == 37.5  # 30 + 5 + 2.5 + 0

    # by_day: two days, ordered ascending; day1 cost 30, day2 cost 7.5.
    by_day = summary["by_day"]
    assert len(by_day) == 2
    assert by_day[0]["day"] == day1.date().isoformat()
    assert by_day[1]["day"] == day2.date().isoformat()
    assert by_day[0]["cost_usd"] == 30.0
    assert by_day[1]["cost_usd"] == 7.5

    # by_model: grouped by (provider, model). mock contributes 0.
    by_model = {(m["provider"], m["model"]): m for m in summary["by_model"]}
    opus = by_model[("anthropic", "claude-opus-4-8")]
    assert opus["runs"] == 2
    assert opus["input_tokens"] == 2_000_000
    assert opus["output_tokens"] == 1_000_000
    assert opus["cost_usd"] == 35.0  # 30 + 5
    assert by_model[("openai", "gpt-4o")]["cost_usd"] == 2.5
    assert by_model[("mock", "claude-opus-4-8")]["cost_usd"] == 0.0


def test_usage_summary_window_excludes_old_runs(tmp_path):
    engine = make_engine(str(tmp_path / "usage_window.db"))
    init_db(engine)

    old = utcnow() - timedelta(days=40)
    with session_scope(engine) as db:
        run = AgentRun(
            session_id="s1",
            provider="anthropic",
            model="claude-opus-4-8",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        run.created_at = old
        db.add(run)
        db.commit()

    summary = Observability(engine).usage_summary(since_days=30)
    assert summary["totals"]["runs"] == 0
    assert summary["totals"]["cost_usd"] == 0.0


def test_usage_summary_empty_returns_zeros(tmp_path):
    engine = make_engine(str(tmp_path / "usage_empty.db"))
    init_db(engine)

    summary = Observability(engine).usage_summary()
    assert summary["totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "runs": 0,
    }
    assert summary["by_day"] == []
    assert summary["by_model"] == []
    assert summary["since_days"] == 30
