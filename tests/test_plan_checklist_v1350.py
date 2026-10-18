"""v1.350.0 lane C2 — an agent's plan as a live checklist, and the optional
plan approval (agent-elements item #2, daemon half; ideas only, MIT).

THE USER'S DECISION (2026-10-17): the plan is shown as a live checklist and
NOTHING WAITS by default; a setting "Ask me to approve an agent's plan before
it starts" exists and is OFF.

* ``GET /sessions/{id}/plan`` answers ``{run_id, steps: [{goal, state}]}`` for
  the session's newest planned run, state waiting | running | done | failed |
  skipped, derived from the stored step results and whether the run is live.
  No plan, an unknown session or an unreadable record is a plain 404.
* ``config.plan_approval`` (OFF) + one settings-schema row.
* The gate in ``decompose.run_decomposed``: only with the setting on, only for
  an ATTENDED origin, never on a resume — one ordinary ask (tool
  ``approve_plan``, args ``{"steps": [...]}``) through the runtime's pause.
  Allow runs the plan unchanged; Deny ends the run before any step, FAILED,
  its result saying so.

Every e2e test below drives the REAL orchestrator → runtime → decompose path
with a scripted text-only adapter (the router wraps it in the prompted
scaffold, which is what engages the planned-steps lane).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents import transcript as tr
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    PLAN_APPROVAL_TOOL,
    PLAN_DECLINED_RESULT,
    PLAN_UNANSWERED_RESULT,
    AgentRuntime,
    plan_approval_wanted,
)
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.config import Config
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    RunPlan,
    Session,
    SessionStatus,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.settings import schema

#: Long enough (> 200 chars) to engage the planned-steps lane, and free of
#: bulk words ("every", "all", "each"), which route to the worklist plan.
_LONG_TASK = (
    "Create a file named statements.txt that notes the three quarterly "
    "statements, and after that produce a second file named summary.txt "
    "holding a short note about anything that looked out of place, keeping "
    "both files inside the session workspace so they can be reviewed later."
)
_GOALS = ["collect the statements", "write the summary"]
_PLAN = json.dumps({"steps": [{"goal": g} for g in _GOALS]})


class _TextOnly(LLMAdapter):
    """Scripted text-only adapter. Running out of replies RAISES, so a step
    that should never have run fails the run loudly instead of passing."""

    def __init__(self, replies, provider="local-plan"):
        self.provider = provider
        self.model = "m"
        self._replies = list(replies)
        self.calls: list[list[LLMMessage]] = []

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": False, "vision": False}

    async def complete(self, *, system, messages, tools):
        self.calls.append(list(messages))
        return LLMResponse(text=self._replies.pop(0), usage={"input_tokens": 1, "output_tokens": 1})


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({
            "type": str(getattr(type, "value", type)),
            "payload": payload or {},
            "session_id": session_id,
        })
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(
        app=app, platform=platform, published=published, orch=Orchestrator(platform)
    )


def _events(rt, type_: str, session_id: str | None = None) -> list[dict]:
    return [
        p for p in rt.published
        if p["type"] == type_ and (session_id is None or p["session_id"] == session_id)
    ]


async def _wait_for(pred, *, tries=3000, sleep=0.01) -> bool:
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(sleep)
    return False


def _plan_asks(rt, session_id: str) -> list[dict]:
    return [
        a for a in rt.platform.approvals.pending_for(session_id)
        if a.get("tool") == PLAN_APPROVAL_TOOL
    ]


def _run_row(engine, session_id: str) -> AgentRun:
    with session_scope(engine) as db:
        row = db.exec(select(AgentRun).where(AgentRun.session_id == session_id)).first()
        db.expunge(row)
        return row


def _plan_results(engine, run_id: str) -> list[dict]:
    with session_scope(engine) as db:
        return json.loads(db.get(RunPlan, run_id).results_json)


async def _get_plan(app, session_id: str) -> httpx.Response:
    """The route, on THIS loop (the approvals registry the route reads is
    the one the paused run filed its ask in)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.get(f"/sessions/{session_id}/plan")


async def _start(rt, inner: _TextOnly, origin: str | None):
    rt.platform.providers.register(inner.provider, lambda model=None: inner)
    sess = await rt.orch.create_session(
        _LONG_TASK, AgentType.BUILDER, inner.provider, origin=origin
    )
    task = asyncio.create_task(rt.orch.run_session(sess.id))
    return sess, task


