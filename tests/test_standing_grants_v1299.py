"""v1.299.0 — ARGUMENT-SCOPED STANDING GRANTS (/goal wave 4b).

Every grant before this wave was keyed by NAME: "Allow for this conversation"
on ``shell`` allowed every shell command for the run; the goals trust ladder
could offer "always allow web_fetch", never "always allow THIS command". Now:

* ``core/grants.py`` — ``args_hash(tool, args)`` over the REAL arguments;
  ``StandingGrantRecord`` ``(scope_kind, scope_id, tool, args_hash)``;
  ``GrantStore`` create/dedupe/match/list/revoke, in-memory live view.
* ``tools/permissions.authorize(..., grants=, scopes=)`` lifts an ASK on a
  match — AFTER the deny check (a deny is never lifted), never any-args on a
  floor tool, never a quarantined pack tool; the registry records the grant
  id on the ``ToolInvocation`` row.
* The runtime and the stream chat lane skip the card for a covered call and
  pass the store + scopes into the REAL registry gate (not a copied name);
  the "always" decision mints an exact grant in the strongest scope.
* The goals ladder groups asks by ``args_hash`` (the runtime writes it into
  ``approval.requested``) and offers an EXACT grant when the streak shares
  one hash; ``PATCH /goals/{id}/grants`` lands store rows; ``/grants`` lists
  and revokes.

Driven through the REAL ``registry.invoke`` / ``PermissionEngine`` with only
a tool's ``execute`` stubbed (the v1.270.1 lesson: a covered call must CARRY
its grant into the registry, and a stubbed invoke cannot see that it did not).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.approvals import DECISIONS
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.grants import (
    GrantStore,
    StandingGrantRecord,
    args_hash,
    grant_label,
    pick_scope,
)
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import AgentType, EventRecord, ToolInvocation
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.core.trust import LOW_TRUST_DENY, low_trust_overrides
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import goals as goals_routes
from iron_jarvis.daemon.routes import grants as grants_routes
from iron_jarvis.tools.base import ToolContext, ToolResult
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS, PermissionEngine
from tests.test_chat_approvals_v1187 import (
    _ASK_MSG,
    _asgi_post,
    _drive_stream,
    _shell_call_stream,
)
from tests.test_goals_routes_v1208 import TOKENS_BUDGET

SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"

GIT = {"command": "git status"}
RM = {"command": "rm -rf build"}


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _attach_store(platform) -> GrantStore:
    """The platform fixture has no store yet (platform.py is another doer's);
    attach the one build_platform will — field ``grants``."""
    store = GrantStore(platform.engine, platform.event_bus)
    platform.grants = store
    return store


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    store = _attach_store(platform)
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": type, "payload": payload or {}, "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(
        runtime=AgentRuntime(platform), platform=platform, store=store,
        published=published, app=app,
    )


def _session(sid="session_test", origin="goal:g1", approval_mode="", agent_name="", project_id=None):
    return SimpleNamespace(
        id=sid, origin=origin, approval_mode=approval_mode,
        agent_name=agent_name, project_id=project_id, trust="full",
    )


def _tc(args=None, name="shell"):
    return SimpleNamespace(name=name, arguments=dict(args or GIT))


def _events(rt, type_, session_id=None):
    return [
        p for p in rt.published
        if p["type"] == type_ and (session_id is None or p["session_id"] == session_id)
    ]


async def _wait_for(pred, *, tries=400, sleep=0.01) -> bool:
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(sleep)
    return False


async def _answer(rt, decision: str, *, session_id: str | None = None):
    ok = await _wait_for(lambda: bool(_events(rt, "approval.requested", session_id)))
    assert ok, "the pause never published its request"
    req = _events(rt, "approval.requested", session_id)[-1]
    assert rt.platform.approvals.resolve(req["payload"]["approval_id"], decision)
    return req["payload"]


def _stub_execute(platform, name: str) -> list[dict]:
    """Stub ONE tool's `execute`; everything before it (the gate, the ledger) is real."""
    tool = platform.registry.get(name)
    assert tool is not None, f"no tool {name}"
    calls: list[dict] = []

    async def fake_execute(args, ctx=None, *a, **kw):
        calls.append(dict(args))
        return ToolResult(ok=True, output="ran")

    tool.execute = fake_execute  # type: ignore[method-assign]
    return calls


def _ctx(platform, tmp_path, session_id="s1"):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, session_id=session_id, agent_run_id="r1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


def _rows(engine, session_id: str) -> list[ToolInvocation]:
    from sqlmodel import select

    with session_scope(engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == session_id)).all())
        for r in rows:
            db.expunge(r)
        return rows


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


# --------------------------------------------------------------------------- #
# 1. the hash
# --------------------------------------------------------------------------- #


