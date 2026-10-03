"""v1.300.0 — Claude-subscription work is PRICED (as a list-price equivalent).

THE DEFECT: ``eval.pricing.cost_for("claude-cli", <any model>, in, out)``
returned 0.0, so every run served by the logged-in Claude CLI cost $0 — a
custom agent's DOLLAR allowance (v1.295.0, ``allowance.month_spend``) never
counted subscription work, and the Usage page showed $0 for it.

THE CONTRACT pinned here:

* ``claude-cli`` prices on the Anthropic rows by model family; the figure is a
  list-price EQUIVALENT (``is_list_price_equivalent``), never a charge.
* ``cost_for_usage`` is cache-aware (reads 10% of input, 5-minute writes 125%
  — 1-hour writes 200% for ``claude-cli``, whose CLI writes 1-hour cache —
  or the row's own ``cache_read`` price); ``cost_for`` keeps its signature.
* ``step_cost`` prefers the adapter's own ``usage["cost_usd"]``.
* ``AgentRun.cost_usd`` / ``Session.cost_usd`` (additive) accumulate per step
  through the REAL runtime; ``month_spend`` reads them (old METERED rows fall
  back to their tokens; an old subscription row is 0 — see
  ``test_subscription_cost_followups_v1300.py`` §4).
* The v1.295 auto-pause FIRES for a claude-cli agent with a $ allowance.
* Both chat lanes carry the same ``usage`` object (``_usage_frame``) and
  persist the turn's cost on the chat AgentRun row — lock-step.
* The usage rollup uses the stored cost and flags subscription rows.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import allowance
from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.core.db import init_db, session_scope
from iron_jarvis.core.models import AgentRun, EventRecord, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import pricing
from iron_jarvis.eval.observability import Observability
from iron_jarvis.eval.pricing import (
    UsageTally,
    cost_for,
    cost_for_usage,
    is_list_price_equivalent,
    step_cost,
)
from iron_jarvis.eval.usage_view import merged_usage
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_chat_turn_stop_v1241 import _drive_stream

NAME = "scout"
ROSTER = "custom:scout"
PROMPT = "You are Scout, a focused research helper. Be concise."
TOOLS = ["read_file", "list_files"]
OCT = datetime(2026, 10, 15, 12, 0, 0)
M = 1_000_000


# --------------------------------------------------------------------------- #
# 1. pricing: claude-cli is priced, cache-aware math, native cost wins
# --------------------------------------------------------------------------- #


def test_claude_cli_prices_a_known_family_like_the_api():
    api = cost_for("anthropic", "claude-opus-4-8", 1000, 200)
    assert api > 0
    # THE DEFECT: this was 0.0 for every model.
    assert cost_for("claude-cli", "claude-opus-4-8", 1000, 200) == pytest.approx(api)
    # The CLI's bare aliases name a family.
    assert cost_for("claude-cli", "opus", M, 0) == 5.0
    assert cost_for("claude-cli", "sonnet", M, 0) == 3.0
    assert cost_for("claude-cli", "haiku", 0, M) == 5.0
    assert cost_for("claude-cli", "fable", M, 0) == 10.0
    # An unknown model under the CLI ("subscription" = its own default) stays
    # honestly unpriced; the CLI's native cost_usd covers it (see step_cost).
    assert cost_for("claude-cli", "subscription", M, M) == 0.0
    # The figure is an EQUIVALENT for the subscription, a charge for the API.
    assert is_list_price_equivalent("claude-cli") is True
    assert is_list_price_equivalent("anthropic") is False
    assert is_list_price_equivalent("") is False


def test_the_5x_rows_and_the_unchanged_cost_for_contract():
    assert cost_for("anthropic", "claude-opus-5-5", M, M) == 24.0
    assert cost_for("claude-cli", "claude-fable-5-1", 0, M) == 50.0
    assert cost_for("anthropic", "claude-sonnet-5", M, 0) == 2.0
    # Existing callers: same signature, same refusals.
    assert cost_for("anthropic", "claude-opus-4-8", "x", "y") == 0.0
    assert cost_for("anthropic", "claude-opus-4-8", None, None) == 0.0
    assert cost_for("mock", "claude-opus-4-8", 10_000, 10_000) == 0.0


def test_cache_aware_pricing_exact_numbers():
    usage = {
        "input_tokens": M,  # TOTAL prompt, cache parts included
        "output_tokens": 100_000,
        "cache_read_input_tokens": 600_000,
        "cache_creation_input_tokens": 200_000,
    }
    # Opus family row: $5 in / $25 out → read $0.50, 5-minute write $6.25.
    #   200k uncached × 5 = 1.00; 600k read × 0.5 = 0.30;
    #   200k write × 6.25 = 1.25; 100k out × 25 = 2.50  →  5.05
    assert cost_for_usage("anthropic", "claude-opus-4-8", usage) == pytest.approx(5.05)
    # The Claude CLI writes 1-HOUR cache: 200k write × $10 (2x) = 2.00 → 5.80.
    assert cost_for_usage("claude-cli", "claude-opus-4-8", usage) == pytest.approx(5.80)
    # A row with its OWN cache-read price (Opus 5.5: $0.20, not 10% of $4).
    assert cost_for_usage(
        "claude-cli", "claude-opus-5-5",
        {"input_tokens": M, "output_tokens": 0, "cache_read_input_tokens": M},
    ) == pytest.approx(0.20)
    # Raw Messages-API shape (cache parts exceed the "total"): input_tokens is
    # read as the uncached remainder instead of going negative.
    assert cost_for_usage(
        "anthropic", "claude-opus-4-8",
        {"input_tokens": 100, "output_tokens": 0, "cache_read_input_tokens": 1000},
    ) == pytest.approx((100 * 5 + 1000 * 0.5) / M)
    # No cache keys → identical to cost_for.
    assert cost_for_usage("anthropic", "claude-opus-4-8", {"input_tokens": 1000, "output_tokens": 200}) == (
        pytest.approx(cost_for("anthropic", "claude-opus-4-8", 1000, 200))
    )
    assert cost_for_usage("claude-cli", "opus", None) == 0.0


def test_step_cost_prefers_the_native_cost_and_rejects_junk():
    usage = {"input_tokens": M, "output_tokens": 0, "cost_usd": 0.42}
    assert step_cost("claude-cli", "claude-opus-4-8", usage) == 0.42
    # Native wins even where the table knows nothing.
    assert step_cost("claude-cli", "subscription", usage) == 0.42
    assert step_cost("claude-cli", "claude-opus-4-8", {**usage, "cost_usd": 0.0}) == 0.0
    # Junk natives fall back to the table (1M opus input = $5).
    for junk in (float("nan"), float("inf"), -1.0, True, "abc", None):
        assert step_cost("claude-cli", "claude-opus-4-8", {**usage, "cost_usd": junk}) == 5.0, junk


def test_usage_tally_reports_only_known_keys():
    t = UsageTally()
    t.add("nobody", "no-such-model", {"input_tokens": 10, "output_tokens": 2})
    # Unknown provider, no cache keys, no native cost → tokens only.
    assert t.as_dict() == {"input_tokens": 10, "output_tokens": 2}
    t.add(
        "claude-cli", "claude-opus-4-8",
        {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 97,
         "cache_creation_input_tokens": 0, "cost_usd": 0.03},
    )
    assert t.as_dict() == {
        "input_tokens": 110,
        "output_tokens": 7,
        "cache_read_input_tokens": 97,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0.03,
        "list_price_equivalent": True,
    }


# --------------------------------------------------------------------------- #
# 2. the REAL runtime accumulates cost onto AgentRun + Session
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "spawn_agent": "allow", "delegate": "allow"}
    )
    return p


def _ctx(platform, tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="parent-session",
        agent_run_id="parent1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _as_claude_cli(platform, usage: dict, model: str = "claude-opus-4-8") -> dict:
    """Serve every run through the offline mock, but DRESSED as the Claude CLI:
    the adapter reports ``claude-cli`` (what the router puts on the run) and
    each answer carries ``usage`` — the rebuilt adapter's shape."""
    seen: dict = {"calls": 0}
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        if getattr(adapter, "_v1300_dressed", False):
            return adapter
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen["calls"] += 1
            resp = await real_complete(system=system, messages=messages, tools=tools, **kw)
            resp.usage = dict(usage)
            return resp

        adapter.complete = spy
        adapter.provider = "claude-cli"
        adapter.model = model
        adapter._v1300_dressed = True
        return adapter

    platform.providers.get = spy_get
    return seen