async def _finish(task) -> None:
    try:
        await asyncio.wait_for(task, timeout=60)
    finally:
        if not task.done():
            task.cancel()


# --------------------------------------------------------------------------- #
# 1. THE SETTING
# --------------------------------------------------------------------------- #
def test_the_setting_row_exists_off_by_default_and_turning_it_off_from_chat_is_protected(tmp_path):
    d = schema.get("plan_approval")
    assert d.label == "Ask me to approve an agent's plan before it starts"
    assert (d.group, d.type, d.store) == ("automation", "bool", "config")
    # A neighbour's tier (busy_runs_notice_at is allow); turning the check
    # OFF from chat asks every time.
    assert d.tier == schema.get("busy_runs_notice_at").tier == "allow"
    assert d.tier_for(True) == "allow" and d.tier_for(False) == "ask-floor"
    assert "chat or the Agents page" in d.help and "background runs never do" in d.help
    assert "plan_approval" in schema.daemon_keys()
    assert Config(home=tmp_path, project_root=tmp_path).plan_approval is False


def test_the_settings_page_can_turn_it_on_and_the_gate_reads_that_value(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    rows = {r["key"]: r for r in client.get("/settings/schema").json()["settings"]}
    assert rows["plan_approval"]["group"] == "automation"
    assert client.get("/settings/values").json()["values"]["plan_approval"] is False
    cfg = app.state.platform.config
    chat = SimpleNamespace(origin="chat")
    assert plan_approval_wanted(cfg, chat) is False
    r = client.put("/settings/values", json={"values": {"plan_approval": True}})
    assert r.status_code == 200, r.text
    assert cfg.plan_approval is True and plan_approval_wanted(cfg, chat) is True


@pytest.mark.parametrize("origin", ["chat", "job:agents", "job:mission", "project:p1", "user"])
def test_an_attended_origin_waits_when_the_setting_is_on(origin):
    on, off = SimpleNamespace(plan_approval=True), SimpleNamespace(plan_approval=False)
    assert plan_approval_wanted(on, SimpleNamespace(origin=origin)) is True
    assert plan_approval_wanted(off, SimpleNamespace(origin=origin)) is False


@pytest.mark.parametrize(
    "origin",
    [None, "", "schedule:nightly", "goal:g1", "workflow:w1", "reflex:r1",
     "comm:telegram", "autonomy", "assignment:a1", "self_dev"],
)
def test_a_background_or_untagged_origin_never_waits(origin):
    assert plan_approval_wanted(SimpleNamespace(plan_approval=True), SimpleNamespace(origin=origin)) is False


def test_a_stub_config_that_is_not_literally_true_never_waits():
    # A MagicMock-style attribute (truthy, not True) must not arm the gate.
    assert plan_approval_wanted(SimpleNamespace(plan_approval="yes"), SimpleNamespace(origin="chat")) is False
    assert plan_approval_wanted(None, SimpleNamespace(origin="chat")) is False


# --------------------------------------------------------------------------- #
# 2. THE GATE — nothing waits unless asked to
# --------------------------------------------------------------------------- #
async def test_with_the_setting_off_an_attended_run_does_not_wait(rt):
    assert rt.platform.config.plan_approval is False  # the shipped default
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    sess, task = await _start(rt, inner, "chat")
    await _finish(task)
    row = rt.orch.get_session(sess.id)
    assert row.status is SessionStatus.COMPLETED
    assert _events(rt, "approval.requested", sess.id) == []
    assert len(inner.calls) == 4  # plan, two steps, assemble
    assert [e["payload"]["steps"] for e in _events(rt, "plan.created", sess.id)] == [_GOALS]


@pytest.mark.parametrize("origin", ["schedule:nightly", "workflow:w1", "comm:telegram"])
async def test_with_the_setting_on_a_background_run_does_not_wait(rt, origin):
    rt.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    sess, task = await _start(rt, inner, origin)
    await _finish(task)
    assert rt.orch.get_session(sess.id).status is SessionStatus.COMPLETED
    assert _events(rt, "approval.requested", sess.id) == []
    assert len(inner.calls) == 4


async def test_with_the_setting_on_a_resumed_plan_carries_on_without_asking(rt):
    """The resume path skips planning, and so it skips the plan ask: the run
    carries on in the plan it already had (the user pressed Continue)."""
    rt.platform.config.plan_approval = True
    with session_scope(rt.platform.engine) as db:
        sess = Session(task=_LONG_TASK, status=SessionStatus.FAILED, provider="local-plan",
                       origin="chat", workspace_path=str(rt.platform.config.workspaces_dir / "x"))
        db.add(sess)
        db.flush()
        run = AgentRun(session_id=sess.id, provider="local-plan")
        db.add(run)
        db.commit()
        sid, rid = sess.id, run.id
    tr._write_plan(
        rt.platform.engine, rid, sid,
        json.dumps([{"goal": g, "success_criteria": "", "tools": []} for g in _GOALS]),
        json.dumps([{"index": 0, "goal": _GOALS[0], "ok": True, "output": "Collected Q1-Q3."}]),
    )
    inner = _TextOnly(["Wrote summary.txt.", "All done."])  # no plan reply: no re-plan
    rt.platform.providers.register("local-plan", lambda model=None: inner)
    new = await rt.orch.continue_session(sid, "Continue where you left off", resume=True)
    assert new.origin == "chat"  # attended: the gate WOULD apply to a fresh plan
    done = await asyncio.wait_for(
        rt.orch.run_session(new.id, get_agent_definition(AgentType.BUILDER)), timeout=60
    )
    assert done.status is SessionStatus.COMPLETED
    assert _events(rt, "approval.requested", new.id) == []
    assert len(inner.calls) == 2  # step 2 + assemble


# --------------------------------------------------------------------------- #
# 3. THE ASK — Allow runs the plan, Deny runs nothing
# --------------------------------------------------------------------------- #
async def test_with_the_setting_on_an_attended_run_waits_and_allow_runs_the_plan(rt):
    rt.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    sess, task = await _start(rt, inner, "chat")
    try:
        assert await _wait_for(lambda: bool(_plan_asks(rt, sess.id))), "the plan was never put to the user"
        assert await _wait_for(lambda: bool(_events(rt, "approval.requested", sess.id)))
        await asyncio.sleep(0.3)  # any step that would (wrongly) start has the chance to
        assert len(inner.calls) == 1, "a step ran before the plan was approved"
        assert _events(rt, "plan.step_started", sess.id) == []
        assert _run_row(rt.platform.engine, sess.id).state is AgentState.WAITING
        # ONE ordinary ask on the ledger: the steps ride as its arguments, an
        # attended ask has no clock, and a plan is approved or declined only.
        req = _events(rt, "approval.requested", sess.id)
        assert len(req) == 1
        payload = req[0]["payload"]
        assert payload["tool"] == PLAN_APPROVAL_TOOL
        assert payload["args"] == {"steps": _GOALS}
        assert payload["timeout_s"] == 0
        assert payload["once_only"] is True and payload["can_always"] is False
        # The plan was announced before the ask (the checklist has its goals).
        kinds = [p["type"] for p in rt.published if p["session_id"] == sess.id]
        assert kinds.index("plan.created") < kinds.index("approval.requested")
        # The checklist: every step waiting while the plan awaits Approve.
        r = await _get_plan(rt.app, sess.id)
        assert r.status_code == 200, r.text
        assert [s["state"] for s in r.json()["steps"]] == ["waiting", "waiting"]
        assert rt.platform.approvals.resolve(payload["approval_id"], "once")
        await _finish(task)
    finally:
        if not task.done():
            task.cancel()
    row = rt.orch.get_session(sess.id)
    assert row.status is SessionStatus.COMPLETED
    assert len(inner.calls) == 4
    run_id = _run_row(rt.platform.engine, sess.id).id
    # The plan ran UNCHANGED, and plan.step_completed kept its exact shape.
    assert [e["payload"] for e in _events(rt, "plan.step_completed", sess.id)] == [
        {"run_id": run_id, "index": 0, "ok": True},
        {"run_id": run_id, "index": 1, "ok": True},
    ]
    assert [e["payload"]["decision"] for e in _events(rt, "approval.resolved", sess.id)] == ["once"]
    r = await _get_plan(rt.app, sess.id)
    assert r.json() == {
        "run_id": run_id,
        "steps": [{"goal": g, "state": "done"} for g in _GOALS],
    }


async def test_deny_runs_no_step_and_the_run_says_so(rt):
    rt.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN])  # a step that ran would exhaust the script
    sess, task = await _start(rt, inner, "job:agents")
    try:
        assert await _wait_for(lambda: bool(_plan_asks(rt, sess.id)))
        ask = _plan_asks(rt, sess.id)[0]
        assert rt.platform.approvals.resolve(ask["approval_id"], "deny")
        await _finish(task)
    finally:
        if not task.done():
            task.cancel()
    row = rt.orch.get_session(sess.id)
    # Honest: the job was not done, so the session does not read completed.
    assert row.status is SessionStatus.FAILED
    assert row.outcome != "completed"
    assert row.summary.startswith(PLAN_DECLINED_RESULT)
    assert "The plan was:\n1. collect the statements\n2. write the summary" in row.summary
    run = _run_row(rt.platform.engine, sess.id)
    assert run.state is AgentState.FAILED and run.result.startswith(PLAN_DECLINED_RESULT)
    # No step ran — not the plan's, and not the flat loop's either.
    assert len(inner.calls) == 1
    assert _events(rt, "plan.step_started", sess.id) == []
    assert [e["payload"]["decision"] for e in _events(rt, "approval.resolved", sess.id)] == ["deny"]
    # Every step is recorded as not attempted, and the checklist says skipped.
    results = _plan_results(rt.platform.engine, run.id)
    assert [(r["index"], r["attempted"], r["ok"]) for r in results] == [(0, False, False), (1, False, False)]
    assert {r["reason"] for r in results} == {"not run — you declined the plan"}
    r = await _get_plan(rt.app, sess.id)
    assert [s["state"] for s in r.json()["steps"]] == ["skipped", "skipped"]


