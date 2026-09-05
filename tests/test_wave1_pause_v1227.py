"""v1.227.0 Wave 1, lane BE-1 — THE PAUSE (A2, A11, A4 runtime half).

Converted from ``tests/_audit_20260904/test_approvals_lane_audit.py`` (A1 →
sibling release, A3 → the run row says WAITING, A6 → the timeout is no
longer labelled "permission denied").

Live reframe that produced these: asks were PARALLEL batches of 4-5 (same
microsecond), each window 300 s, so 30 of a session's 40 minutes were spent
waiting; 'Allow for this conversation' on one card released nothing (the
siblings were already parked in their own ``wait_for``); the model re-batched
after every timeout because the deny text told it to "ask the user to re-run
or grant allow_tools" — impossible mid-run; and nothing but the bell badge
could tell a paused run from a running one.

* A2: ``ChatApprovals`` records the session per ask (``request(...,
  session_id=)``), lists them (``pending_for``) and answers a whole
  (session, tool-set) at once (``resolve_where``). A 'conversation' decision
  in ``_pause_for_approval`` releases every sibling ask of the same session
  + permission.
* A11: the timeout reason is honest — paused, unanswered, NOT run, do not
  retry — and rides the ledger as kind ``paused``, not "permission denied".
* A4: the AgentRun is WAITING for the length of the wait (``agent.
  state_changed {from, to}``, persisted) and RUNNING again on every exit:
  answer, timeout, deny, cancel.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlmodel import select

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import PAUSE_TIMEOUT_REASON, AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

BRIEF_SENTENCE = (
    "paused for the user and not answered in time — this call was NOT run. "
    "Do not retry it; continue with what does not need it, or finish with an "
    "honest summary of what is blocked."
)


# --------------------------------------------------------------------------- #
# fixture — the v1189 shape: real platform, spied event bus
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
        runtime=AgentRuntime(platform), platform=platform, published=published, app=app
    )


def _session(sid: str = "session_test", origin: str = "chat"):
    return SimpleNamespace(id=sid, origin=origin)


def _tc(cmd: str):
    return SimpleNamespace(name="shell", arguments={"command": cmd})


def _events(rt, type_: str, session_id: str | None = None) -> list[dict]:
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


def _shell_then_done():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c0", name="shell", arguments={"command": "mv a b"}),
            ])
        else:
            resp = LLMResponse(text="Task incomplete — 0 of 1 renamed.")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _run_state(engine, session_id: str) -> str:
    with session_scope(engine) as db:
        run = db.exec(select(AgentRun).where(AgentRun.session_id == session_id)).first()
        if run is None:  # polled before the run row exists
            return ""
        return getattr(run.state, "value", str(run.state))


# --------------------------------------------------------------------------- #
# A2 — the registry: session-scoped asks
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_registry_records_the_session_and_lists_its_asks():
    ap = ChatApprovals()
    a, fa = ap.request("shell", {"command": "mv a b"}, session_id="s1")
    b, fb = ap.request("shell", {"command": "mv c d"}, session_id="s1")
    c, fc = ap.request("write_file", None, session_id="s2")
    d, fd = ap.request("shell", None)  # a chat-lane ask: no session
    try:
        rows = ap.pending_for("s1")
        assert [r["approval_id"] for r in rows] == [a, b]  # oldest first
        assert rows[0] == {
            "approval_id": a, "tool": "shell", "args": {"command": "mv a b"},
            "requested_at": rows[0]["requested_at"],
        }
        assert isinstance(rows[0]["requested_at"], float)
        assert [r["approval_id"] for r in ap.pending_for("s2")] == [c]
        # A chat-lane ask is never attributed to a run; no id answers [].
        assert ap.pending_for(None) == [] and ap.pending_for("") == []
        assert ap.pending_for("nope") == []
        # pop forgets the metadata too.
        ap.pop(a)
        assert [r["approval_id"] for r in ap.pending_for("s1")] == [b]
    finally:
        for f in (fa, fb, fc, fd):
            if not f.done():
                f.set_result("deny")


@pytest.mark.asyncio
async def test_resolve_where_answers_only_the_matching_session_and_tools():
    ap = ChatApprovals()
    a, fa = ap.request("shell", None, session_id="s1")
    b, fb = ap.request("shell", None, session_id="s1")
    c, fc = ap.request("write_file", None, session_id="s1")
    d, fd = ap.request("shell", None, session_id="s2")
    e, fe = ap.request("shell", None)
    try:
        assert ap.resolve_where("s1", "shell", "conversation") == 2
        assert fa.result() == "conversation" and fb.result() == "conversation"
        assert not fc.done() and not fd.done() and not fe.done()
        # An answered future is not answered twice; a set of names works.
        assert ap.resolve_where("s1", {"shell", "write_file"}, "conversation") == 1
        assert fc.result() == "conversation"
        # Bad decision / no session: nothing happens.
        assert ap.resolve_where("s2", "shell", "maybe") == 0
        assert ap.resolve_where(None, "shell", "conversation") == 0
        assert not fd.done()
    finally:
        for f in (fd, fe):
            if not f.done():
                f.set_result("deny")


# --------------------------------------------------------------------------- #
# A2 — the runtime: a 'conversation' grant wakes the siblings (converted A1)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_conversation_grant_on_one_parallel_ask_releases_its_siblings(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 1.0)
    allow: set = set()
    agent_def = get_agent_definition(AgentType.BUILDER)

    async def answer_first_with_conversation():
        ok = await _wait_for(lambda: len(_events(rt, "approval.requested")) >= 3)
        assert ok, "the asks never got filed"
        first = _events(rt, "approval.requested", "session_test")[0]
        assert rt.platform.approvals.resolve(first["payload"]["approval_id"], "conversation")

    answerer = asyncio.create_task(answer_first_with_conversation())
    other_allow: set = set()
    (d1, _), (d2, _), (d3, _) = await asyncio.gather(
        rt.runtime._pause_for_approval(_session(), _tc("mv a b"), agent_def, allow),
        rt.runtime._pause_for_approval(_session(), _tc("mv c d"), agent_def, allow),
        # ANOTHER session's ask for the same tool must NOT be released by this
        # session's grant — it runs out its (short) clock.
        rt.runtime._pause_for_approval(
            _session("session_other"), _tc("mv e f"), agent_def, other_allow
        ),
    )
    await answerer
    assert "shell" in allow
    assert d1 == "" and d2 == "", (d1, d2)
    assert d3 == PAUSE_TIMEOUT_REASON and "shell" not in other_allow
    decisions = [
        p["payload"]["decision"] for p in _events(rt, "approval.resolved", "session_test")
    ]
    assert decisions == ["conversation", "conversation"], decisions
    assert [
        p["payload"]["decision"] for p in _events(rt, "approval.resolved", "session_other")
    ] == ["timeout"]
    # Every id was popped: nothing is left dangling in the registry.
    assert rt.platform.approvals.pending_count() == 0


@pytest.mark.asyncio
async def test_once_and_deny_release_nothing(rt, monkeypatch):
    """'once' covers exactly this call and 'deny' refuses exactly this call —
    neither may speak for a sibling."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.6)
    allow: set = set()
    agent_def = get_agent_definition(AgentType.BUILDER)

    async def answer():
        ok = await _wait_for(lambda: len(_events(rt, "approval.requested")) >= 2)
        assert ok
        reqs = _events(rt, "approval.requested")
        assert rt.platform.approvals.resolve(reqs[0]["payload"]["approval_id"], "once")

    answerer = asyncio.create_task(answer())
    (d1, extra1), (d2, _) = await asyncio.gather(
        rt.runtime._pause_for_approval(_session(), _tc("mv a b"), agent_def, allow),
        rt.runtime._pause_for_approval(_session(), _tc("mv c d"), agent_def, allow),
    )
    await answerer
    assert d1 == "" and extra1 == {"shell"}
    assert d2 == PAUSE_TIMEOUT_REASON
    assert "shell" not in allow