def _rows(engine, model):
    with session_scope(engine) as db:
        return list(db.exec(select(model)))


async def _spawn(platform, tmp_path, task="summarize the project"):
    return await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": task},
        _ctx(platform, tmp_path), platform.permissions,
    )


async def test_a_real_run_accumulates_native_cost_on_the_run_and_the_session(platform, tmp_path):
    platform.agents_registry.register(NAME, PROMPT, TOOLS)
    seen = _as_claude_cli(
        platform,
        {"input_tokens": 1000, "output_tokens": 10, "cache_read_input_tokens": 900,
         "cache_creation_input_tokens": 0, "cost_usd": 0.0123},
    )
    result = await _spawn(platform, tmp_path)
    assert result.ok, result.error
    assert seen["calls"] >= 1, "the model was really called"
    sessions = [s for s in _rows(platform.engine, Session) if s.agent_name == ROSTER]
    assert len(sessions) == 1
    child = sessions[0]
    runs = [r for r in _rows(platform.engine, AgentRun) if r.session_id == child.id]
    assert runs and all(r.provider == "claude-cli" for r in runs)
    steps = sum(r.steps for r in runs)
    assert steps == seen["calls"]
    # Each step added the CLI's own figure — not the table's price of 1000
    # tokens of opus (which would be 0.005 + 0.00025 per step).
    assert sum(r.cost_usd for r in runs) == pytest.approx(0.0123 * steps)
    assert child.cost_usd == pytest.approx(0.0123 * steps)
    assert child.provider == "claude-cli"
    # The per-step audit event carries the same step cost.
    with session_scope(platform.engine) as db:
        evs = list(db.exec(select(EventRecord).where(EventRecord.type == "llm.completed")))
    costs = [json.loads(e.payload_json)["cost_usd"] for e in evs if e.session_id == child.id]
    assert costs and all(c == pytest.approx(0.0123) for c in costs)