async def test_an_unanswered_plan_ask_runs_nothing_and_reads_needs_you(rt, monkeypatch):
    """Reachable only where the attended clock is set (a seam; the app
    waits with no clock): the run ends with no step run, and the ledger's
    unanswered ask makes the outcome needs_you."""
    rt.platform.config.plan_approval = True
    monkeypatch.setattr(runtime_mod, "ATTENDED_APPROVAL_TIMEOUT_S", 0.2)
    inner = _TextOnly([_PLAN])
    sess, task = await _start(rt, inner, "chat")
    await _finish(task)
    row = rt.orch.get_session(sess.id)
    assert row.status is SessionStatus.FAILED
    assert row.summary.startswith(PLAN_UNANSWERED_RESULT)
    assert row.outcome == "needs_you"
    assert len(inner.calls) == 1
    run = _run_row(rt.platform.engine, sess.id)
    assert {r["reason"] for r in _plan_results(rt.platform.engine, run.id)} == {
        "not run — the plan was not approved"
    }


async def test_a_conversation_answer_runs_the_plan_and_is_kept_for_the_next_run(rt):
    rt.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    sess, task = await _start(rt, inner, "chat")
    try:
        assert await _wait_for(lambda: bool(_plan_asks(rt, sess.id)))
        assert rt.platform.approvals.resolve(_plan_asks(rt, sess.id)[0]["approval_id"], "conversation")
        await _finish(task)
    finally:
        if not task.done():
            task.cancel()
    row = rt.orch.get_session(sess.id)
    assert row.status is SessionStatus.COMPLETED and len(inner.calls) == 4
    # Written where the NEXT run reads (the v1.232.0 rule), so the chat's
    # follow-up run does not ask about its plan again.
    assert PLAN_APPROVAL_TOOL in json.loads(row.allow_tools_json)