def test_args_hash_is_deterministic_order_independent_and_argument_sensitive():
    a = args_hash("shell", {"command": "git status", "cwd": "."})
    b = args_hash("shell", {"cwd": ".", "command": "git status"})
    assert a == b and len(a) == 64 and int(a, 16) >= 0
    assert args_hash("shell", RM) != a, "different arguments must hash differently"
    assert args_hash("repl", GIT) != args_hash("shell", GIT), "the tool is part of the identity"
    # The app's own control keys are not the call's identity: the lane adds
    # `_isolate` under low trust and strips `_store_as` before the gate.
    assert args_hash("shell", {**GIT, "_isolate": True}) == args_hash("shell", GIT)
    assert args_hash("shell", None) == args_hash("shell", {})
    assert grant_label("shell", GIT) == 'shell {"command": "git status"}'
    assert len(grant_label("shell", {"x": "y" * 500})) <= 200
    assert "always" in DECISIONS
    assert pick_scope([("chat", "chat"), ("project", "p1")]) == ("project", "p1")
    assert pick_scope([("goal", "g"), ("project", "p"), ("agent", "a")]) == ("agent", "a")
    assert pick_scope([("bogus", "x"), ("goal", "")]) is None


# --------------------------------------------------------------------------- #
# 2. the store
# --------------------------------------------------------------------------- #


def test_store_create_dedupe_match_scope_expiry_revoke(platform):
    store = _attach_store(platform)
    h = args_hash("shell", GIT)
    rec = store.create("goal", "g1", "shell", h, grant_label("shell", GIT))
    assert rec.id.startswith("grant_") and rec.expires_at is not None
    assert (rec.expires_at - rec.created_at) >= timedelta(days=29)
    # dedupe: an identical live grant is returned, not duplicated
    assert store.create("goal", "g1", "shell", h, "again").id == rec.id
    assert len(store.list("goal", "g1")) == 1
    # exact match, argument mismatch, scope mismatch
    assert store.match([("goal", "g1")], "shell", GIT).id == rec.id
    assert store.match([("goal", "g1")], "shell", RM) is None
    assert store.match([("goal", "g2")], "shell", GIT) is None
    assert store.match([("project", "g1")], "shell", GIT) is None
    assert store.match([], "shell", GIT) is None
    # any-args grant (the ladder's shape) covers every argument set
    anyg = store.create("goal", "g1", "web_fetch", "", "web_fetch (any arguments)")
    assert store.match([("goal", "g1")], "web_fetch", {"url": "a"}).id == anyg.id
    assert store.match([("goal", "g1")], "web_fetch", {"url": "b"}).id == anyg.id
    # a floor tool may never be granted any-args
    for tool in sorted(DENY_FLOOR_TOOLS)[:3]:
        with pytest.raises(ValueError):
            store.create("goal", "g1", tool, "", "x")
    # "never" is for goals only
    with pytest.raises(ValueError):
        store.create("chat", "chat", "web_fetch", "", "x", expires_days=None)
    forever = store.create("goal", "g1", "list_folder", "", "x", expires_days=None)
    assert forever.expires_at is None and forever.is_live()
    with pytest.raises(ValueError):
        store.create("nope", "x", "shell", h, "x")
    # revoke: immediate, idempotent, unknown -> None
    gone = store.revoke(rec.id)
    assert gone.revoked_at is not None
    assert store.match([("goal", "g1")], "shell", GIT) is None
    assert store.revoke(rec.id).id == rec.id
    assert store.revoke("grant_nope") is None
    assert [g.id for g in store.list("goal", "g1")] == sorted([anyg.id, forever.id], key=lambda i: [g.id for g in store.list("goal", "g1")].index(i))
    assert rec.id in {g.id for g in store.list("goal", "g1", live_only=False)}
    # expiry: a row past its date is not live, even in the in-memory view
    with session_scope(platform.engine) as db:
        row = db.get(StandingGrantRecord, anyg.id)
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()
    store.refresh()
    assert store.match([("goal", "g1")], "web_fetch", {"url": "a"}) is None
    assert anyg.id not in {g.id for g in store.list("goal", "g1")}


def test_a_match_counts_a_use_off_the_loop(platform):
    store = _attach_store(platform)
    rec = store.create("chat", "chat", "shell", args_hash("shell", GIT), "l")

    async def go():
        assert store.match([("chat", "chat")], "shell", GIT) is not None
        assert store.match([("chat", "chat")], "shell", GIT, touch=False) is not None
        await store.flush()

    asyncio.run(go())
    with session_scope(platform.engine) as db:
        row = db.get(StandingGrantRecord, rec.id)
        assert row.uses == 1 and row.last_used_at is not None


def test_a_floor_row_written_by_hand_as_any_args_never_matches(platform):
    """Belt and braces: `create` refuses it, and `match` ignores it too."""
    store = _attach_store(platform)
    with session_scope(platform.engine) as db:
        db.add(StandingGrantRecord(scope_kind="goal", scope_id="g1", tool="shell", args_hash="", label="x"))
        db.commit()
    store.refresh()
    assert store.match([("goal", "g1")], "shell", GIT) is None
    assert store.match([("goal", "g1")], "shell", RM) is None


# --------------------------------------------------------------------------- #
# 3. the engine
# --------------------------------------------------------------------------- #


