"""Billing ledger + budgets — no network, no API keys."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from iron_jarvis.billing.ledger import BillingService, estimate_credits, is_billable_provider
from iron_jarvis.billing import models as _billing_models  # noqa: F401


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def billing(engine):
    return BillingService(engine, enabled=True, require_credits=False, min_credits=1.0)


def test_grant_and_burn(billing: BillingService):
    billing.grant(100.0, ref_id="seed")
    assert billing.balance() == 100.0
    billing.burn(25.0, ref_id="sess1")
    assert billing.balance() == 75.0
    entries = billing.ledger(limit=10)
    assert len(entries) >= 2
    assert entries[0]["kind"] in ("burn", "grant")


def test_insufficient_burn_raises(billing: BillingService):
    with pytest.raises(ValueError, match="insufficient"):
        billing.burn(5.0)


def test_can_start_run_respects_require_credits(engine):
    b = BillingService(engine, enabled=True, require_credits=True, min_credits=10.0)
    ok, reason = b.can_start_run("anthropic")
    assert not ok
    assert "insufficient" in reason
    b.grant(50.0)
    ok2, _ = b.can_start_run("anthropic")
    assert ok2
    b2 = BillingService(engine, enabled=True, require_credits=True, min_credits=9999.0)
    assert b2.can_start_run("mock")[0]
    assert b2.can_start_run("ollama")[0]


def test_estimate_local_zero():
    assert estimate_credits("mock", 1000, 1000) == 0.0
    assert estimate_credits("ollama", 1000, 1000) == 0.0
    assert not is_billable_provider("mock")
    assert is_billable_provider("anthropic")


def test_record_session_usage_burns(billing: BillingService):
    billing.grant(1000.0)
    out = billing.record_session_usage(
        session_id="s1",
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert out["estimated_usd"] > 0
    assert out["credits_burned"] > 0
    assert billing.balance() < 1000.0


def test_no_secrets_in_summary(billing: BillingService):
    summary = billing.summary()
    blob = str(summary).lower()
    assert "sk-" not in blob  # no live stripe-looking secrets
    for p in summary["products"]:
        assert "sk_" not in str(p)


@pytest.mark.asyncio
async def test_reflex_balance_command(billing: BillingService, engine):
    from types import SimpleNamespace

    from iron_jarvis.reflex.commands import CommandInterpreter

    billing.grant(12.5)
    platform = SimpleNamespace(billing=billing, config=SimpleNamespace(
        default_provider="mock", default_model="mock-1"
    ), engine=engine, observability=SimpleNamespace(usage_summary=lambda d: {"totals": {"runs": 0}}))
    orch = SimpleNamespace(list_sessions=lambda limit=200: [])
    router = SimpleNamespace()
    ci = CommandInterpreter(platform, orch, router)
    help_txt = await ci.interpret("/help")
    assert help_txt and "balance" in help_txt.lower()
    bal = await ci.interpret("/balance")
    assert bal and ("12.5" in bal or "12.50" in bal)