async def test_a_plan_is_never_offered_always_and_an_always_answer_remembers_nothing(rt):
    """A run WITH a grant scope (an agent name) would normally be offered
    "Always allow exactly this"; a plan is not — the next plan is a different
    one. An "always" answer that arrives anyway approves THIS plan only."""
    rt.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    rt.platform.providers.register(inner.provider, lambda model=None: inner)
    sess = await rt.orch.create_session(
        _LONG_TASK, AgentType.BUILDER, inner.provider, origin="job:agents", agent_name="builder"
    )
    assert AgentRuntime.grant_scopes(sess) == [("agent", "builder")]  # a scope exists
    task = asyncio.create_task(rt.orch.run_session(sess.id))
    try:
        assert await _wait_for(lambda: bool(_events(rt, "approval.requested", sess.id)))
        payload = _events(rt, "approval.requested", sess.id)[0]["payload"]
        assert payload["can_always"] is False
        assert rt.platform.approvals.resolve(payload["approval_id"], "always")
        await _finish(task)
    finally:
        if not task.done():
            task.cancel()
    assert rt.orch.get_session(sess.id).status is SessionStatus.COMPLETED
    assert len(inner.calls) == 4
    assert [g.tool for g in rt.platform.grants.list()] == []


def test_the_plan_ask_ignores_the_permission_tier_and_only_a_grant_lifts_it(tmp_path):
    app = create_app(str(tmp_path))
    runtime = AgentRuntime(app.state.platform)
    agent_def = get_agent_definition(AgentType.BUILDER)
    call = SimpleNamespace(name=PLAN_APPROVAL_TOOL, arguments={"steps": _GOALS})
    chat = SimpleNamespace(id="s1", origin="chat", approval_mode="")
    # A policy that would ALLOW (or DENY) a tool by that name changes nothing:
    # the setting is this ask's tier.
    for mode in ("allow", "deny"):
        app.state.platform.permissions._base[PLAN_APPROVAL_TOOL] = mode
        assert runtime._pause_needed(chat, call, agent_def, set()) == PLAN_APPROVAL_TOOL
    assert runtime._pause_needed(chat, call, agent_def, {PLAN_APPROVAL_TOOL}) == ""
    # always_ask: only an answer given in THIS run lifts it.
    strict = SimpleNamespace(id="s2", origin="chat", approval_mode="always_ask")
    assert runtime._pause_needed(strict, call, agent_def, {PLAN_APPROVAL_TOOL}) == PLAN_APPROVAL_TOOL
    runtime._run_grants["s2"] = {PLAN_APPROVAL_TOOL}
    assert runtime._pause_needed(strict, call, agent_def, set()) == ""
    # An origin that cannot ask never pauses (the ASKING_ORIGINS allowlist).
    assert runtime._pause_needed(SimpleNamespace(id="s3", origin=""), call, agent_def, set()) == ""