def test_authorize_lifts_an_exact_ask_only_and_never_a_deny(platform):
    store = _attach_store(platform)
    eng = PermissionEngine({"shell": "ask", "web_fetch": "deny", "ltm_append": "ask"})
    scopes = [("goal", "g1")]
    g = store.create("goal", "g1", "shell", args_hash("shell", GIT), "l")
    ok = eng.authorize("shell", GIT, None, None, grants=store, scopes=scopes)
    assert ok.allowed is True and ok.grant_id == g.id and g.id in ok.reason
    miss = eng.authorize("shell", RM, None, None, grants=store, scopes=scopes)
    assert miss.allowed is False and miss.grant_id == ""
    assert eng.authorize("shell", GIT, None, None, grants=store, scopes=[("goal", "g2")]).allowed is False
    assert eng.authorize("shell", GIT, None, None).allowed is False, "no store, no lift"
    # a deny is NEVER lifted, exact grant or not
    store.create("goal", "g1", "web_fetch", args_hash("web_fetch", {"url": "x"}), "l")
    denied = eng.authorize("web_fetch", {"url": "x"}, None, None, grants=store, scopes=scopes)
    assert denied.allowed is False and "denied by policy" in denied.reason
    # low trust: the kept-away tool is a deny through the overrides; the grant is moot
    assert "ltm_append" in LOW_TRUST_DENY
    store.create("goal", "g1", "ltm_append", args_hash("ltm_append", {"text": "t"}), "l")
    low = eng.authorize(
        "ltm_append", {"text": "t"}, low_trust_overrides({}), None, grants=store, scopes=scopes
    )
    assert low.allowed is False and "denied" in low.reason
    full = eng.authorize("ltm_append", {"text": "t"}, {}, None, grants=store, scopes=scopes)
    assert full.allowed is True, "the same grant lifts the same call under FULL trust (control)"
    # quarantine: neither the name grant nor the standing grant lifts
    q = eng.authorize("shell", GIT, None, {"shell"}, grants=store, scopes=scopes, quarantined=True)
    assert q.allowed is False and "approval" in q.reason
    assert eng.authorize("shell", GIT, None, {"shell"}).allowed is True, "the name grant works when not quarantined (control)"


@pytest.mark.asyncio
async def test_the_real_registry_runs_a_covered_call_and_records_the_grant_id(rt, tmp_path):
    ran = _stub_execute(rt.platform, "shell")
    g = rt.store.create("goal", "g1", "shell", args_hash("shell", GIT), grant_label("shell", GIT))
    ctx = _ctx(rt.platform, tmp_path, "s_cov")
    # control: the same call with NO store passed is the headless refusal
    res = await rt.platform.registry.invoke("shell", dict(GIT), ctx, rt.platform.permissions)
    assert res.ok is False and ran == [], res.error
    res = await rt.platform.registry.invoke(
        "shell", dict(GIT), ctx, rt.platform.permissions,
        grants=rt.store, scopes=[("goal", "g1")],
    )
    assert res.ok is True, res.error
    assert ran == [GIT]
    rows = [r for r in _rows(rt.platform.engine, "s_cov") if r.ok]
    assert len(rows) == 1 and rows[0].grant_id == g.id and rows[0].verdict.value == "ask"
    executed = [e for e in _events(rt, EventType.TOOL_EXECUTED, "s_cov") if e["payload"].get("ok")]
    assert executed and executed[-1]["payload"].get("grant_id") == g.id
    # a different command through the same store is still refused
    res = await rt.platform.registry.invoke(
        "shell", dict(RM), ctx, rt.platform.permissions,
        grants=rt.store, scopes=[("goal", "g1")],
    )
    assert res.ok is False and ran == [GIT]
    # shell under a hand-written any-args row: still refused through the real gate
    with session_scope(rt.platform.engine) as db:
        db.add(StandingGrantRecord(scope_kind="goal", scope_id="g1", tool="shell", args_hash="", label="x"))
        db.commit()
    rt.store.refresh()
    res = await rt.platform.registry.invoke(
        "shell", dict(RM), ctx, rt.platform.permissions,
        grants=rt.store, scopes=[("goal", "g1")],
    )
    assert res.ok is False and ran == [GIT]


# --------------------------------------------------------------------------- #
# 4. the runtime lane
# --------------------------------------------------------------------------- #


def test_runtime_scopes_come_from_the_row():
    s = AgentRuntime.grant_scopes
    assert s(_session(origin="goal:g1")) == [("goal", "g1")]
    assert s(_session(origin="chat", agent_name="custom:tax", project_id="p1")) == [
        ("agent", "custom:tax"), ("project", "p1"),
    ]
    assert s(_session(origin="chat")) == []
    assert s(_session(origin="goal:")) == []


@pytest.mark.asyncio
async def test_a_goal_scoped_exact_grant_skips_the_card_and_the_run_still_goes_through_the_gate(rt, monkeypatch):
    agent_def = get_agent_definition(AgentType.BUILDER)
    sess = _session(origin="goal:g1")
    # anti-vacuity: without the grant the pause is needed
    assert rt.runtime._pause_needed(sess, _tc(), agent_def, set()) == "shell"
    rt.store.create("goal", "g1", "shell", args_hash("shell", GIT), "l")
    assert rt.runtime._pause_needed(sess, _tc(), agent_def, set()) == ""
    assert rt.runtime._pause_needed(sess, _tc(RM), agent_def, set()) == "shell", "a different command still asks"
    assert rt.runtime._pause_needed(_session(origin="goal:g2"), _tc(), agent_def, set()) == "shell", "another goal still asks"
    deny, extra = await rt.runtime._pause_for_approval(sess, _tc(), agent_def, set())
    assert (deny, extra) == ("", set()) and not _events(rt, "approval.requested")
    # the kwargs the run hands the registry carry the SAME store + scopes
    kw = rt.runtime._grant_invoke_kwargs(sess)
    assert kw == {"grants": rt.store, "scopes": [("goal", "g1")]}
    assert rt.runtime._grant_invoke_kwargs(_session(origin="chat")) == {}