# --------------------------------------------------------------------------- #
# 3. month_spend reads the stored cost, old rows fall back
# --------------------------------------------------------------------------- #


def _seed(engine, *, provider, model, in_tok, out_tok=0, cost_usd=None):
    row = Session(task="seeded", agent_name=ROSTER, provider=provider, model=model,
                  input_tokens=in_tok, output_tokens=out_tok)
    row.created_at = OCT
    if cost_usd is not None:
        row.cost_usd = cost_usd
    with session_scope(engine) as db:
        db.add(row)
        db.commit()


def test_month_spend_uses_the_stored_cost_and_falls_back_for_old_rows(platform):
    e = platform.engine
    # A claude-cli session with tokens AND its own figure: the figure counts.
    _seed(e, provider="claude-cli", model="claude-opus-4-8", in_tok=1000, cost_usd=0.42)
    assert allowance.month_spend(e, ROSTER, OCT)["usd"] == pytest.approx(0.42)
    # An OLD row (cost 0, as every pre-v1.300 row reads) is priced from tokens.
    _seed(e, provider="anthropic", model="claude-opus-4-8", in_tok=10_000, out_tok=1000, cost_usd=0.0)
    old = cost_for("anthropic", "claude-opus-4-8", 10_000, 1000)
    spend = allowance.month_spend(e, ROSTER, OCT)
    assert spend["usd"] == pytest.approx(0.42 + old) and old > 0
    assert spend["tokens"] == 1000 + 11_000 and spend["runs"] == 2