# --------------------------------------------------------------------------- #
# 4. THE CHECKLIST ROUTE
# --------------------------------------------------------------------------- #
def _seed(engine, *, status: SessionStatus, run_state: AgentState, goals, results,
          plan_text: str | None = None, results_text: str | None = None) -> tuple[str, str]:
    with session_scope(engine) as db:
        sess = Session(task="t", status=status, origin="chat")
        db.add(sess)
        db.flush()
        run = AgentRun(session_id=sess.id, state=run_state)
        db.add(run)
        db.commit()
        sid, rid = sess.id, run.id
    tr._write_plan(
        engine, rid, sid,
        plan_text if plan_text is not None else json.dumps(
            [{"goal": g, "success_criteria": "", "tools": []} for g in goals]
        ),
        results_text if results_text is not None else json.dumps(results),
    )
    return sid, rid


def _result(i: int, ok: bool, attempted: bool = True) -> dict:
    return {"index": i, "goal": f"g{i}", "ok": ok, "attempted": attempted}


_THREE = ["read the ledger", "fix the totals", "write the note"]


@pytest.mark.parametrize(
    ("status", "run_state", "results", "expected"),
    [
        # FINISHED: a pass, a failure, and a step the budget never reached.
        (SessionStatus.COMPLETED, AgentState.COMPLETED,
         [_result(0, True), _result(1, False), _result(2, False, attempted=False)],
         ["done", "failed", "skipped"]),
        # FAILED mid-plan: the cut-off step failed, the rest never ran.
        (SessionStatus.FAILED, AgentState.FAILED, [_result(0, True)],
         ["done", "failed", "skipped"]),
        # STOPPED by the user: nothing after the last finished step ran.
        (SessionStatus.CANCELLED, AgentState.CANCELLED, [_result(0, True)],
         ["done", "skipped", "skipped"]),
        # LIVE: one done, the next running, the last waiting.
        (SessionStatus.ACTIVE, AgentState.RUNNING, [_result(0, True)],
         ["done", "running", "waiting"]),
        # LIVE, nothing finished yet (a step-level ask keeps it "running").
        (SessionStatus.ACTIVE, AgentState.WAITING, [],
         ["running", "waiting", "waiting"]),
        # The session row still says active but the RUN is over (the settle
        # gap): not live.
        (SessionStatus.ACTIVE, AgentState.FAILED, [_result(0, True)],
         ["done", "failed", "skipped"]),
    ],
    ids=["finished", "failed", "cancelled", "live", "live-first-step", "run-over"],
)
def test_the_route_derives_each_state(tmp_path, status, run_state, results, expected):
    app = create_app(str(tmp_path))
    sid, rid = _seed(app.state.platform.engine, status=status, run_state=run_state,
                     goals=_THREE, results=results)
    r = TestClient(app).get(f"/sessions/{sid}/plan")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "run_id": rid,
        "steps": [{"goal": g, "state": s} for g, s in zip(_THREE, expected)],
    }