# --------------------------------------------------------------------------- #
# A11 — the honest timeout (converted A6)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_timeout_reason_is_honest_and_rides_the_ledger_as_paused(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    await orch.run_session(sess.id)

    assert BRIEF_SENTENCE in PAUSE_TIMEOUT_REASON
    denied = _events(rt, "tool.denied", sess.id)
    assert len(denied) == 1
    assert denied[0]["payload"]["kind"] == "paused", denied[0]["payload"]
    assert denied[0]["payload"]["reason"] == PAUSE_TIMEOUT_REASON
    # The old text told the model to ask the user to re-run mid-run.
    assert "ask the user to re-run" not in denied[0]["payload"]["reason"]
    # The ledger row carries the same reason; nothing ran.
    with session_scope(rt.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == sess.id)))
        assert len(rows) == 1 and rows[0].ok is False and rows[0].tool == "shell"
        assert BRIEF_SENTENCE in rows[0].output
    # What the MODEL read: label + reason, never "permission denied".
    transcript = orch.transcript(sess.id)
    tool_msgs = [m for m in transcript.get("messages", []) if m.get("role") == "tool"]
    if tool_msgs:  # the transcript keeps tool messages on some shapes only
        assert "permission denied" not in tool_msgs[0].get("content", "")
        assert "paused: " in tool_msgs[0].get("content", "")


