"""v1.505.0 (north-star 5.2, decision D10): a schedule or a workflow can be
approved ONCE.

What was read (not guessed): a schedule's task fire called ``create_session``
with neither ``allow_tools`` nor ``agent_name`` (even for a custom agent), a
workflow's agent step the same, and a standing grant's scopes were goal, agent
and project only (``AgentRuntime.grant_scopes``). So a schedule's run had no
scope, its card said ``can_always: false``, "Allow for this run" died with
that one run, and "every Monday at 8, build the rent list" parked every Monday.

Now ``schedule`` and ``workflow`` are standing-grant scopes, read from the
run's own origin stamp (``schedule:<name>``, ``workflow:<name>``); the card
offers "Always for this schedule" / "Always for this workflow"; the grant is a
plain ``standinggrant`` row, so ``GET /grants``, ``grant_revoke`` and the
schedule's / workflow's own row show it and take it back; the floor and a
deny stand.

THE GATE IS PROVEN THROUGH THE LAYER THAT REFUSES (the root rule): the
schedule fires through ``POST /schedules/{name}/run`` with the REAL registry
and permission engine; only ``shell``'s ``execute`` is stubbed (so "it ran"
means the gate let it through) and the model's two rounds are scripted.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import SCOPE_KINDS, args_hash, grant_label, pick_scope
from iron_jarvis.core.models import AgentType, ToolInvocation
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.workflows.engine import (
    Step,
    WorkflowDef,
    WorkflowEngine,
    workflow_grant_scope_id,
)

GIT = {"command": "git status"}
RM = {"command": "rm -rf build"}
SCHEDULE = "rent-list"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    assert platform.grants is not None, "the platform builds its grant store"
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": type, "payload": payload or {}, "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(
        runtime=AgentRuntime(platform), platform=platform, store=platform.grants,
        published=published, app=app,
    )


def _session(sid="session_s", origin=f"schedule:{SCHEDULE}", agent_name="", project_id=None):
    return SimpleNamespace(
        id=sid, origin=origin, approval_mode="", agent_name=agent_name,
        project_id=project_id, trust="full",
    )


def _tc(args=None, name="shell"):
    return SimpleNamespace(name=name, arguments=dict(args or GIT))


def _events(rt, type_, session_id=None):
    return [
        p for p in rt.published
        if p["type"] == type_ and (session_id is None or p["session_id"] == session_id)
    ]


async def _answer(rt, decision: str, *, session_id: str):
    for _ in range(400):
        reqs = _events(rt, "approval.requested", session_id)
        if reqs:
            assert rt.platform.approvals.resolve(reqs[-1]["payload"]["approval_id"], decision)
            return reqs[-1]["payload"]
        await asyncio.sleep(0.01)
    raise AssertionError("the pause never published its request")


def _stub_shell(platform) -> list[dict]:
    """Stub ONE tool's `execute`; everything before it (the gate, the ledger) is real."""
    tool = platform.registry.get("shell")
    calls: list[dict] = []

    async def fake_execute(args, ctx=None, *a, **kw):
        calls.append(dict(args))
        return ToolResult(ok=True, output="ran")

    tool.execute = fake_execute  # type: ignore[method-assign]
    return calls


def _shell_then_done(command="git status"):
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c0", name="shell", arguments={"command": command}),
            ])
        else:
            resp = LLMResponse(text="Done.")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _ok_shell_rows(engine, session_id: str) -> list[ToolInvocation]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == session_id)).all())
        for r in rows:
            db.expunge(r)
    return [r for r in rows if r.ok and r.tool == "shell"]


def _fire(client, rt) -> str:
    rt.platform.router.stream = _shell_then_done()
    fired = client.post(f"/schedules/{SCHEDULE}/run").json()
    sid = fired.get("last_session_id") or ""
    assert sid, fired
    return sid


# --------------------------------------------------------------------------- #
# 1. the scopes
# --------------------------------------------------------------------------- #