def test_an_old_database_gains_the_cost_columns_and_old_rows_fall_back(platform):
    """A pre-v1.300 database gains ``cost_usd`` from the additive reconciler as
    a NULLABLE column: its old METERED rows read None and must price from
    their tokens — never crash, never count 0. (An old SUBSCRIPTION row reads
    0 — it was free when it ran; pinned in the follow-ups file §4.)"""
    e = platform.engine
    _seed(e, provider="anthropic", model="claude-opus-4-8", in_tok=M)
    _run(e, provider="anthropic", model="claude-opus-4-8", in_tok=M)
    with e.begin() as conn:  # make it an OLD database again
        conn.exec_driver_sql("ALTER TABLE session DROP COLUMN cost_usd")
        conn.exec_driver_sql("ALTER TABLE agentrun DROP COLUMN cost_usd")
    init_db(e)  # boot: the reconciler re-adds both as nullable
    with e.begin() as conn:
        assert conn.exec_driver_sql("SELECT cost_usd FROM session").fetchall() == [(None,)]
        assert conn.exec_driver_sql("SELECT cost_usd FROM agentrun").fetchall() == [(None,)]
    assert allowance.month_spend(e, ROSTER, OCT)["usd"] == pytest.approx(5.0)
    summary = Observability(e).usage_summary(36500)
    assert summary["by_model"][0]["cost_usd"] == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# 4. THE ANTI-VACUITY: a claude-cli agent's $ allowance now PAUSES it
# --------------------------------------------------------------------------- #


async def test_auto_pause_fires_for_a_claude_cli_agent_on_its_native_cost(platform, tmp_path):
    reg = platform.agents_registry
    reg.register(NAME, PROMPT, TOOLS, allowance_usd=0.05)
    _as_claude_cli(platform, {"input_tokens": 600, "output_tokens": 0, "cost_usd": 0.06})
    result = await _spawn(platform, tmp_path)
    assert result.ok, result.error  # the door let it through at $0
    rec = reg.get(NAME)
    assert rec.paused_reason.startswith("monthly allowance used up ("), rec.paused_reason
    with session_scope(platform.engine) as db:
        paused = list(db.exec(select(EventRecord).where(EventRecord.type == "agent.paused")))
    assert len(paused) == 1
    # And the next spawn is refused in the pause words.
    again = await _spawn(platform, tmp_path, task="again")
    assert again.ok is False and again.error.startswith("scout is paused: monthly allowance used up")


async def test_auto_pause_fires_for_a_claude_cli_agent_priced_from_tokens(platform, tmp_path):
    """No native figure (an older CLI): the opus family row prices it."""
    reg = platform.agents_registry
    reg.register(NAME, PROMPT, TOOLS, allowance_usd=0.05)
    _as_claude_cli(platform, {"input_tokens": 20_000, "output_tokens": 0})  # $0.10 / step
    result = await _spawn(platform, tmp_path)
    assert result.ok, result.error
    assert reg.get(NAME).paused_reason.startswith("monthly allowance used up (")


# --------------------------------------------------------------------------- #
# 5. both chat lanes carry the same usage object and persist the cost
# --------------------------------------------------------------------------- #

_USAGE = {
    "input_tokens": 1000,
    "output_tokens": 40,
    "cache_read_input_tokens": 970,
    "cache_creation_input_tokens": 0,
    "cost_usd": 0.03,
}
_EXPECTED = {
    "input_tokens": 1000,
    "output_tokens": 40,
    "cache_read_input_tokens": 970,
    "cache_creation_input_tokens": 0,
    "cost_usd": 0.03,
    "list_price_equivalent": True,
}
_BODY = {"messages": [{"role": "user", "content": "hi"}], "auto_tools": False}


def _chat_runs(platform):
    return [r for r in _rows(platform.engine, AgentRun) if r.session_id == "chat"]


def test_post_chat_carries_usage_and_persists_the_turn_cost(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    platform = app.state.platform

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        return RouteResult(LLMResponse(text="Hello.", usage=dict(_USAGE)), "claude-cli", "claude-opus-4-8")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = TestClient(app).post("/chat", json=_BODY)
    assert r.status_code == 200, r.text
    assert r.json()["usage"] == _EXPECTED
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].cost_usd == pytest.approx(0.03)
    assert runs[0].provider == "claude-cli"


