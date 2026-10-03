"""v1.300.0 review follow-ups — subscription dollars stay a list-price VALUE.

``claude-cli`` reports the CLI's own ``total_cost_usd``: a LIST-PRICE
EQUIVALENT of work the Claude subscription covers, never a charge. The review
found the places it still read as SPEND, the places it was wrong, and pins
that survived mutation. Each section below is one finding:

1. The Activity page summed the per-step ``llm.completed`` dollars as "Cost in
   this view" — the event now carries ``list_price_equivalent: true`` and the
   timeline keeps that value out of the entry's ``cost_usd`` (it rides in
   ``list_price_equivalent_usd``). An old event (no cost recorded) from the
   subscription stays $0, as it was when it ran.
2. The goal digest said a goal SPENT list-price dollars. Its ``spent`` now
   says which part is a list-price value, in words, driven through the REAL
   ``compose_digest``. (Goal BUDGETS still count it — the user's decision.)
3. A model that draws pay-as-you-go USAGE CREDITS (``claude_models`` marks it
   ``usage_credits``) costs real money: ``is_list_price_equivalent`` is
   model-aware, so those runs are billed, counted as cloud spend, and the
   receipt does not say "list". Pricing never spawns the CLI.
4. Upgrade day: an OLD subscription row (no stored cost) is priced 0 — it was
   free by the rules then in force — so an agent's dollar allowance cannot
   trip the day v1.300.0 installs. Metered rows are unchanged.
5. The table fallback prices claude-cli cache WRITES at the 1-hour rate (2x):
   the CLI writes 1-hour cache. The API keeps 1.25x.
6. Pins that survived mutation: the max() cost copy (delegate, phone lane),
   decompose's ``add_step_cost``, the chat nudge/rewrite tally (both lanes),
   and the ``list_price_equivalent`` flag in ``eval/usage_view``.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import allowance
from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.comm import Notifier
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.core.db import init_db, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    EventRecord,
    Session,
    SessionStatus,
)
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import pricing
from iron_jarvis.eval.observability import AuditTimeline, Observability
from iron_jarvis.eval.pricing import (
    UsageTally,
    cost_for,
    cost_for_usage,
    is_list_price_equivalent,
    recorded_cost,
)
from iron_jarvis.eval.usage_view import merged_usage
from iron_jarvis.goals.digest import compose_digest
from iron_jarvis.goals.store import GoalStore
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import claude_models as cm
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine

M = 1_000_000
NAME = "scout"
ROSTER = "custom:scout"
PROMPT = "You are Scout, a focused research helper. Be concise."
TOOLS = ["read_file", "list_files"]
CHINESE = "这是一个测试句子，用来检查语言泄漏的检测器是否有效。"


# --------------------------------------------------------------------------- #
# shared harness
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


def _dress(platform, usage: dict, *, provider: str = "claude-cli",
           model: str = "claude-opus-4-8") -> dict:
    """Serve every run through the offline mock, DRESSED as ``provider``: the
    adapter reports that provider/model and each answer carries ``usage``."""
    seen: dict = {"calls": 0}
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        if getattr(adapter, "_followups_dressed", False):
            return adapter
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen["calls"] += 1
            resp = await real_complete(system=system, messages=messages, tools=tools, **kw)
            resp.usage = dict(usage)
            return resp

        adapter.complete = spy
        adapter.provider = provider
        adapter.model = model
        adapter._followups_dressed = True
        return adapter

    platform.providers.get = spy_get
    return seen


async def _spawn(platform, tmp_path, task="summarize the project"):
    return await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": task},
        _ctx(platform, tmp_path), platform.permissions,
    )


def _llm_events(engine, session_id=None) -> list[dict]:
    with session_scope(engine) as db:
        evs = list(db.exec(select(EventRecord).where(EventRecord.type == "llm.completed")))
    return [json.loads(e.payload_json) for e in evs
            if session_id is None or e.session_id == session_id]


def _token_entries(engine) -> list[dict]:
    out = Observability(engine).timeline(kind="token", limit=200)
    return [e for e in out["entries"] if e["kind"] == "token"]


@pytest.fixture
def credits_catalog():
    """The live picker as a PRO plan sees it: Fable 5.1 draws usage credits
    (the CLI's own rule, ``claude_models._usage_credits``), Opus 5.5 is the
    plan's default and included. Seeded in-process — no handshake."""
    cm.reset()
    cm.set_home(None)
    data = {
        "models": [
            {"id": "claude-fable-5-1", "value": "claude-fable-5-1", "label": "Fable 5.1",
             "description": "Fable 5.1 · Billed as usage credits", "native": "claude-fable-5-1[1m]",
             "context_window": cm.ONE_M, "usage_credits": True, "pinned": True, "default": False},
            {"id": "claude-opus-5-5", "value": "opus", "label": "Opus 5.5",
             "description": "Opus 5.5 · Most capable", "native": "claude-opus-5-5[1m]",
             "context_window": cm.ONE_M, "usage_credits": False, "pinned": True, "default": True},
        ],
        "subscription": "pro",
        "at": cm._now_iso(),
        "error": None,
    }
    cm._remember(data, at=time.monotonic())
    yield data
    cm.reset()


@pytest.fixture
def fable_is_the_default(credits_catalog):
    """Same plan, but the CLI's own default resolves to the credit model."""
    for row in credits_catalog["models"]:
        row["default"] = row["id"] == "claude-fable-5-1"
    cm._remember(credits_catalog, at=time.monotonic())
    return credits_catalog


# --------------------------------------------------------------------------- #
# 1. the Activity page: the llm.completed event carries the flag, the
#    timeline keeps list-price dollars out of cost_usd
# --------------------------------------------------------------------------- #


async def test_a_subscription_step_event_is_flagged_and_is_not_activity_cost(platform, tmp_path):
    platform.agents_registry.register(NAME, PROMPT, TOOLS)
    seen = _dress(platform, {"input_tokens": 1000, "output_tokens": 10, "cost_usd": 0.0123})
    result = await _spawn(platform, tmp_path)
    assert result.ok, result.error
    assert seen["calls"] >= 1
    payloads = _llm_events(platform.engine)
    assert payloads, "the runtime published its per-step event"
    # THE FLAG rides the event, beside the step's own figure.
    assert all(p["list_price_equivalent"] is True for p in payloads)
    assert all(p["cost_usd"] == pytest.approx(0.0123) for p in payloads)
    # THE ACTIVITY ROW: the value is said apart, never summed as cost.
    entries = _token_entries(platform.engine)
    assert len(entries) == len(payloads)
    for e in entries:
        assert e["cost_usd"] == 0.0, e
        assert e["list_price_equivalent"] is True
        assert e["list_price_equivalent_usd"] == pytest.approx(0.0123)


async def test_a_metered_step_event_is_cost_and_carries_no_flag(platform, tmp_path):
    platform.agents_registry.register(NAME, PROMPT, TOOLS)
    _dress(platform, {"input_tokens": 1000, "output_tokens": 10, "cost_usd": 0.0123},
           provider="anthropic")
    result = await _spawn(platform, tmp_path)
    assert result.ok, result.error
    payloads = _llm_events(platform.engine)
    assert payloads and all("list_price_equivalent" not in p for p in payloads)
    entries = _token_entries(platform.engine)
    assert entries and all(e["cost_usd"] == pytest.approx(0.0123) for e in entries)
    assert all("list_price_equivalent" not in e for e in entries)


async def test_a_usage_credit_step_event_is_cost(platform, tmp_path, credits_catalog):
    """Item 3 on the event: Fable on a Pro plan draws usage credits — money."""
    platform.agents_registry.register(NAME, PROMPT, TOOLS)
    _dress(platform, {"input_tokens": 1000, "output_tokens": 10, "cost_usd": 0.05},
           model="claude-fable-5-1")
    result = await _spawn(platform, tmp_path)
    assert result.ok, result.error
    payloads = _llm_events(platform.engine)
    assert payloads and all("list_price_equivalent" not in p for p in payloads)
    entries = _token_entries(platform.engine)
    assert entries and all(e["cost_usd"] == pytest.approx(0.05) for e in entries)


def _row(payload: dict, etype: str = "llm.completed"):
    return ("evt_1", utcnow(), etype, "s1", json.dumps(payload), None, None)


def test_event_entries_old_and_new():
    entry = AuditTimeline._event_entry
    # An OLD subscription event (before v1.300.0 no cost was recorded): it was
    # free then, and the table must not re-price it as cost now.
    old_sub = entry(_row({"provider": "claude-cli", "model": "claude-opus-4-8",
                          "input_tokens": M, "output_tokens": 0}))
    assert old_sub["cost_usd"] == 0.0
    assert "list_price_equivalent" not in old_sub
    # An OLD metered event is priced from its tokens exactly as before.
    old_api = entry(_row({"provider": "anthropic", "model": "claude-opus-4-8",
                          "input_tokens": M, "output_tokens": 0}))
    assert old_api["cost_usd"] == pytest.approx(5.0)
    # A NEW flagged event: value apart, cost 0.
    new_sub = entry(_row({"provider": "claude-cli", "model": "claude-opus-4-8",
                          "input_tokens": 10, "output_tokens": 0,
                          "cost_usd": 0.42, "list_price_equivalent": True}))
    assert new_sub["cost_usd"] == 0.0
    assert new_sub["list_price_equivalent"] is True
    assert new_sub["list_price_equivalent_usd"] == pytest.approx(0.42)
    # A junk cost never raises.
    junk = entry(_row({"provider": "anthropic", "model": "x", "input_tokens": 1,
                       "output_tokens": 1, "cost_usd": "abc"}))
    assert junk["cost_usd"] == 0.0


# --------------------------------------------------------------------------- #
# 1b / 6b. decompose's one-shot completion: cost on run + session, flagged event
# --------------------------------------------------------------------------- #


def _one_shot_runtime(provider: str, model: str, usage: dict):
    published: list[tuple] = []

    async def complete(**kw):
        return RouteResult(LLMResponse(text="ok", usage=dict(usage)), provider, model)

    async def publish(etype, payload, session_id=None):
        published.append((etype, payload, session_id))

    rt = SimpleNamespace(p=SimpleNamespace(
        router=SimpleNamespace(complete=complete),
        event_bus=SimpleNamespace(publish=publish),
    ))
    return rt, published


@pytest.mark.parametrize("provider, flagged", [("claude-cli", True), ("anthropic", False)])
async def test_decompose_one_shot_adds_the_step_cost_and_flags_the_event(provider, flagged):
    from iron_jarvis.agents import decompose

    rt, published = _one_shot_runtime(provider, "claude-opus-4-8",
                                      {"input_tokens": 100, "output_tokens": 5, "cost_usd": 0.031})
    run = SimpleNamespace(id="run_1", steps=1, input_tokens=0, output_tokens=0, cost_usd=0.0)
    session = SimpleNamespace(id="s_1", provider=provider, model="claude-opus-4-8", cost_usd=0.0)
    text = await decompose._one_shot(rt, run, session, system="plan", messages=[],
                                     task_class="plan")
    assert text == "ok"
    # 6b: the dollars ride with the tokens — run AND session.
    assert run.cost_usd == pytest.approx(0.031)
    assert session.cost_usd == pytest.approx(0.031)
    assert len(published) == 1
    payload = published[0][1]
    assert payload["cost_usd"] == pytest.approx(0.031)
    assert ("list_price_equivalent" in payload) is flagged
    if flagged:
        assert payload["list_price_equivalent"] is True


# --------------------------------------------------------------------------- #
# 2 / 6c. the goal digest, through the REAL compose_digest
# --------------------------------------------------------------------------- #


def _goal_session(platform, goal_id, *, provider, model, in_tok, out_tok=0, cost_usd=None):
    row = Session(task="iterate", origin=f"goal:{goal_id}", provider=provider, model=model,
                  status=SessionStatus.COMPLETED, input_tokens=in_tok, output_tokens=out_tok,
                  summary="did stuff", created_at=utcnow() - timedelta(hours=1))
    if cost_usd is not None:
        row.cost_usd = cost_usd
    with session_scope(platform.engine) as db:
        db.add(row)
        db.commit()


def _digest_goal(platform, name="Inbox zero"):
    goal = GoalStore(platform.engine).create(
        name=name, contract_text="do a thing", budget={"max_tokens": M})
    return goal.id


def _spent(platform, gid) -> dict:
    d = compose_digest(platform, hours=24)
    (g,) = [g for g in d["goals"] if g["id"] == gid]
    return g["spent"]


def test_digest_says_a_subscription_goal_spent_a_list_price_value(platform):
    gid = _digest_goal(platform)
    _goal_session(platform, gid, provider="claude-cli", model="claude-opus-4-8",
                  in_tok=1000, cost_usd=0.42)
    spent = _spent(platform, gid)
    # The figure is the RECORDED one (the CLI's own), not the table's.
    assert spent["dollars"] == pytest.approx(0.42)
    assert spent["list_price_equivalent"] is True
    assert spent["list_price_dollars"] == pytest.approx(0.42)
    assert spent["words"] == "≈$0.42 at list price (Claude subscription)"
    assert "spent" not in spent["words"]


def test_digest_words_for_a_metered_goal_are_unchanged(platform):
    gid = _digest_goal(platform)
    _goal_session(platform, gid, provider="anthropic", model="claude-opus-4-8",
                  in_tok=M, cost_usd=1.5)
    spent = _spent(platform, gid)
    assert spent["dollars"] == pytest.approx(1.5)
    assert "list_price_equivalent" not in spent and "list_price_dollars" not in spent
    assert spent["words"] == "$1.50 spent"


def test_digest_splits_a_mixed_goal_and_reprices_old_metered_rows(platform):
    gid = _digest_goal(platform)
    _goal_session(platform, gid, provider="claude-cli", model="claude-opus-4-8",
                  in_tok=1000, cost_usd=0.42)
    # An OLD metered row (no stored cost) is priced from its tokens: $5.
    _goal_session(platform, gid, provider="anthropic", model="claude-opus-4-8", in_tok=M)
    # An OLD subscription row (no stored cost) was free when it ran.
    _goal_session(platform, gid, provider="claude-cli", model="claude-opus-4-8", in_tok=M)
    spent = _spent(platform, gid)
    assert spent["dollars"] == pytest.approx(5.42)  # what the budget counts
    assert spent["list_price_dollars"] == pytest.approx(0.42)
    assert spent["words"] == "$5.00 spent · ≈$0.42 at list price (Claude subscription)"


def test_digest_bills_a_usage_credit_goal(platform, credits_catalog):
    gid = _digest_goal(platform)
    _goal_session(platform, gid, provider="claude-cli", model="claude-fable-5-1",
                  in_tok=1000, cost_usd=0.9)
    spent = _spent(platform, gid)
    assert spent["dollars"] == pytest.approx(0.9)
    assert "list_price_equivalent" not in spent
    assert spent["words"] == "$0.90 spent"


# --------------------------------------------------------------------------- #
# 3. usage credits are money: the flag is model-aware, never spawns
# --------------------------------------------------------------------------- #


def test_the_flag_is_model_aware(credits_catalog):
    assert is_list_price_equivalent("claude-cli", "claude-opus-5-5") is True
    assert is_list_price_equivalent("claude-cli", "opus") is True  # a picker value
    assert is_list_price_equivalent("claude-cli", "claude-fable-5-1") is False
    assert is_list_price_equivalent("claude-cli", "claude-fable-5-1[1m]") is False
    assert is_list_price_equivalent("claude-cli", "fable") is False  # alias
    # The CLI's own default is the included Opus here.
    assert is_list_price_equivalent("claude-cli", "subscription") is True
    assert is_list_price_equivalent("claude-cli") is True
    # A metered provider is never an equivalent, credits or not.
    assert is_list_price_equivalent("anthropic", "claude-fable-5-1") is False


def test_the_default_model_on_credits_is_money(fable_is_the_default):
    assert is_list_price_equivalent("claude-cli", "subscription") is False
    assert is_list_price_equivalent("claude-cli", "") is False
    assert is_list_price_equivalent("claude-cli", "claude-opus-5-5") is True


def test_without_a_discovery_every_claude_cli_model_is_included():
    """The pinned table never claims credits (``usage_credits: False``)."""
    cm.reset()
    cm.set_home(None)
    try:
        assert is_list_price_equivalent("claude-cli", "claude-fable-5-1") is True
        assert is_list_price_equivalent("claude-cli", "subscription") is True
    finally:
        cm.reset()


def test_the_flag_never_spawns_and_never_raises(monkeypatch, tmp_path):
    cm.reset()
    cm.set_home(tmp_path)  # a cold start with nothing on disk

    def boom(*a, **kw):
        raise AssertionError("pricing spawned the claude CLI")

    monkeypatch.setattr(cm, "_run_handshake", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(cm, "_refresh_in_background", boom)
    try:
        assert is_list_price_equivalent("claude-cli", "claude-fable-5-1") is True
        # A broken catalog read costs the refinement, never the answer.
        monkeypatch.setattr(cm, "_known", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        assert is_list_price_equivalent("claude-cli", "claude-fable-5-1") is True
        assert is_list_price_equivalent("anthropic", "claude-fable-5-1") is False
        assert is_list_price_equivalent(None, None) is False  # type: ignore[arg-type]
    finally:
        cm.set_home(None)
        cm.reset()


def test_the_tally_does_not_say_list_for_a_credit_model(credits_catalog):
    t = UsageTally()
    t.add("claude-cli", "claude-fable-5-1", {"input_tokens": 10, "output_tokens": 1, "cost_usd": 0.2})
    assert "list_price_equivalent" not in t.as_dict()
    t2 = UsageTally()
    t2.add("claude-cli", "claude-opus-5-5", {"input_tokens": 10, "output_tokens": 1, "cost_usd": 0.2})
    assert t2.as_dict()["list_price_equivalent"] is True


def test_the_chat_receipt_does_not_say_list_for_a_credit_model(tmp_path, monkeypatch, credits_catalog):
    app = create_app(str(tmp_path))
    cm._remember(credits_catalog, at=time.monotonic())  # app boot may reset it

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        return RouteResult(
            LLMResponse(text="Hello.", usage={"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.2}),
            "claude-cli", "claude-fable-5-1",
        )

    monkeypatch.setattr(app.state.platform.router, "complete", fake_complete)
    r = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                            "auto_tools": False})
    assert r.status_code == 200, r.text
    usage = r.json()["usage"]
    assert usage["cost_usd"] == pytest.approx(0.2)
    assert "list_price_equivalent" not in usage


def _run(engine, *, provider, model, in_tok, out_tok=0, cost_usd=0.0):
    with session_scope(engine) as db:
        db.add(AgentRun(session_id="s", provider=provider, model=model,
                        input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost_usd))
        db.commit()


def test_usage_credit_runs_land_in_billed_totals_and_cloud_spend(tmp_path, credits_catalog):
    app = create_app(str(tmp_path))
    cm._remember(credits_catalog, at=time.monotonic())
    e = app.state.platform.engine
    _run(e, provider="claude-cli", model="claude-fable-5-1", in_tok=1000, cost_usd=0.9)
    _run(e, provider="claude-cli", model="claude-opus-5-5", in_tok=1000, cost_usd=0.5)
    summary = Observability(e).usage_summary(30)
    rows = {r["model"]: r for r in summary["by_model"]}
    assert "list_price_equivalent" not in rows["claude-fable-5-1"]
    assert rows["claude-opus-5-5"]["list_price_equivalent"] is True
    assert summary["totals"]["cost_usd"] == pytest.approx(0.9)
    assert summary["totals"]["list_price_equivalent_usd"] == pytest.approx(0.5)
    merged = merged_usage(app.state.platform, 30)
    mrows = {r["model"]: r for r in merged["by_model"]}
    assert "list_price_equivalent" not in mrows["claude-fable-5-1"]
    out = TestClient(app).get("/fleet/usage").json()
    assert out["cloud_cost_usd"] == pytest.approx(0.9)  # the credits row only


# --------------------------------------------------------------------------- #
# 4. upgrade day: an OLD subscription row is not re-priced
# --------------------------------------------------------------------------- #


def test_recorded_cost_prices_an_old_subscription_row_at_zero():
    for stored in (None, 0, 0.0, float("nan"), "junk", -1):
        assert recorded_cost("claude-cli", "claude-opus-4-8", M, M, stored) == 0.0, stored
    # A stored figure still wins.
    assert recorded_cost("claude-cli", "claude-opus-4-8", M, 0, 0.42) == 0.42
    # Metered: unchanged — an old row is priced from its tokens.
    assert recorded_cost("anthropic", "claude-opus-4-8", M, 0, None) == 5.0
    assert recorded_cost("anthropic", "claude-opus-4-8", M, 0, 0.0) == 5.0


def test_recorded_cost_prices_an_old_credit_row_from_its_tokens(credits_catalog):
    assert recorded_cost("claude-cli", "claude-fable-5-1", M, 0, None) == pytest.approx(10.0)


def _seed_session(engine, *, provider, model, in_tok, out_tok=0, cost_usd=None):
    row = Session(task="seeded", agent_name=ROSTER, provider=provider, model=model,
                  input_tokens=in_tok, output_tokens=out_tok)
    if cost_usd is not None:
        row.cost_usd = cost_usd
    with session_scope(engine) as db:
        db.add(row)
        db.commit()


def _null_cost_columns(engine):
    """Make it an OLD database again: drop the cost columns and boot — the
    additive reconciler re-adds them NULLABLE, so every existing row reads
    NULL, exactly as a pre-v1.300 row does after the upgrade."""
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE session DROP COLUMN cost_usd")
        conn.exec_driver_sql("ALTER TABLE agentrun DROP COLUMN cost_usd")
    init_db(engine)
    with engine.begin() as conn:
        assert {r[0] for r in conn.exec_driver_sql("SELECT cost_usd FROM session")} <= {None}
        assert {r[0] for r in conn.exec_driver_sql("SELECT cost_usd FROM agentrun")} <= {None}


def test_month_spend_does_not_reprice_old_subscription_rows(platform):
    e = platform.engine
    _seed_session(e, provider="claude-cli", model="claude-opus-4-8", in_tok=M)
    _null_cost_columns(e)
    spend = allowance.month_spend(e, ROSTER)
    assert spend["usd"] == 0.0  # THE DEFECT: $5.00 the day v1.300.0 installed
    assert spend["tokens"] == M and spend["runs"] == 1
    # A metered row with no stored cost is still priced from its tokens.
    _seed_session(e, provider="anthropic", model="claude-opus-4-8", in_tok=M, cost_usd=0.0)
    assert allowance.month_spend(e, ROSTER)["usd"] == pytest.approx(5.0)
    # A new subscription row with its own figure counts (allowances meter it).
    _seed_session(e, provider="claude-cli", model="claude-opus-4-8", in_tok=10, cost_usd=0.3)
    assert allowance.month_spend(e, ROSTER)["usd"] == pytest.approx(5.3)


def test_an_allowance_does_not_trip_on_upgrade_day(platform):
    reg = platform.agents_registry
    rec = reg.register(NAME, PROMPT, TOOLS, allowance_usd=0.05)
    _seed_session(platform.engine, provider="claude-cli", model="claude-opus-4-8", in_tok=M)
    _null_cost_columns(platform.engine)
    assert allowance.refusal_for(reg.get(NAME) or rec, platform.engine) == ""


def test_the_usage_rollup_uses_the_same_rule(platform):
    e = platform.engine
    _run(e, provider="claude-cli", model="claude-opus-4-8", in_tok=M)
    _run(e, provider="anthropic", model="claude-opus-4-8", in_tok=M)
    _null_cost_columns(e)
    summary = Observability(e).usage_summary(30)
    rows = {r["provider"]: r for r in summary["by_model"]}
    assert rows["claude-cli"]["cost_usd"] == 0.0
    assert rows["anthropic"]["cost_usd"] == pytest.approx(5.0)
    assert summary["totals"]["list_price_equivalent_usd"] == 0.0


# --------------------------------------------------------------------------- #
# 5. claude-cli's table fallback prices 1-hour cache writes
# --------------------------------------------------------------------------- #


def test_claude_cli_cache_writes_are_priced_at_the_one_hour_rate():
    write = {"input_tokens": M, "output_tokens": 0, "cache_creation_input_tokens": M}
    # Opus family: $5 input. The CLI writes 1-hour cache (2x) …
    assert cost_for_usage("claude-cli", "claude-opus-4-8", write) == pytest.approx(10.0)
    # … the API's fallback keeps the 5-minute rate (1.25x).
    assert cost_for_usage("anthropic", "claude-opus-4-8", write) == pytest.approx(6.25)
    assert pricing.CACHE_WRITE_1H_MULTIPLIER == 2.0
    assert pricing.CACHE_WRITE_MULTIPLIER == 1.25
    # Reads and plain tokens are the same for both.
    read = {"input_tokens": M, "output_tokens": M, "cache_read_input_tokens": M}
    assert cost_for_usage("claude-cli", "claude-opus-4-8", read) == pytest.approx(
        cost_for_usage("anthropic", "claude-opus-4-8", read))
    assert cost_for("claude-cli", "claude-opus-4-8", M, M) == cost_for("anthropic", "claude-opus-4-8", M, M)


# --------------------------------------------------------------------------- #
# 6a. the max() cost copy: delegate tool + the phone's dynamic-agent lane
# --------------------------------------------------------------------------- #


def _stub_run(session_cost: float, run_cost: float):
    """The runtime ran the child: it added ``session_cost`` to the session
    object in hand, and its run row carries ``run_cost`` (a row loaded
    separately may hold more)."""

    async def fake_run(self, session, agent_def, parent_id=None):
        session.cost_usd = session_cost
        return AgentRun(session_id=session.id, state=AgentState.COMPLETED,
                        provider="claude-cli", model="claude-opus-4-8",
                        input_tokens=10, output_tokens=2, cost_usd=run_cost, result="done")

    return fake_run


def _child_cost(engine, exclude=("parent-session",)) -> float:
    with session_scope(engine) as db:
        rows = [s for s in db.exec(select(Session)) if s.id not in exclude]
    assert rows, "no child session was created"
    return float(rows[-1].cost_usd or 0.0)


@pytest.mark.parametrize("session_cost, run_cost, expected",
                         [(0.03, 0.07, 0.07), (0.09, 0.07, 0.09), (0.05, 0.05, 0.05)])
async def test_delegate_copies_the_larger_cost_onto_the_child(
    tmp_path, monkeypatch, session_cost, run_cost, expected
):
    p = build_platform(str(tmp_path))
    p.registry.register(DelegateTool(p))
    p.permissions = PermissionEngine({**p.config.permissions, "delegate": "allow"})
    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", _stub_run(session_cost, run_cost))
    res = await p.registry.invoke("delegate", {"agent_type": "builder", "task": "x"},
                                  _ctx(p, tmp_path), p.permissions)
    assert res.ok, res.error
    # The larger of the two — never the session's alone, never a sum.
    assert _child_cost(p.engine) == pytest.approx(expected)


@pytest.mark.parametrize("session_cost, run_cost, expected",
                         [(0.03, 0.07, 0.07), (0.09, 0.07, 0.09), (0.05, 0.05, 0.05)])
async def test_the_phone_lane_copies_the_larger_cost_onto_the_session(
    tmp_path, monkeypatch, session_cost, run_cost, expected
):
    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)
    poller = InboundPoller(Notifier(), orch, p.engine, platform=p)
    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", _stub_run(session_cost, run_cost))
    s = await orch.create_session("escalated from the phone", provider="mock", model="mock-1",
                                  origin="comm:telegram")
    out = await poller._run_dynamic_session(s, get_agent_definition(AgentType.BUILDER))
    assert out.cost_usd == pytest.approx(expected)
    with session_scope(p.engine) as db:
        row = db.get(Session, s.id)
    assert row.cost_usd == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# 6d. the chat nudge + language rewrite completions reach the turn's cost
# --------------------------------------------------------------------------- #


def _chat_runs(platform) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    out: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data))
    return out