def test_a_schedule_and_a_workflow_are_grant_scopes_read_from_the_origin():
    s = AgentRuntime.grant_scopes
    assert "schedule" in SCOPE_KINDS and "workflow" in SCOPE_KINDS
    assert s(_session()) == [("schedule", SCHEDULE)]
    assert s(_session(origin="workflow:Monthly close")) == [("workflow", "Monthly close")]
    assert s(_session(agent_name="custom:remy")) == [("schedule", SCHEDULE), ("agent", "custom:remy")]
    assert s(_session(origin="schedule:")) == []
    assert s(_session(origin="chat")) == []
    # "Always" on a schedule's card is kept ON the schedule, even when the
    # schedule runs a custom agent or is grounded in a project.
    assert pick_scope([("agent", "custom:remy"), ("schedule", SCHEDULE)]) == ("schedule", SCHEDULE)
    assert pick_scope([("project", "p1"), ("workflow", "w")]) == ("workflow", "w")
    # Unchanged for the scopes that were there.
    assert pick_scope([("goal", "g"), ("project", "p"), ("agent", "a")]) == ("agent", "a")


def test_a_workflow_steps_scope_is_its_origin_stamp():
    assert workflow_grant_scope_id("Monthly close") == "Monthly close"
    # The origin stamp is laundered and clipped; the scope id is the same text.
    odd = "rent/list <monthly>" + "x" * 80
    sid = workflow_grant_scope_id(odd)
    assert AgentRuntime.grant_scopes(_session(origin=f"workflow:{sid}")) == [("workflow", sid)]
    assert "/" not in sid and "<" not in sid and len("workflow:" + sid) <= 64


async def test_a_workflows_agent_step_carries_the_workflow_scope(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    made: list[dict] = []

    class _Orch:
        async def create_session(self, task, agent_type, **kw):
            made.append(kw)
            raise RuntimeError("stop here")

    engine = WorkflowEngine(platform, _Orch())
    wf = WorkflowDef(name="Monthly close", steps=[Step(name="Draft", kind="agent", task="x")])
    rec = await asyncio.to_thread(engine.create_record, wf)
    await engine.run_record(rec, wf)
    origin = made[0]["origin"]
    assert AgentRuntime.grant_scopes(SimpleNamespace(origin=origin)) == [("workflow", "Monthly close")]


# --------------------------------------------------------------------------- #
# 2. the card and "Always"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("origin", "kind", "scope_id"),
    [(f"schedule:{SCHEDULE}", "schedule", SCHEDULE), ("workflow:Monthly close", "workflow", "Monthly close")],
)
async def test_the_card_offers_always_for_this_schedule_or_workflow_and_keeps_it_there(rt, origin, kind, scope_id):
    agent_def = get_agent_definition(AgentType.BUILDER)
    first = _session(sid="session_a", origin=origin, agent_name="custom:remy")
    answerer = asyncio.create_task(_answer(rt, "always", session_id="session_a"))
    deny, extra = await rt.runtime._pause_for_approval(first, _tc(), agent_def, set())
    payload = await answerer
    assert deny == "" and extra == {"shell"}, "always is once for THIS call"
    assert payload["can_always"] is True
    assert payload["always_scope"] == kind
    await rt.store.flush()
    live = rt.store.list(kind, scope_id)
    assert [(g.tool, g.args_hash) for g in live] == [("shell", args_hash("shell", GIT))]
    assert live[0].expires_at is not None
    assert not rt.store.list("agent", "custom:remy"), "kept on the schedule / workflow, not the agent"
    # The NEXT run of the same schedule / workflow: the identical call needs no card.
    second = _session(sid="session_b", origin=origin)
    assert rt.runtime._pause_needed(second, _tc(), agent_def, set()) == ""
    assert rt.runtime._pause_needed(second, _tc(RM), agent_def, set()) == "shell", "other arguments still ask"
    other = _session(sid="session_c", origin=f"{kind}:another")
    assert rt.runtime._pause_needed(other, _tc(), agent_def, set()) == "shell", "another one still asks"


async def test_a_goal_card_carries_no_always_scope_word(rt):
    """Only the two new scopes add ``always_scope``: every other card's event
    is the shape it was."""
    agent_def = get_agent_definition(AgentType.BUILDER)
    answerer = asyncio.create_task(_answer(rt, "deny", session_id="session_g"))
    await rt.runtime._pause_for_approval(_session(sid="session_g", origin="goal:g1"), _tc(), agent_def, set())
    payload = await answerer
    assert payload["can_always"] is True and "always_scope" not in payload