def test_post_chat_usage_has_only_known_keys(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        return RouteResult(LLMResponse(text="Hi.", usage={"input_tokens": 5, "output_tokens": 1}), "nobody", "x")

    monkeypatch.setattr(app.state.platform.router, "complete", fake_complete)
    r = TestClient(app).post("/chat", json=_BODY)
    assert r.status_code == 200, r.text
    assert r.json()["usage"] == {"input_tokens": 5, "output_tokens": 1}


def _fake_stream(usage):
    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None, task_class=None, **kw):
        yield {"type": "text", "text": "Hello."}
        yield {
            "type": "final",
            "response": LLMResponse(text="Hello.", usage=dict(usage)),
            "provider": "claude-cli", "model": "claude-opus-4-8",
        }

    return fake_stream


async def test_stream_done_frame_carries_the_same_usage_and_persists_the_cost(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _fake_stream(_USAGE)
    frames = await _drive_stream(app, _BODY)
    done = [d for ev, d in frames if ev == "done"]
    assert len(done) == 1, frames
    assert done[0]["usage"] == _EXPECTED  # LOCK-STEP with the POST lane
    runs = _chat_runs(app.state.platform)
    assert len(runs) == 1 and runs[0].cost_usd == pytest.approx(0.03)


# --------------------------------------------------------------------------- #
# 6. the usage rollup: stored cost, subscription flag
# --------------------------------------------------------------------------- #


def _run(engine, *, provider, model, in_tok, out_tok=0, cost_usd=0.0):
    with session_scope(engine) as db:
        db.add(AgentRun(session_id="s", provider=provider, model=model,
                        input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost_usd))
        db.commit()


def test_usage_rollup_uses_the_stored_cost_and_flags_subscription_rows(platform):
    e = platform.engine
    _run(e, provider="claude-cli", model="claude-opus-4-8", in_tok=M, cost_usd=0.5)
    _run(e, provider="anthropic", model="claude-opus-4-8", in_tok=M, cost_usd=0.0)  # old row
    summary = Observability(e).usage_summary(30)
    rows = {r["provider"]: r for r in summary["by_model"]}
    assert rows["claude-cli"]["cost_usd"] == pytest.approx(0.5)  # stored, not 5.0
    assert rows["claude-cli"]["list_price_equivalent"] is True
    assert rows["anthropic"]["cost_usd"] == pytest.approx(5.0)  # priced from tokens
    assert "list_price_equivalent" not in rows["anthropic"]
    # MONEY vs VALUE: totals.cost_usd is METERED money only; the
    # subscription's list-price equivalent is its own total.
    assert summary["totals"]["cost_usd"] == pytest.approx(5.0)
    assert summary["totals"]["list_price_equivalent_usd"] == pytest.approx(0.5)
    day = summary["by_day"][0]
    assert day["cost_usd"] == pytest.approx(5.0)
    assert day["list_price_equivalent_usd"] == pytest.approx(0.5)
    # The merged view the Usage page reads keeps the flag and both totals.
    merged = merged_usage(platform, 30)
    cli = [r for r in merged["by_model"] if r["provider"] == "claude-cli"]
    assert cli and cli[0]["list_price_equivalent"] is True
    assert merged["totals"]["list_price_equivalent_usd"] == pytest.approx(0.5)
    assert math.isfinite(merged["totals"]["cost_usd"])


def test_pricing_module_names_the_equivalent_providers():
    # One definition: the rollup, the tally and the receipt all read this set.
    assert pricing.LIST_PRICE_EQUIVALENT_PROVIDERS == frozenset({"claude-cli"})
    assert pricing.PRICE_ALIASES["claude-cli"] == "anthropic"


# --------------------------------------------------------------------------- #
# 7. follow-ups: fleet cloud spend, goal budgets, failed/cancelled sessions
# --------------------------------------------------------------------------- #


def test_fleet_cloud_cost_skips_subscription_equivalents(tmp_path):
    """A subscription's list-price equivalent is not cloud SPEND: its tokens
    count as cloud work, its dollars do not; a metered API row still does."""
    app = create_app(str(tmp_path))
    e = app.state.platform.engine
    _run(e, provider="claude-cli", model="claude-opus-4-8", in_tok=M, cost_usd=0.5)
    _run(e, provider="anthropic", model="claude-opus-4-8", in_tok=M, cost_usd=0.0)
    out = TestClient(app).get("/fleet/usage").json()
    assert out["cloud_tokens"] == 2 * M
    assert out["cloud_cost_usd"] == pytest.approx(5.0)  # the API row only


async def test_a_claude_cli_goal_iteration_is_charged_its_native_cost(platform):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.goals.engine import GoalEngine

    engine = GoalEngine(platform, Orchestrator(platform))
    _as_claude_cli(platform, {"input_tokens": 1000, "output_tokens": 0, "cost_usd": 0.0123})
    goal = engine.store.create(
        name="g", contract_text="Create a file summarizing the task.",
        budget={"max_tokens": 1_000_000},
    )
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True, result
    with session_scope(platform.engine) as db:
        row = db.get(Session, result["session_id"])
    assert row.provider == "claude-cli" and row.cost_usd > 0
    spent = engine.store.get(goal.id).decoded_spent()
    # The CLI's own figure — not the table's price of the tokens (0.005/step).
    assert spent["dollars"] == pytest.approx(round(row.cost_usd, 6))
    assert row.cost_usd == pytest.approx(0.0123 * round(row.cost_usd / 0.0123))


def test_goal_digest_reads_the_recorded_cost_and_falls_back_for_old_rows():
    assert pricing.recorded_cost("claude-cli", "claude-opus-4-8", M, 0, 0.42) == 0.42
    assert pricing.recorded_cost("anthropic", "claude-opus-4-8", M, 0, 0.0) == 5.0
    assert pricing.recorded_cost("anthropic", "claude-opus-4-8", M, 0, None) == 5.0
    assert pricing.recorded_cost("anthropic", "claude-opus-4-8", M, 0, float("nan")) == 5.0


def _two_step_adapter(second):
    """Step 1 answers with a tool call and a native cost; step 2 ``second``."""
    from iron_jarvis.providers.adapters.base import ToolCall
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    class _TwoStep(MockLLMAdapter):
        calls = 0

        async def complete(self, **kw):
            type(self).calls += 1
            if type(self).calls == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="t1", name="list_files", arguments={"path": "."})],
                    finish_reason="tool_use",
                    usage={"input_tokens": 100, "output_tokens": 5, "cost_usd": 0.02},
                )
            raise second()

    return _TwoStep


