"""v1.232.0 Wave 6, task 6D — grants that carry, the posture that rides
(audit A6 / A7).

Converted from ``tests/_audit_20260904/test_approvals_lane_audit.py``:
A2 (a continue could not carry a grant made after the opener — ContinueBody
had no ``allow_tools`` and a card's "conversation" answer lived only in the
run's in-memory set) and A7 (the chat posture never rode the escalation).
The audit's A7 expected a yolo chat's escalation to run WITHOUT asking;
Decision 2 says yolo NEVER inherits — it lands as approve-for-me — so that
test is inverted here on purpose.

What is pinned:

* A6: ``POST /sessions/{id}/continue`` takes ``allow_tools`` and UNIONS it
  with the stored grant; a 'conversation' answer to a mid-run ask is written
  to the row's ``allow_tools_json`` at resolve time (off the loop), and the
  in-memory Session carries it too (the finalizers ``merge`` that object), so
  ``continue_session`` inherits it with no help from the client.
* A7: ``approval_mode`` rides ``POST /sessions`` / ``/continue`` / a custom
  spawn onto the row (``inherited_approval_mode``: yolo → approve_for_me at
  every door); ``approve_for_me`` lets the grant list run without a pause and
  asks for everything else; ``always_ask`` asks even for an armed tool and
  honours only a grant answered during THIS run; rerun/continue inherit it.
* The chat page sends the armed set + posture on the continue (source pin —
  the call site, like ``test_draft_spacing_v1163``).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    PAUSE_TIMEOUT_REASON,
    AgentRuntime,
    inherited_approval_mode,
)
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": type, "payload": payload or {}, "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(
        runtime=AgentRuntime(platform), platform=platform, published=published, app=app
    )


def _session(sid="session_test", origin="chat", approval_mode=""):
    return SimpleNamespace(id=sid, origin=origin, approval_mode=approval_mode)


def _tc(cmd="mv a b"):
    return SimpleNamespace(name="shell", arguments={"command": cmd})


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


def _stored(engine, session_id: str) -> list[str]:
    with session_scope(engine) as db:
        row = db.get(SessionRow, session_id)
        return json.loads(row.allow_tools_json or "[]")


def _posture(engine, session_id: str) -> str:
    with session_scope(engine) as db:
        return db.get(SessionRow, session_id).approval_mode


def _settle(engine, session_id: str) -> None:
    """A continue refuses a busy workspace: mark the row finished first."""
    with session_scope(engine) as db:
        row = db.get(SessionRow, session_id)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()


# --------------------------------------------------------------------------- #
# A6 — grants that carry
# --------------------------------------------------------------------------- #
def test_continue_body_allow_tools_unions_with_the_stored_grant(tmp_path):
    """Converted A2: the opener's grant rode already; a grant sent on the
    continue did not exist. Now it is unioned — widened, never narrowed."""
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/sessions",
        json={"task": "survey the folder", "wait": True, "origin": "chat",
              "allow_tools": ["read_file"]},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r2 = client.post(
        f"/sessions/{sid}/continue",
        json={"message": "now rename them", "wait": True,
              "allow_tools": ["rename_file", "read_file"]},
    )
    assert r2.status_code == 200, r2.text
    stored = _stored(client.app.state.platform.engine, r2.json()["id"])
    assert stored == ["read_file", "rename_file"], stored


@pytest.mark.asyncio
async def test_a_conversation_grant_is_persisted_and_rides_the_continue(rt):
    """The run's "Allow for this run" answer lands on the ROW at resolve time
    (not only in the in-memory set), the Session object in hand carries it
    (the finalizers merge that object), and the next continue inherits it
    with NOTHING sent by the client."""
    orch = Orchestrator(rt.platform)
    session = await orch.create_session("rename the files", origin="chat")
    assert _stored(rt.platform.engine, session.id) == []
    allow: set = set()
    answerer = asyncio.create_task(_answer(rt, "conversation", session_id=session.id))
    deny, _ = await rt.runtime._pause_for_approval(
        session, _tc(), get_agent_definition(AgentType.BUILDER), allow
    )
    await answerer
    assert deny == "" and "shell" in allow
    assert "shell" in _stored(rt.platform.engine, session.id), "not persisted"
    assert "shell" in json.loads(session.allow_tools_json), (
        "the in-memory Session must carry it or a later merge undoes the row"
    )
    # A stale merge (what every finalizer does) keeps the grant.
    session.status = SessionStatus.COMPLETED
    orch._save(session)
    assert "shell" in _stored(rt.platform.engine, session.id)
    # …and the continue — the chat's next message — inherits it unasked.
    follow = await orch.continue_session(session.id, "now the rest")
    assert "shell" in _stored(rt.platform.engine, follow.id)
    # The in-run set is per session id: the follow-up starts clean.
    assert follow.id not in rt.runtime._run_grants


@pytest.mark.asyncio
async def test_a_failed_grant_write_never_fails_the_call(rt, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(rt.runtime, "_persist_grant", boom)
    allow: set = set()
    answerer = asyncio.create_task(_answer(rt, "conversation"))
    deny, _ = await rt.runtime._pause_for_approval(
        _session(), _tc(), get_agent_definition(AgentType.BUILDER), allow
    )
    await answerer
    assert deny == "" and "shell" in allow


# --------------------------------------------------------------------------- #
# A7 — the posture that rides (and the one that never does)
# --------------------------------------------------------------------------- #
def test_yolo_never_inherits_at_the_vocabulary():
    assert inherited_approval_mode("yolo") == "approve_for_me"
    assert inherited_approval_mode("YOLO ") == "approve_for_me"
    assert inherited_approval_mode("always_ask") == "always_ask"
    assert inherited_approval_mode("approve_for_me") == "approve_for_me"
    assert inherited_approval_mode("full_auto") == "approve_for_me"
    assert inherited_approval_mode("") == "" and inherited_approval_mode(None) == ""


def test_a_yolo_chat_escalates_as_approve_for_me_and_still_asks(rt, monkeypatch):
    """Inverted A7: the audit expected no ask; Decision 2 says a yolo chat's
    run asks once per ask-tier tool like any other — auto-approve was
    consented to one watched turn at a time, not for a background batch."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    rt.platform.router.stream = _shell_then_done()
    client = TestClient(rt.app)
    r = client.post(
        "/sessions",
        json={"task": "rename the files", "wait": True, "origin": "chat",
              "approval_mode": "yolo"},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["approval_mode"] == "approve_for_me"
    assert _posture(rt.platform.engine, sid) == "approve_for_me"
    asked = _events(rt, "approval.requested", sid)
    assert asked and asked[0]["payload"]["tool"] == "shell", (
        "a yolo chat's escalation must still ask"
    )


@pytest.mark.asyncio
async def test_approve_for_me_auto_grants_an_armed_tool_and_asks_for_an_unarmed_one(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.2)
    agent_def = get_agent_definition(AgentType.BUILDER)
    # Armed at escalation (allow_tools carried shell): no pause, no ask.
    deny, extra = await rt.runtime._pause_for_approval(
        _session(approval_mode="approve_for_me"), _tc(), agent_def, {"shell"}
    )
    assert (deny, extra) == ("", set())
    assert not _events(rt, "approval.requested")
    # The default posture ("" — every non-chat door) behaves the same.
    deny, _ = await rt.runtime._pause_for_approval(
        _session(approval_mode=""), _tc(), agent_def, {"shell"}
    )
    assert deny == "" and not _events(rt, "approval.requested")
    # Not armed: the pause runs its clock and reports the honest timeout.
    deny, _ = await rt.runtime._pause_for_approval(
        _session(approval_mode="approve_for_me"), _tc(), agent_def, set()
    )
    assert deny == PAUSE_TIMEOUT_REASON
    assert len(_events(rt, "approval.requested")) == 1


@pytest.mark.asyncio
async def test_always_ask_ignores_the_armed_list_but_honours_a_run_grant(rt):
    """Ask-for-approval in a session: the list the escalation carried does
    not pre-approve; the tool asks once, and "Allow for this run" covers the
    rest of the run (the sibling release covers its batch)."""
    agent_def = get_agent_definition(AgentType.BUILDER)
    allow = {"shell"}
    answerer = asyncio.create_task(_answer(rt, "conversation"))
    deny, _ = await rt.runtime._pause_for_approval(
        _session(approval_mode="always_ask"), _tc(), agent_def, allow
    )
    await answerer
    assert deny == ""
    assert len(_events(rt, "approval.requested")) == 1, "an armed tool must still ask"
    # Second call this run: the run grant covers it — no second ask.
    deny, _ = await rt.runtime._pause_for_approval(
        _session(approval_mode="always_ask"), _tc("mv c d"), agent_def, allow
    )
    assert deny == ""
    assert len(_events(rt, "approval.requested")) == 1


@pytest.mark.asyncio
async def test_rerun_and_continue_inherit_the_posture_and_never_yolo(rt):
    orch = Orchestrator(rt.platform)
    s1 = await orch.create_session("job", origin="chat", approval_mode="always_ask")
    assert _posture(rt.platform.engine, s1.id) == "always_ask"
    _settle(rt.platform.engine, s1.id)
    s2 = await orch.rerun_session(s1.id)
    assert _posture(rt.platform.engine, s2.id) == "always_ask"
    s3 = await orch.continue_session(s1.id, "more")
    assert _posture(rt.platform.engine, s3.id) == "always_ask"
    _settle(rt.platform.engine, s3.id)
    # A stated posture on the continue replaces the parent's; yolo lands as
    # approve-for-me at this door too.
    s4 = await orch.continue_session(s1.id, "more", approval_mode="yolo")
    assert _posture(rt.platform.engine, s4.id) == "approve_for_me"
    s5 = await orch.create_session("plain", origin="job:agents")
    assert _posture(rt.platform.engine, s5.id) == ""


def test_the_continue_route_and_the_spawn_carry_the_posture(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/sessions", json={"task": "t", "wait": True, "origin": "chat"})
    sid = r.json()["id"]
    r2 = client.post(
        f"/sessions/{sid}/continue",
        json={"message": "m", "wait": True, "approval_mode": "always_ask"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["approval_mode"] == "always_ask"
    # A custom-agent escalation is the same door (SpawnBody.approval_mode).
    from iron_jarvis.daemon.schemas import ContinueBody, SessionCreate, SpawnBody

    for model in (SessionCreate, SpawnBody, ContinueBody):
        assert "approval_mode" in model.model_fields, model.__name__
    assert "allow_tools" in ContinueBody.model_fields


# --------------------------------------------------------------------------- #
# The page's call site (the client half of A6/A7)
# --------------------------------------------------------------------------- #
def test_the_chat_page_sends_the_grant_and_posture_on_the_continue():
    src = (ROOT / "dashboard" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    m = re.search(r"/sessions/\$\{sessionId\}/continue`, \{(.*?)\}\);", src, re.S)
    assert m, "the continue POST moved — re-anchor this pin"
    body = m.group(1)
    assert "allow_tools: armedNow" in body, "the continue no longer sends the armed set"
    assert "...posture" in body, "the continue no longer sends the posture"
    # The posture rides both escalation bodies too (builtin + custom spawn).
    assert src.count("...posture,") >= 2, src.count("...posture,")