def test_a_whole_run_under_a_goal_grant_runs_the_tool_with_no_card(rt, monkeypatch):
    """End to end through POST /sessions: the model asks for `git status`, the
    goal holds an exact grant, the tool RUNS (execute stubbed), no card, and
    the ledger row names the grant. Then the control: revoke it, run again —
    the card comes and execute is never reached."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(runtime_mod, "ATTENDED_APPROVAL_TIMEOUT_S", 0.2)
    ran = _stub_execute(rt.platform, "shell")
    g = rt.store.create("goal", "g1", "shell", args_hash("shell", GIT), "l")
    client = TestClient(rt.app)
    rt.platform.router.stream = _shell_then_done()
    r = client.post("/sessions", json={"task": "check the repo", "wait": True, "origin": "goal:g1"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert ran == [GIT], f"the covered call never reached execute: {ran}"
    assert not _events(rt, "approval.requested", sid), "a covered call must not card"
    ok_rows = [row for row in _rows(rt.platform.engine, sid) if row.ok and row.tool == "shell"]
    assert len(ok_rows) == 1 and ok_rows[0].grant_id == g.id
    # control
    rt.store.revoke(g.id)
    rt.platform.router.stream = _shell_then_done()
    r = client.post("/sessions", json={"task": "check the repo", "wait": True, "origin": "goal:g1"})
    sid2 = r.json()["id"]
    assert _events(rt, "approval.requested", sid2), "without the grant the card must come"
    assert ran == [GIT], "the unanswered call must not run"


@pytest.mark.asyncio
async def test_always_in_the_runtime_lane_mints_an_exact_grant_a_new_session_reads(rt):
    agent_def = get_agent_definition(AgentType.BUILDER)
    first = _session(sid="session_a", origin="goal:g1")
    answerer = asyncio.create_task(_answer(rt, "always", session_id="session_a"))
    deny, extra = await rt.runtime._pause_for_approval(first, _tc(), agent_def, set())
    payload = await answerer
    assert deny == "" and extra == {"shell"}, "always is once for THIS call"
    assert payload["args_hash"] == args_hash("shell", GIT)
    assert payload["can_always"] is True
    await rt.store.flush()
    live = rt.store.list("goal", "g1")
    assert [(g.tool, g.args_hash, g.label) for g in live] == [
        ("shell", args_hash("shell", GIT), 'shell {"command": "git status"}')
    ]
    assert live[0].expires_at is not None
    created = _events(rt, EventType.GRANT_CREATED)
    assert created and created[-1]["payload"]["id"] == live[0].id
    # a NEW session of the same goal: the identical call needs no card
    second = _session(sid="session_b", origin="goal:g1")
    assert rt.runtime._pause_needed(second, _tc(), agent_def, set()) == ""
    assert rt.runtime._pause_needed(second, _tc(RM), agent_def, set()) == "shell"
    resolved = _events(rt, "approval.resolved", "session_a")
    assert resolved and resolved[-1]["payload"]["decision"] == "always"


@pytest.mark.asyncio
async def test_always_picks_the_strongest_scope_and_degrades_to_once_without_one(rt):
    agent_def = get_agent_definition(AgentType.BUILDER)
    sess = _session(sid="session_c", origin="goal:g1", agent_name="custom:tax", project_id="p1")
    answerer = asyncio.create_task(_answer(rt, "always", session_id="session_c"))
    deny, extra = await rt.runtime._pause_for_approval(sess, _tc(), agent_def, set())
    await answerer
    assert deny == "" and extra == {"shell"}
    await rt.store.flush()
    assert [(g.scope_kind, g.scope_id) for g in rt.store.list(live_only=True)] == [("agent", "custom:tax")]
    # no scope at all (origin "chat", no agent, no project): the card says so and "always" is "once"
    bare = _session(sid="session_d", origin="chat")
    answerer = asyncio.create_task(_answer(rt, "always", session_id="session_d"))
    deny, extra = await rt.runtime._pause_for_approval(bare, _tc(), agent_def, set())
    payload = await answerer
    assert deny == "" and extra == {"shell"} and payload["can_always"] is False
    await rt.store.flush()
    assert len(rt.store.list(live_only=True)) == 1, "nothing minted without a scope"


@pytest.mark.asyncio
async def test_a_low_trust_run_is_refused_the_kept_away_tool_despite_a_grant(rt, tmp_path):
    """The deny from low_trust_overrides is a deny; the grant is never read."""
    agent_def = get_agent_definition(AgentType.BUILDER)
    low = SimpleNamespace(id="session_low", origin="goal:g1", approval_mode="",
                          agent_name="", project_id=None, trust="low")
    tool = rt.platform.registry.get("ltm_append")
    assert tool is not None
    note = {"title": "t", "content": "remember"}
    rt.store.create("goal", "g1", "ltm_append", args_hash("ltm_append", note), "l")
    assert rt.runtime._pause_needed(low, _tc(note, name="ltm_append"), agent_def, set()) == ""
    assert rt.runtime._low_trust_refusal_for(low, "ltm_append"), "the refusal names the cause"
    ran = _stub_execute(rt.platform, "ltm_append")
    res = await rt.platform.registry.invoke(
        "ltm_append", dict(note), _ctx(rt.platform, tmp_path, "s_low"), rt.platform.permissions,
        rt.runtime._trust_overrides(low, agent_def),
        grants=rt.store, scopes=rt.runtime.grant_scopes(low),
    )
    assert res.ok is False and ran == [], res.error
    assert "denied" in (res.error or "")
    # control: the same call under FULL trust is lifted by the same grant
    full = _session(sid="session_full", origin="goal:g1")
    res = await rt.platform.registry.invoke(
        "ltm_append", dict(note), _ctx(rt.platform, tmp_path, "s_full"), rt.platform.permissions,
        rt.runtime._trust_overrides(full, agent_def),
        grants=rt.store, scopes=rt.runtime.grant_scopes(full),
    )
    assert res.ok is True and len(ran) == 1, res.error


# --------------------------------------------------------------------------- #
# 5. the chat lanes (lock-step)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_stream_lane_offers_always_mints_a_chat_grant_and_skips_the_next_card(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    store = _attach_store(platform)
    ran = _stub_execute(platform, "shell")
    platform.router.stream = _shell_call_stream()
    seen: list[dict] = []

    async def approve(data):
        seen.append(data)
        assert data["can_always"] is True
        assert data["args_hash"] == args_hash("shell", {"command": "echo approved-run"})
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "always"})
        assert status == 200

    body = {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}
    frames = await _drive_stream(app, body, approve)
    kinds = [ev for ev, _ in frames]
    assert kinds.count("approval") == 1 and len(seen) == 1
    resolved = next(d for ev, d in frames if ev == "approval_resolved")
    assert resolved["decision"] == "always"
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is True and ran == [{"command": "echo approved-run"}]
    await store.flush()
    live = store.list()
    assert [(g.scope_kind, g.scope_id, g.tool) for g in live] == [("chat", "chat", "shell")]
    assert live[0].args_hash == args_hash("shell", {"command": "echo approved-run"})
    # the next turn: the identical call is covered — no card, and it RUNS
    # through the real registry (the ledger row names the grant)
    platform.router.stream = _shell_call_stream()
    frames = await _drive_stream(app, body, approve)
    kinds = [ev for ev, _ in frames]
    assert "approval" not in kinds, "a covered call must not card"
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is True and len(ran) == 2
    chat_rows = [r for r in _rows(platform.engine, "chat") if r.ok and r.tool == "shell"]
    assert chat_rows and chat_rows[-1].grant_id == live[0].id
    # a different command still cards (the control for the next card)
    platform.router.stream = _shell_call_stream("echo other")
    cards: list[dict] = []

    async def deny(data):
        cards.append(data)
        await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})

    frames = await _drive_stream(app, {"messages": [{"role": "user", "content": "run the command `echo other` in the terminal for me"}], "auto_tools": True}, deny)
    assert len(cards) == 1 and len(ran) == 2


@pytest.mark.asyncio
async def test_the_stream_lane_without_a_store_reads_always_as_once(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    # platform.py attaches a store since the wiring landed; this case is the
    # NO-store contract (an older daemon / a bare platform): detach it.
    platform.grants = None
    ran = _stub_execute(platform, "shell")
    platform.router.stream = _shell_call_stream()

    async def approve(data):
        assert data["can_always"] is False
        await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "always"})

    frames = await _drive_stream(app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, approve)
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is True and len(ran) == 1


def test_both_chat_lanes_carry_the_store_into_the_registry_lock_step():
    """The POST lane never cards (every armed tool is its own grant), so it has
    no 'always' to answer; it carries the same `grants=/scopes=` kwargs so a
    lifted call's ledger row names its grant. The stream lane cards, offers
    `can_always`, mints on 'always', and skips a covered call."""
    turn = (SRC / "daemon" / "chat_turn.py").read_text(encoding="utf-8")
    stream = (SRC / "daemon" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "approvals.request(" not in turn.replace("_approvals(d).request(", ""), "the POST lane has no card"
    assert "_grant_invoke_kwargs(" in turn and "chat_grant_scopes(" in turn
    assert stream.count("_grant_invoke_kwargs(d.platform, _grant_scopes)") == 1
    assert '"can_always": _can_always(' in stream
    assert 'if _decision == "always":' in stream
    # the covered-call predicate runs in BOTH places the stream lane decides a
    # card: the batch grouping (`_would_card`) and the per-call branch
    assert stream.count(" _grant_covers(") == 2  # not `tab_grant_covers(`
    runtime = (SRC / "agents" / "runtime.py").read_text(encoding="utf-8")
    assert 'if decision == "always":' in runtime and "self._mint_always" in runtime
    assert "**self._grant_invoke_kwargs(session)," in runtime


# --------------------------------------------------------------------------- #
# 6. the goals ladder
# --------------------------------------------------------------------------- #


@pytest.fixture
def goal_client(platform, orchestrator):
    platform.goal_engine._orch = orchestrator
    store = _attach_store(platform)
    app = FastAPI()
    d = SimpleNamespace(platform=platform)
    goals_routes.register(app, d)
    grants_routes.register(app, d)
    return SimpleNamespace(client=TestClient(app), platform=platform, store=store)


def _goal(gc) -> str:
    r = gc.client.post("/goals", json={
        "name": "inbox zero", "contract_text": "Create a file summarizing the task.",
        "budget": TOKENS_BUDGET,
    })
    assert r.status_code == 200, r.text
    return r.json()["goal"]["id"]


def _seed_session(platform, origin: str) -> str:
    with session_scope(platform.engine) as db:
        row = SessionRow(task="goal work", origin=origin)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _seed_ask(platform, session_id: str, tool: str, args: dict | None, decision: str, *, with_hash=True) -> None:
    """ONE ask the runtime's shape: a request carrying the REDACTED args and
    (v1.299.0) the args_hash, and a resolution joined by approval_id."""
    aid = new_id("apr")
    req = {"approval_id": aid, "tool": tool, "args": {"shown": "SECRET-ARG-NEVER-SERVED"}, "timeout_s": 0}
    if with_hash:
        req["args_hash"] = args_hash(tool, args)
    with session_scope(platform.engine) as db:
        db.add(EventRecord(id=new_id("evt"), type="approval.requested", session_id=session_id,
                           payload_json=json.dumps(req)))
        db.add(EventRecord(id=new_id("evt"), type="approval.resolved", session_id=session_id,
                           payload_json=json.dumps({"approval_id": aid, "tool": tool, "decision": decision})))
        db.commit()


def test_the_ladder_offers_exact_when_the_streak_shares_one_hash_else_per_tool(goal_client):
    gc = goal_client
    gid = _goal(gc)
    sid = _seed_session(gc.platform, f"goal:{gid}")
    # web_fetch: three approvals, three DIFFERENT urls -> per-tool offer (today's rule)
    for i in range(3):
        _seed_ask(gc.platform, sid, "web_fetch", {"url": f"https://x/{i}"}, "once")
    # shell: three approvals of the SAME command -> an exact offer (a floor
    # tool is never offered per-tool; the exact command is not any-args)
    for _ in range(3):
        _seed_ask(gc.platform, sid, "shell", GIT, "always" if _ == 0 else "once")
    # list_folder: same hash but one deny -> nothing
    for d in ("once", "once", "deny"):
        _seed_ask(gc.platform, sid, "list_folder", {"path": "."}, d)
    # read_file: pre-v1.299 rows without a hash -> per-tool offer only
    for _ in range(3):
        _seed_ask(gc.platform, sid, "read_file", {"path": "a"}, "once", with_hash=False)
    view = gc.client.get(f"/goals/{gid}").json()["goal"]
    assert view["grant_offers"] == ["read_file", "web_fetch"]
    assert view["grant_offers_exact"] == [
        {"tool": "shell", "args_hash": args_hash("shell", GIT), "label": 'shell {"shown": "SECRET-ARG-NEVER-SERVED"}', "count": 3}
    ]
    assert view["ask_stats"]["shell"] == {"asked": 3, "approved": 3, "denied": 0, "timed_out": 0}
    assert view["standing_grants"] == []
    # the hash stats themselves never ride the view
    assert "by_hash" not in json.dumps(view)
    # same command x3 for a non-floor tool: exact, and NOT per-tool
    sid2 = _seed_session(gc.platform, f"goal:{gid}")
    for _ in range(3):
        _seed_ask(gc.platform, sid2, "web_search", {"query": "q"}, "once")
    view = gc.client.get(f"/goals/{gid}").json()["goal"]
    assert "web_search" not in view["grant_offers"]
    assert [o["tool"] for o in view["grant_offers_exact"]] == ["shell", "web_search"]
    # accepting the exact offer suppresses it; accepting per-tool suppresses that
    h = args_hash("shell", GIT)
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add_exact": [{"tool": "shell", "args_hash": h, "label": "shell git status"}]})
    assert r.status_code == 200, r.text
    view = r.json()["goal"]
    assert [o["tool"] for o in view["grant_offers_exact"]] == ["web_search"]
    assert [(g["tool"], g["args_hash"], g["exact"]) for g in view["standing_grants"]] == [("shell", h, True)]
    assert r.json()["granted"][0]["label"] == "shell git status"
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add": ["web_fetch"]})
    assert r.status_code == 200, r.text
    view = r.json()["goal"]
    assert view["grant_offers"] == ["read_file"]
    assert "web_fetch" in view["allowed_grants"], "the compat JSON still rides create_session"
    rows = gc.store.list("goal", gid)
    assert sorted((g.tool, g.args_hash) for g in rows) == [("shell", h), ("web_fetch", "")]
    assert all(g.expires_at is not None for g in rows)


def test_patch_refuses_the_floor_per_tool_and_lands_never_expiry_for_goals(goal_client):
    gc = goal_client
    gid = _goal(gc)
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add": ["shell"]})
    assert r.status_code == 400 and "deny floor" in r.json()["detail"]
    assert gc.store.list("goal", gid) == []
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add_exact": [{"tool": "repl", "args_hash": "zz"}]})
    assert r.status_code == 400 and "64 hex" in r.json()["detail"]
    # (review pin) an exact grant names a REGISTERED tool — a typo lands nothing
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add_exact": [{"tool": "shel", "args_hash": args_hash("shel", GIT)}]})
    assert r.status_code == 400 and "unknown tool" in r.json()["detail"]
    assert gc.store.list("goal", gid) == []
    r = gc.client.patch(f"/goals/{gid}/grants", json={})
    assert r.status_code == 400
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add": ["web_fetch"], "expires_days": None})
    assert r.status_code == 200, r.text
    (row,) = gc.store.list("goal", gid)
    assert row.expires_at is None and row.args_hash == ""
    r = gc.client.patch(f"/goals/{gid}/grants", json={"add": ["list_folder"], "expires_days": 0})
    assert r.status_code == 400
    assert gc.client.patch("/goals/goal_nope/grants", json={"add": ["web_fetch"]}).status_code == 404


def test_grants_routes_list_and_revoke(goal_client):
    gc = goal_client
    gid = _goal(gc)
    h = args_hash("shell", GIT)
    gc.client.patch(f"/goals/{gid}/grants", json={"add_exact": [{"tool": "shell", "args_hash": h}]})
    gc.store.create("chat", "chat", "web_fetch", "", "web_fetch (any arguments)")
    r = gc.client.get("/grants")
    assert r.status_code == 200 and len(r.json()["grants"]) == 2
    r = gc.client.get("/grants", params={"scope_kind": "goal", "scope_id": gid})
    (row,) = r.json()["grants"]
    assert row["tool"] == "shell" and row["exact"] is True and row["live"] is True
    assert gc.client.get("/grants", params={"scope_kind": "bogus"}).status_code == 400
    # the grant lifts through the real engine...
    eng = gc.platform.permissions
    assert eng.authorize("shell", GIT, None, None, grants=gc.store, scopes=[("goal", gid)]).allowed is True
    # ...until revoked — immediately, idempotently
    r = gc.client.post(f"/grants/{row['id']}/revoke")
    assert r.status_code == 200 and r.json()["grant"]["revoked_at"] and r.json()["grant"]["live"] is False
    assert eng.authorize("shell", GIT, None, None, grants=gc.store, scopes=[("goal", gid)]).allowed is False
    assert gc.client.post(f"/grants/{row['id']}/revoke").status_code == 200
    assert gc.client.post("/grants/grant_nope/revoke").status_code == 404
    assert len(gc.client.get("/grants").json()["grants"]) == 1
    assert len(gc.client.get("/grants", params={"live": "false"}).json()["grants"]) == 2
    # a daemon without a store says so
    bare = FastAPI()
    grants_routes.register(bare, SimpleNamespace(platform=SimpleNamespace()))
    assert TestClient(bare).get("/grants").status_code == 503


def test_grant_events_are_published(platform):
    store = _attach_store(platform)
    seen: list[tuple[str, dict]] = []

    async def go():
        async def spy(type, payload=None, session_id=None, **kw):
            seen.append((type, payload or {}))

        platform.event_bus.publish = spy
        rec = store.create("chat", "chat", "shell", args_hash("shell", GIT), "l")
        store.revoke(rec.id)
        store.revoke(rec.id)  # idempotent: no second event
        await store.flush()
        return rec

    rec = asyncio.run(go())
    assert [t for t, _ in seen] == [EventType.GRANT_CREATED, EventType.GRANT_REVOKED]
    assert all(p["id"] == rec.id and p["tool"] == "shell" and p["scope_kind"] == "chat" for _, p in seen)


# --------------------------------------------------------------------------- #
# 7. quarantine: no grant, name or standing, lifts a pack tool the user
#    never knowingly installed (the quarantine doer's seam, pinned through
#    the real invoke)
# --------------------------------------------------------------------------- #


def test_a_quarantined_pack_tool_is_lifted_by_no_grant(tmp_path):
    from tests.test_mcp_quarantine_v1299 import ECHO, LIST_ITEMS, WIPE, _body, _write_tools

    tools_file = tmp_path / "tools.json"
    _write_tools(tools_file, [ECHO])
    root = tmp_path / "root"
    root.mkdir()
    with TestClient(create_app(str(root))) as client:
        add = client.post("/mcp/servers", json=_body("mut", tools_file, auto_approve=False)).json()
        assert add["tools_loaded"] == 1
    _write_tools(tools_file, [ECHO, WIPE, LIST_ITEMS])
    with TestClient(create_app(str(root))) as client2:
        platform = client2.app.state.platform
        store = _attach_store(platform)
        wipe = platform.registry.get("mcp__mut__wipe")
        echo = platform.registry.get("mcp__mut__echo")
        assert wipe is not None and wipe.quarantined is True and echo.quarantined is False
        assert wipe.perm_key() == echo.perm_key() == "mcp_call"
        ctx = _ctx(platform, tmp_path, "s_q")

        def run(name, **kw):
            return asyncio.run(platform.registry.invoke(name, {"text": "hi"} if name.endswith("echo") else {}, ctx, platform.permissions, **kw))

        # control: with the pack NOT auto-approved, the trusted sibling needs
        # the per-run name grant — and gets it
        assert run("mcp__mut__echo").ok is False
        assert run("mcp__mut__echo", session_allow={"mcp_call"}).ok is True
        # the quarantined newcomer: the same name grant lifts nothing
        held = run("mcp__mut__wipe", session_allow={"mcp_call"})
        assert held.ok is False and "permission denied" in held.error, held.error
        # nor does a standing grant — exact, in a matching scope
        store.create("chat", "chat", "mcp__mut__wipe", args_hash("mcp__mut__wipe", {}), "l")
        store.create("chat", "chat", "mcp__mut__echo", args_hash("mcp__mut__echo", {"text": "hi"}), "l")
        assert run("mcp__mut__echo", grants=store, scopes=[("chat", "chat")]).ok is True, "the standing grant lifts the trusted sibling (control)"
        held = run("mcp__mut__wipe", session_allow={"mcp_call"}, grants=store, scopes=[("chat", "chat")])
        assert held.ok is False and "permission denied" in held.error, held.error


# --------------------------------------------------------------------------- #
# 8. review follow-ups: a quarantined tool is never offered "Always" (all
#    three seams say so and why), and a goal's grants die with the goal
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_runtime_card_never_offers_always_for_a_quarantined_tool(rt, monkeypatch):
    agent_def = get_agent_definition(AgentType.BUILDER)
    sess = _session(sid="session_q", origin="goal:g1")
    monkeypatch.setattr(rt.platform.registry.get("shell"), "quarantined", True, raising=False)
    answerer = asyncio.create_task(_answer(rt, "always", session_id="session_q"))
    deny, extra = await rt.runtime._pause_for_approval(sess, _tc(), agent_def, set())
    payload = await answerer
    assert payload["quarantined"] is True and payload["can_always"] is False
    assert deny == "" and extra == {"shell"}, "the answer still covers THIS call by name"
    await rt.store.flush()
    assert rt.store.list(live_only=True) == [], "nothing is minted for a quarantined tool"
    # control: the same tool, not quarantined, is offered
    monkeypatch.setattr(rt.platform.registry.get("shell"), "quarantined", False, raising=False)
    answerer = asyncio.create_task(_answer(rt, "deny", session_id="session_q2"))
    await rt.runtime._pause_for_approval(_session(sid="session_q2", origin="goal:g1"), _tc(), agent_def, set())
    payload = await answerer
    assert payload["quarantined"] is False and payload["can_always"] is True


def test_the_chat_lanes_shared_predicate_refuses_a_quarantined_tool(platform, monkeypatch):
    from iron_jarvis.daemon.chat_turn import _can_always, _quarantined, chat_grant_scopes

    _attach_store(platform)
    scopes = chat_grant_scopes(None)
    state = {"low": False, "tainted": False}
    assert _can_always(platform, scopes, state, "shell") is True
    monkeypatch.setattr(platform.registry.get("shell"), "quarantined", True, raising=False)
    assert _quarantined(platform, "shell") is True
    assert _can_always(platform, scopes, state, "shell") is False
    assert _quarantined(platform, "no_such_tool") is False


@pytest.mark.asyncio
async def test_the_stream_frame_says_quarantined_and_offers_no_always(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    store = _attach_store(platform)
    ran = _stub_execute(platform, "shell")
    monkeypatch.setattr(platform.registry.get("shell"), "quarantined", True, raising=False)
    platform.router.stream = _shell_call_stream()
    seen: list[dict] = []

    async def approve(data):
        seen.append(data)
        await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "always"})

    frames = await _drive_stream(app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, approve)
    assert len(seen) == 1 and seen[0]["quarantined"] is True and seen[0]["can_always"] is False
    resolved = next(d for ev, d in frames if ev == "approval_resolved")
    assert resolved["decision"] == "always"
    # (`shell` is an artificial stand-in: a REAL quarantined tool is an MCP
    # tool whose own name differs from the shared key, and test 7 pins that
    # its card's own answer runs it through the real gate.)
    await store.flush()
    assert store.list(live_only=True) == [], "an 'always' on a quarantined tool mints nothing"


def test_deleting_a_goal_revokes_its_standing_grants(goal_client):
    gc = goal_client
    gid, other = _goal(gc), _goal(gc)
    h = args_hash("shell", GIT)
    assert gc.client.patch(f"/goals/{gid}/grants", json={"add": ["web_fetch"], "add_exact": [{"tool": "shell", "args_hash": h}], "expires_days": None}).status_code == 200
    assert gc.client.patch(f"/goals/{other}/grants", json={"add": ["web_fetch"]}).status_code == 200
    mine = {g.id for g in gc.store.list("goal", gid)}
    assert len(mine) == 2 and all(g.expires_at is None for g in gc.store.list("goal", gid))
    r = gc.client.delete(f"/goals/{gid}")
    assert r.status_code == 200 and set(r.json()["revoked_grants"]) == mine
    assert gc.store.list("goal", gid) == []
    assert gc.store.match([("goal", gid)], "shell", GIT) is None
    assert len(gc.store.list("goal", other)) == 1, "another goal's grants are untouched"
    assert gc.client.delete(f"/goals/{gid}").status_code == 404
    assert "DELETE /goals/{id}" in (SRC / "core" / "grants.py").read_text(encoding="utf-8")