def _usage(cost: float) -> dict:
    return {"input_tokens": 10, "output_tokens": 2, "cost_usd": cost}


def _cli(resp: LLMResponse) -> RouteResult:
    return RouteResult(resp, "claude-cli", "claude-opus-4-8")


def _final(text: str, usage: dict, calls=None) -> dict:
    return {"type": "final",
            "response": LLMResponse(text=text, tool_calls=calls or [], usage=usage),
            "provider": "claude-cli", "model": "claude-opus-4-8"}


async def _ok_invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
    return ToolResult(ok=True, output="PNG 2x2")


_TOOL_CALL = ToolCall(id="c1", name="image_info", arguments={"path": "x.png"})
_TOOL_BODY = {"messages": [{"role": "user", "content": "inspect x.png"}], "tools": ["image_info"]}


def test_post_lane_bills_the_final_answer_nudge(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        n["i"] += 1
        if n["i"] == 1:
            return _cli(LLMResponse(text="", tool_calls=[_TOOL_CALL], usage=_usage(0.01)))
        if n["i"] == 2:
            return _cli(LLMResponse(text="", usage=_usage(0.02)))  # the silent stop
        return _cli(LLMResponse(text="It is a 2x2 PNG image.", usage=_usage(0.04)))  # the nudge

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _ok_invoke)
    r = client.post("/chat", json=_TOOL_BODY)
    assert r.status_code == 200, r.text
    assert n["i"] == 3, "the nudge really ran (anti-vacuity)"
    assert r.json()["usage"]["cost_usd"] == pytest.approx(0.07)
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].cost_usd == pytest.approx(0.07)