@pytest.mark.parametrize(
    "second, status",
    [(lambda: RuntimeError("fleet down at 3am"), "failed"),
     (lambda: __import__("asyncio").CancelledError(), "cancelled")],
    ids=["failed", "cancelled"],
)
async def test_a_failed_or_cancelled_run_keeps_its_steps_cost_on_the_session(tmp_path, second, status):
    """The orchestrator copies TOKENS onto the session only on success; the
    COST rides the session object every finalizer merges, so a run that dies
    after a billed step still charges it (an allowance must see it)."""
    import asyncio as _asyncio

    from iron_jarvis.agents.orchestrator import Orchestrator

    p = build_platform(str(tmp_path))
    adapter_cls = _two_step_adapter(second)
    p.providers.register("mock", lambda model=None: adapter_cls())
    orch = Orchestrator(p)
    s = await orch.create_session("list the folder", provider="mock", model="mock-1")
    with pytest.raises((RuntimeError, _asyncio.CancelledError)):
        await orch.run_session(s.id)
    # >= 2: the router may retry the failing step once before giving up.
    assert adapter_cls.calls >= 2, "the second step really ran (anti-vacuity)"
    row = orch.get_session(s.id)
    assert row.status.value == status
    assert row.cost_usd == pytest.approx(0.02)
    runs = [r for r in _rows(p.engine, AgentRun) if r.session_id == s.id]
    assert sum(r.cost_usd for r in runs) == pytest.approx(0.02)