def test_the_newest_planned_run_of_the_session_is_the_one_shown(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    sid, old = _seed(engine, status=SessionStatus.ACTIVE, run_state=AgentState.FAILED,
                     goals=["old plan step", "old step two"], results=[_result(0, True)])
    with session_scope(engine) as db:
        older = db.get(AgentRun, old)
        older.created_at = older.created_at - timedelta(minutes=5)
        db.add(older)
        db.commit()
    with session_scope(engine) as db:
        newer = AgentRun(session_id=sid, state=AgentState.RUNNING)
        db.add(newer)
        db.commit()
        new_id = newer.id
    tr._write_plan(engine, new_id, sid,
                   json.dumps([{"goal": g, "success_criteria": "", "tools": []} for g in _THREE]), "[]")
    body = TestClient(app).get(f"/sessions/{sid}/plan").json()
    assert body["run_id"] == new_id
    assert [s["goal"] for s in body["steps"]] == _THREE
    assert [s["state"] for s in body["steps"]] == ["running", "waiting", "waiting"]


def test_no_plan_is_a_plain_404(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    with session_scope(app.state.platform.engine) as db:
        sess = Session(task="t", status=SessionStatus.COMPLETED)
        db.add(sess)
        db.commit()
        sid = sess.id
    r = client.get(f"/sessions/{sid}/plan")
    assert r.status_code == 404
    assert r.json()["detail"] == "No plan for this session: its run has not split the work into steps."
    # An empty plan record is no plan either (``has_plan``'s rule).
    sid2, _ = _seed(app.state.platform.engine, status=SessionStatus.COMPLETED,
                    run_state=AgentState.COMPLETED, goals=[], results=[], plan_text="[]")
    assert client.get(f"/sessions/{sid2}/plan").status_code == 404
    r = client.get("/sessions/session_nope/plan")
    assert r.status_code == 404 and r.json()["detail"] == "session not found"


@pytest.mark.parametrize(
    ("plan_text", "results_text"),
    [
        ("not json", "[]"),
        ('{"steps": []}', "[]"),                       # not a list
        ('[{"nogoal": 1}]', "[]"),                     # a step without a goal
        ('[{"goal": "a"}]', "{"),                      # results unreadable
        ('[{"goal": "a"}]', '["x"]'),                  # a result that is not an object
        ('[{"goal": "a"}]', '[{"ok": true}, {"ok": true}]'),  # more results than steps
    ],
    ids=["garbage", "object", "no-goal", "bad-results", "result-not-object", "too-many"],
)
def test_an_unreadable_plan_is_a_plain_404_never_a_500(tmp_path, plan_text, results_text):
    app = create_app(str(tmp_path))
    sid, _ = _seed(app.state.platform.engine, status=SessionStatus.COMPLETED,
                   run_state=AgentState.COMPLETED, goals=[], results=[],
                   plan_text=plan_text, results_text=results_text)
    r = TestClient(app, raise_server_exceptions=False).get(f"/sessions/{sid}/plan")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "The plan this session kept could not be read."


def test_an_unchanged_checklist_poll_is_a_304(tmp_path):
    app = create_app(str(tmp_path))
    sid, _ = _seed(app.state.platform.engine, status=SessionStatus.ACTIVE,
                   run_state=AgentState.RUNNING, goals=_THREE, results=[])
    client = TestClient(app)
    first = client.get(f"/sessions/{sid}/plan")
    etag = first.headers["etag"]
    again = client.get(f"/sessions/{sid}/plan", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""


async def test_the_route_reads_off_the_event_loop(tmp_path, monkeypatch):
    """The DB reads hop to a worker thread (the NOTHING BLOCKING rule)."""
    import threading

    app = create_app(str(tmp_path))
    sid, _ = _seed(app.state.platform.engine, status=SessionStatus.ACTIVE,
                   run_state=AgentState.RUNNING, goals=_THREE, results=[])
    seen: list[str] = []
    real = tr.latest_plan

    def spy(engine, session_id):
        seen.append(threading.current_thread().name)
        return real(engine, session_id)

    monkeypatch.setattr(tr, "latest_plan", spy)
    loop_thread = threading.current_thread().name
    r = await _get_plan(app, sid)
    assert r.status_code == 200
    assert seen and seen[0] != loop_thread