def test_stream_lane_bills_the_final_answer_nudge(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    s = {"i": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        s["i"] += 1
        if s["i"] == 1:
            yield _final("", _usage(0.01), [_TOOL_CALL])
        else:
            yield _final("", _usage(0.02))

    nudges: list = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        nudges.append(messages[-1].content)
        return _cli(LLMResponse(text="Saved the report.", usage=_usage(0.04)))

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _ok_invoke)
    r = client.post("/chat/stream", json=_TOOL_BODY)
    assert r.status_code == 200
    assert nudges == [chat_turn.FINAL_ANSWER_INSTRUCTION]
    done = next(d for e, d in _parse_sse(r.text) if e == "done")
    assert done["usage"]["cost_usd"] == pytest.approx(0.07)
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].cost_usd == pytest.approx(0.07)


def _english_only(client):
    r = client.put("/profile", json={"values": {"language": "en", "enforce_language": True}})
    assert r.status_code == 200, r.text


def test_post_lane_bills_the_language_rewrite(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _english_only(client)
    calls: list = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _cli(LLMResponse(text=f"Sure. {CHINESE}", usage=_usage(0.01)))
        return _cli(LLMResponse(text="Sure. Here is the answer.", usage=_usage(0.02)))

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert len(calls) == 2, "the rewrite really ran (anti-vacuity)"
    assert r.json()["usage"]["cost_usd"] == pytest.approx(0.03)
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].cost_usd == pytest.approx(0.03)