@pytest.mark.asyncio
async def test_a_users_deny_is_still_a_permission_denial(rt, monkeypatch):
    """Only the CLOCK is 'paused'; a human's No keeps its label."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 2.0)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    ok = await _wait_for(lambda: bool(_events(rt, "approval.requested", sess.id)))
    assert ok
    ap_id = _events(rt, "approval.requested", sess.id)[0]["payload"]["approval_id"]
    assert rt.platform.approvals.resolve(ap_id, "deny")
    await task
    denied = _events(rt, "tool.denied", sess.id)
    assert len(denied) == 1 and denied[0]["payload"]["kind"] == "permission denied"
    assert "declined" in denied[0]["payload"]["reason"]


# --------------------------------------------------------------------------- #
# A4 (runtime half) — the run says WAITING while paused (converted A3)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_paused_run_is_waiting_in_the_db_and_running_again_after(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 2.0)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    ok = await _wait_for(lambda: bool(_events(rt, "approval.requested", sess.id)))
    assert ok, "the run never paused"
    # The transition is PUBLISHED with {from, to} and PERSISTED before the
    # request is announced, so a surface reacting to the ask already finds
    # the row waiting.
    changes = [p["payload"] for p in _events(rt, "agent.state_changed", sess.id)]
    assert {"from": "running", "to": "waiting"} == {
        k: changes[-1][k] for k in ("from", "to")
    }, changes
    assert _run_state(rt.platform.engine, sess.id) == "waiting"
    ap_id = _events(rt, "approval.requested", sess.id)[0]["payload"]["approval_id"]
    assert rt.platform.approvals.resolve(ap_id, "once")
    await task
    changes = [
        (p["payload"]["from"], p["payload"]["to"])
        for p in _events(rt, "agent.state_changed", sess.id)
    ]
    assert ("waiting", "running") in changes, changes
    assert changes.index(("waiting", "running")) > changes.index(("running", "waiting"))
    assert _run_state(rt.platform.engine, sess.id) == "completed"


@pytest.mark.asyncio
async def test_timeout_and_deny_both_leave_running_then_terminal(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    await orch.run_session(sess.id)
    changes = [
        (p["payload"]["from"], p["payload"]["to"])
        for p in _events(rt, "agent.state_changed", sess.id)
    ]
    assert ("running", "waiting") in changes and ("waiting", "running") in changes
    assert changes[-1] == ("running", "completed"), changes
    assert _run_state(rt.platform.engine, sess.id) == "completed"


@pytest.mark.asyncio
async def test_a_cancel_during_the_pause_never_leaves_waiting(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 30.0)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    orch._running[sess.id] = task
    ok = await _wait_for(lambda: _run_state(rt.platform.engine, sess.id) == "waiting")
    assert ok, "the run never reached WAITING"
    orch.cancel_session(sess.id)
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert _run_state(rt.platform.engine, sess.id) == "cancelled"
    changes = [
        (p["payload"]["from"], p["payload"]["to"])
        for p in _events(rt, "agent.state_changed", sess.id)
    ]
    # The pause's own exit path restored RUNNING before the cancel settled.
    assert ("waiting", "running") in changes, changes
    assert rt.platform.approvals.pending_count() == 0


@pytest.mark.asyncio
async def test_parallel_asks_share_one_waiting_transition(rt, monkeypatch):
    """N parallel asks = ONE running->waiting and ONE waiting->running, not
    N of each (the kanban must not flicker per card)."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.3)
    agent_def = get_agent_definition(AgentType.BUILDER)
    run = AgentRun(session_id="session_test", state=AgentState.RUNNING)
    rt.runtime._save(run)
    await asyncio.gather(
        rt.runtime._pause_for_approval(_session(), _tc("mv a b"), agent_def, set(), run=run),
        rt.runtime._pause_for_approval(_session(), _tc("mv c d"), agent_def, set(), run=run),
        rt.runtime._pause_for_approval(_session(), _tc("mv e f"), agent_def, set(), run=run),
    )
    changes = [
        (p["payload"]["from"], p["payload"]["to"])
        for p in _events(rt, "agent.state_changed", "session_test")
    ]
    assert changes == [("running", "waiting"), ("waiting", "running")], changes
    assert run.state is AgentState.RUNNING
    assert _run_state(rt.platform.engine, "session_test") == "running"
    assert rt.runtime._waiting_depth == {}


@pytest.mark.asyncio
async def test_a_bare_pause_without_a_run_changes_no_state(rt, monkeypatch):
    """The v1189 call shape (no ``run``) keeps working and publishes no
    state change — there is no run to flip."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.1)
    deny, extra = await rt.runtime._pause_for_approval(
        _session(), _tc("mv a b"), get_agent_definition(AgentType.BUILDER), set()
    )
    assert deny == PAUSE_TIMEOUT_REASON and extra == set()
    assert _events(rt, "agent.state_changed") == []
