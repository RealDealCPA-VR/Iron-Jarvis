"""v1.370.4 — the benchmark's budget ledger compares money at the precision
the run record carries it.

The full 30-task comparison (2026-10-22) flagged one honest Jarvis-on-Codex
record: "the run says 0.065993, the ledger says 0.0659935". The chat lane
rounds a turn's cost to the millionth of a dollar (routes/chat.py), while the
ledger adds each call's unrounded cost, so a record can sit half a millionth
away from its ledger. Tokens must still match exactly, and a real gap (a call
the turn did not bill) must still be caught.
"""

from __future__ import annotations

from iron_jarvis.harness_bench.ledger import (
    BudgetLedger,
    BudgetMismatch,
    CallEntry,
    MONEY_ROUNDING_USD,
    differences,
)


def _record(**kw):
    base = {"tokens_in": 100, "tokens_out": 10, "cost_metered_usd": 0.0, "cost_list_price_usd": 0.0}
    base.update(kw)
    return base


def test_a_cost_rounded_to_the_millionth_matches_its_ledger():
    # The exact pair the live run reported.
    assert differences(_record(cost_list_price_usd=0.065993),
                       _record(cost_list_price_usd=0.0659935)) == []
    assert differences(_record(cost_metered_usd=round(0.1234565, 6)),
                       _record(cost_metered_usd=0.1234565)) == []


def test_a_missing_call_is_still_a_mismatch():
    diff = differences(_record(cost_list_price_usd=0.0659), _record(cost_list_price_usd=0.066))
    assert diff and "cost_list_price_usd" in diff[0]
    # Two millionths is more than one record's rounding.
    assert differences(_record(cost_list_price_usd=0.065993),
                       _record(cost_list_price_usd=0.065995))


def test_tokens_must_match_exactly():
    assert differences(_record(tokens_in=100), _record(tokens_in=101))


def test_a_sum_of_records_may_drift_by_one_rounding_each():
    n = 30
    # Each record is half a millionth below its ledger: the sum is n halves off.
    record_sum = _record(cost_list_price_usd=0.0)
    ledger_sum = _record(cost_list_price_usd=n * MONEY_ROUNDING_USD)
    assert differences(record_sum, ledger_sum, records=n) == []
    # ...but not when counted as one record.
    assert differences(record_sum, ledger_sum, records=1)


def test_verify_record_and_verify_plan_accept_the_live_rounding():
    ledger = BudgetLedger()
    roll = ledger.begin("hard-expression-evaluator", "jarvis")
    for cost in (0.0329935, 0.033):
        roll.add(CallEntry(tokens_in=50, tokens_out=5, cost_metered_usd=0.0,
                           cost_list_price_usd=cost))
    record = {"task_id": "hard-expression-evaluator", "tokens_in": 100, "tokens_out": 10,
              "cost_metered_usd": 0.0, "cost_list_price_usd": 0.065993}
    ledger.verify_record(record, roll)  # no BudgetMismatch
    ledger.verify_plan(["hard-expression-evaluator"], [], [record])


def test_verify_record_still_refuses_a_call_the_record_did_not_bill():
    ledger = BudgetLedger()
    roll = ledger.begin("t", "jarvis")
    roll.add(CallEntry(tokens_in=50, tokens_out=5, cost_metered_usd=0.0, cost_list_price_usd=0.03))
    roll.add(CallEntry(tokens_in=50, tokens_out=5, cost_metered_usd=0.0, cost_list_price_usd=0.03))
    record = {"task_id": "t", "tokens_in": 100, "tokens_out": 10,
              "cost_metered_usd": 0.0, "cost_list_price_usd": 0.03}
    try:
        ledger.verify_record(record, roll)
    except BudgetMismatch as exc:
        assert "cost_list_price_usd" in str(exc)
    else:  # pragma: no cover - the assertion that matters
        raise AssertionError("a call the record did not bill went unnoticed")