def test_the_floor_and_a_deny_stand_under_the_new_scopes(rt):
    store = rt.store
    scopes = [("schedule", SCHEDULE)]
    for kind in ("schedule", "workflow"):
        with pytest.raises(ValueError):
            store.create(kind, "x", "shell", "", "any shell command")  # floor: exact only
        with pytest.raises(ValueError):
            store.create(kind, "x", "web_fetch", "", "x", expires_days=None)  # never: goals only
    store.create("schedule", SCHEDULE, "shell", args_hash("shell", GIT), grant_label("shell", GIT))
    eng = PermissionEngine({"shell": "ask"})
    assert eng.authorize("shell", GIT, None, None, grants=store, scopes=scopes).allowed is True
    assert eng.authorize("shell", RM, None, None, grants=store, scopes=scopes).allowed is False
    # A custom agent's definition that denies the tool: a grant never lifts it.
    assert eng.authorize("shell", GIT, {"shell": "deny"}, None, grants=store, scopes=scopes).allowed is False


# --------------------------------------------------------------------------- #
# 3. end to end through the schedule's fire
# --------------------------------------------------------------------------- #


def test_the_schedule_asks_then_runs_with_its_standing_ok_then_asks_again_after_remove(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    ran = _stub_shell(rt.platform)
    client = TestClient(rt.app)
    r = client.post(
        "/schedules",
        json={"name": SCHEDULE, "cron": "0 8 * * 1", "kind": "task", "payload": {"task": "build the rent list"}},
    )
    assert r.status_code == 200, r.text

    # 1. No standing OK: the fire asks, and the card offers "Always for this schedule".
    sid1 = _fire(client, rt)
    asks = _events(rt, "approval.requested", sid1)
    assert asks, "without a standing OK the schedule's run must ask"
    assert asks[-1]["payload"]["can_always"] is True
    assert asks[-1]["payload"]["always_scope"] == "schedule"
    assert ran == [], "the unanswered call must not run"

    # 2. The OK the card's "Always" writes (the runtime's own mint path).
    made = rt.runtime._mint_always(_session(origin=f"schedule:{SCHEDULE}"), [_tc()])
    assert len(made) == 1 and (made[0].scope_kind, made[0].scope_id) == ("schedule", SCHEDULE)
    sid2 = _fire(client, rt)
    assert ran == [GIT], f"the covered call never reached execute: {ran}"
    assert not _events(rt, "approval.requested", sid2), "a covered call must not card"
    rows = _ok_shell_rows(rt.platform.engine, sid2)
    assert len(rows) == 1 and rows[0].grant_id == made[0].id

    # It is VISIBLE: the grants list, filtered by the new kind, and the schedule's row.
    listed = client.get(f"/grants?scope_kind=schedule&scope_id={SCHEDULE}").json()["grants"]
    assert [g["id"] for g in listed] == [made[0].id]
    row = next(s for s in client.get("/schedules").json()["schedules"] if s["name"] == SCHEDULE)
    assert [g["id"] for g in row["standing_grants"]] == [made[0].id]
    assert row["standing_grants"][0]["label"] == 'shell {"command": "git status"}'

    # 3. Taken back from chat by the schedule's NAME: the next fire asks again.
    from iron_jarvis.settings.records import GrantRevokeTool

    res = asyncio.run(
        GrantRevokeTool(rt.platform).execute(
            {"grant": SCHEDULE},
            ToolContext(workspace=rt.platform.config.home, session_id="chat", agent_run_id="r",
                        config=rt.platform.config, event_bus=rt.platform.event_bus,
                        engine=rt.platform.engine),
        )
    )
    assert res.ok, res.error
    assert "schedule rent-list" in str(res.data)
    row = next(s for s in client.get("/schedules").json()["schedules"] if s["name"] == SCHEDULE)
    assert row["standing_grants"] == []
    sid3 = _fire(client, rt)
    assert _events(rt, "approval.requested", sid3), "after Remove the schedule asks again"
    assert ran == [GIT], "and the call does not run unasked"


def test_a_schedule_that_runs_a_custom_agent_credits_it_and_its_own_grants_apply(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    ran = _stub_shell(rt.platform)
    rt.platform.agents_registry.register("remy", "You keep the books.", ["shell"], provider="mock", model="mock-1")
    client = TestClient(rt.app)
    r = client.post(
        "/schedules",
        json={"name": SCHEDULE, "cron": "0 8 * * 1", "kind": "task",
              "payload": {"task": "build the rent list", "agent_type": "remy"}},
    )
    assert r.status_code == 200, r.text
    # An OK the agent already holds (minted on another of its runs) covers the fire.
    g = rt.store.create("agent", "custom:remy", "shell", args_hash("shell", GIT), "l")
    sid = _fire(client, rt)
    with session_scope(rt.platform.engine) as db:
        assert db.get(SessionRow, sid).agent_name == "custom:remy"
    assert ran == [GIT], "the custom agent's own standing OK must apply to its scheduled run"
    assert _ok_shell_rows(rt.platform.engine, sid)[0].grant_id == g.id


def test_a_builtin_schedule_is_credited_to_no_agent(rt):
    """Only a CUSTOM agent's name rides the fire: a builtin's name as a scope
    would make one "Always" cover every builder run anywhere."""
    client = TestClient(rt.app)
    client.post("/schedules", json={"name": "plain", "cron": "0 8 * * 1", "kind": "task",
                                    "payload": {"task": "say hello", "agent_type": "builder"}})
    fired = client.post("/schedules/plain/run").json()
    with session_scope(rt.platform.engine) as db:
        assert (db.get(SessionRow, fired["last_session_id"]).agent_name or "") == ""


# --------------------------------------------------------------------------- #
# 4. the workflow's row
# --------------------------------------------------------------------------- #


def test_a_saved_workflow_row_lists_its_standing_ok(rt):
    client = TestClient(rt.app)
    r = client.post("/workflows", json={"name": "Monthly close", "steps": [{"name": "Draft", "task": "x"}]})
    assert r.status_code in (200, 201), r.text
    g = rt.store.create("workflow", "Monthly close", "shell", args_hash("shell", GIT), "shell git status")
    rt.store.create("workflow", "another", "shell", args_hash("shell", RM), "other")
    rows = client.get("/workflows").json()["workflows"]
    row = next(w for w in rows if w["name"] == "Monthly close")
    assert [x["id"] for x in row["standing_grants"]] == [g.id]
    rt.store.revoke(g.id)
    row = next(w for w in client.get("/workflows").json()["workflows"] if w["name"] == "Monthly close")
    assert row["standing_grants"] == []


# --------------------------------------------------------------------------- #
# 5. the bell can offer it
# --------------------------------------------------------------------------- #


async def test_the_pending_listing_carries_the_always_offer_and_its_scope(rt):
    """The bell lists a run's ask from ``GET /chat/approvals/pending``; that
    listing dropped ``can_always``, so no run's ask could be answered "Always"
    from the bell, where an unattended schedule's ask is answered."""
    from tests.test_chat_approvals_v1187 import _asgi_post  # noqa: F401 (the scope helper's module)
    import httpx

    agent_def = get_agent_definition(AgentType.BUILDER)
    pause = asyncio.create_task(
        rt.runtime._pause_for_approval(_session(sid="session_p"), _tc(), agent_def, set())
    )
    for _ in range(400):
        if _events(rt, "approval.requested", "session_p"):
            break
        await asyncio.sleep(0.01)
    # The listing reads the event's ROW (the bus writes it); let the write land.
    transport = httpx.ASGITransport(app=rt.app, client=("127.0.0.1", 51234))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as client:
        listed = []
        for _ in range(200):
            listed = [a for a in (await client.get("/chat/approvals/pending")).json()["approvals"]
                      if a.get("session_id") == "session_p"]
            if listed:
                break
            await asyncio.sleep(0.01)
    assert listed, "the ask was not listed"
    assert listed[0]["can_always"] is True
    assert listed[0]["always_scope"] == "schedule"
    req = _events(rt, "approval.requested", "session_p")[-1]["payload"]
    rt.platform.approvals.resolve(req["approval_id"], "deny")
    await pause


async def test_the_runs_own_page_is_told_it_may_answer_always_and_where(rt):
    """Shipping the feature, not the mechanism: a schedule's ask is answered
    on the run's page (its waiting row) and the bell. The page read
    ``can_always`` and ``args_hash`` off a row nothing ever put them on, so it
    never offered "Always" at all, for any run."""
    from iron_jarvis.daemon.routes.sessions import _waiting_on

    agent_def = get_agent_definition(AgentType.BUILDER)
    pause = asyncio.create_task(
        rt.runtime._pause_for_approval(_session(sid="session_w"), _tc(), agent_def, set())
    )
    for _ in range(400):
        if _events(rt, "approval.requested", "session_w"):
            break
        await asyncio.sleep(0.01)
    req = _events(rt, "approval.requested", "session_w")[-1]["payload"]
    row = _waiting_on(rt.app.state.d, "session_w")
    try:
        assert row is not None and row["approval_id"] == req["approval_id"]
        assert row["can_always"] is True
        assert row["always_scope"] == "schedule"
        assert row["args_hash"] == req["args_hash"] and row["args_hash"]
        assert "args" not in row  # a flag, a word and a hash: never the arguments
    finally:
        rt.platform.approvals.resolve(req["approval_id"], "deny")
        await pause


async def test_a_goal_runs_page_offers_always_with_no_place_word(rt):
    from iron_jarvis.daemon.routes.sessions import _waiting_on

    agent_def = get_agent_definition(AgentType.BUILDER)
    session = _session(sid="session_g2", origin="goal:goal_1")
    pause = asyncio.create_task(rt.runtime._pause_for_approval(session, _tc(), agent_def, set()))
    for _ in range(400):
        if _events(rt, "approval.requested", "session_g2"):
            break
        await asyncio.sleep(0.01)
    req = _events(rt, "approval.requested", "session_g2")[-1]["payload"]
    row = _waiting_on(rt.app.state.d, "session_g2")
    try:
        assert row is not None
        # The row says what the ask's own event said, no more.
        assert row.get("can_always", False) is bool(req["can_always"])
        assert "always_scope" not in row
    finally:
        rt.platform.approvals.resolve(req["approval_id"], "deny")
        await pause


# --------------------------------------------------------------------------- #
# 6. a removed schedule or workflow takes its standing OKs with it
# --------------------------------------------------------------------------- #
def test_a_removed_schedule_drops_its_standing_ok_and_a_new_one_of_that_name_has_none(rt):
    """Without this, a schedule made later under the same name would start
    with the old one's OKs, unasked."""
    client = TestClient(rt.app)
    body = {"name": SCHEDULE, "cron": "0 8 * * 1", "kind": "task", "payload": {"task": "build the rent list"}}
    assert client.post("/schedules", json=body).status_code == 200
    kept = rt.store.create("schedule", SCHEDULE, "shell", args_hash("shell", GIT), "shell git status")
    other = rt.store.create("schedule", "another", "shell", args_hash("shell", GIT), "other schedule")
    assert [g.id for g in rt.store.list("schedule", SCHEDULE)] == [kept.id]

    assert client.delete(f"/schedules/{SCHEDULE}").json()["removed"] is True

    assert rt.store.list("schedule", SCHEDULE) == []
    assert [g.id for g in rt.store.list("schedule", "another")] == [other.id], "another schedule's OK stays"
    assert client.post("/schedules", json=body).status_code == 200  # the same name, made again
    row = next(s for s in client.get("/schedules").json()["schedules"] if s["name"] == SCHEDULE)
    assert row["standing_grants"] == []


def test_a_removed_workflow_drops_its_standing_ok(rt):
    client = TestClient(rt.app)
    made = client.post("/workflows", json={"name": "Monthly close", "steps": [{"name": "Draft", "task": "x"}]})
    assert made.status_code in (200, 201), made.text
    rt.store.create("workflow", "Monthly close", "shell", args_hash("shell", GIT), "shell git status")
    assert rt.store.list("workflow", "Monthly close")

    assert client.delete("/workflows/Monthly close").status_code == 200

    assert rt.store.list("workflow", "Monthly close") == []