def test_stream_lane_bills_the_language_rewrite(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _english_only(client)

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        yield {"type": "text", "text": f"Sure. {CHINESE}"}
        yield _final(f"Sure. {CHINESE}", _usage(0.01))

    rewrites: list = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        rewrites.append(1)
        return _cli(LLMResponse(text="Sure. Here is the answer.", usage=_usage(0.02)))

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert rewrites == [1], "the rewrite really ran (anti-vacuity)"
    done = next(d for e, d in _parse_sse(r.text) if e == "done")
    assert done["usage"]["cost_usd"] == pytest.approx(0.03)
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].cost_usd == pytest.approx(0.03)


# --------------------------------------------------------------------------- #
# 6e. eval/usage_view flags a subscription row the rollup did not
# --------------------------------------------------------------------------- #


def test_merged_usage_flags_subscription_rows_itself(platform, monkeypatch, credits_catalog):
    """``merged_usage`` folds rows from other stores and owns the flag for
    every row it serves — not only the ones ``usage_summary`` already marked."""
    rows = [
        {"provider": "claude-cli", "model": "claude-opus-5-5", "input_tokens": 10,
         "output_tokens": 1, "cost_usd": 0.5, "runs": 1},
        {"provider": "claude-cli", "model": "claude-fable-5-1", "input_tokens": 10,
         "output_tokens": 1, "cost_usd": 0.9, "runs": 1},
        {"provider": "anthropic", "model": "claude-opus-4-8", "input_tokens": 10,
         "output_tokens": 1, "cost_usd": 0.1, "runs": 1},
    ]
    summary = {"since_days": 30, "totals": {"input_tokens": 30, "output_tokens": 3,
                                            "cost_usd": 1.0, "runs": 3},
               "by_day": [], "by_model": rows}
    monkeypatch.setattr(platform.observability, "usage_summary", lambda days: summary)
    out = merged_usage(platform, 30)
    by = {r["model"]: r for r in out["by_model"]}
    assert by["claude-opus-5-5"]["list_price_equivalent"] is True
    assert "list_price_equivalent" not in by["claude-fable-5-1"]  # credits = money
    assert "list_price_equivalent" not in by["claude-opus-4-8"]
    assert out["totals"]["list_price_equivalent_usd"] == 0.0

