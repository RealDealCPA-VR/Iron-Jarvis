"""v1.361.0 lane 11C (5): a run the router refused before sending anything
claims neither local nor cloud.

The v1.360.1 run page said "· cloud" (hover: "this message left your network
for OpenRouter") for a run on an unconnected cloud provider: the AgentRun row
is saved BEFORE the first model call, and ``GET /sessions/{id}/result``
judged ``on_this_network`` for the stored provider whether or not anything
was sent. Now ``agents/outcome.reached_a_model`` reads the record — a step a
model answered (or its tokens), a tool call a model asked for, or the
router's own dispatch events (``provider.routed`` is published right before
an adapter is called, after every refusal it can make first) — and the
receipt says ``None`` when none of them is there.

Pinned both ways, through the REAL app factory: a refused openrouter run
(nothing leaves the PC — the HTTP transports are spied) answers ``None``;
a run the router DID send (a routed event, a step, tokens, a tool call) is
still judged; the demo model is still "on this network".
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.outcome import DISPATCH_EVENTS, reached_a_model, session_result
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import (
    AgentRun,
    EventRecord,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app

_BASE = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)


@pytest.fixture()
def outbound(monkeypatch):
    """Every real HTTP request the daemon tries to make, refused and recorded
    (the TestClient's own transport is not an httpx.HTTPTransport)."""
    sent: list[str] = []

    def _block(self, request, *a, **k):
        sent.append(str(request.url))
        raise httpx.ConnectError("blocked by the test")

    async def _ablock(self, request, *a, **k):
        sent.append(str(request.url))
        raise httpx.ConnectError("blocked by the test")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _block)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _ablock)
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return sent


def test_a_run_the_router_refused_says_neither_local_nor_cloud(tmp_path, outbound):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        # The dashboard's way: the run goes on in the background.
        r = client.post(
            "/sessions",
            json={"task": "say hi", "provider": "openrouter", "model": "meta/llama-3-8b", "wait": False},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        detail = _settled(client, sid)
        # The shape the run page saw: one FAILED agent run that no model ever
        # answered (saved before the first call, never stepped).
        assert detail["session"]["status"] == "failed"
        assert detail["session"]["provider"] == "openrouter"
        runs = detail["transcript"]["runs"]
        assert len(runs) == 1
        assert runs[0]["steps"] == 0
        assert runs[0]["input_tokens"] == 0 and runs[0]["output_tokens"] == 0
        assert detail["transcript"]["tools"] == []
        res = client.get(f"/sessions/{sid}/result").json()
        assert res["provider"] == "openrouter"
        # Nothing was sent, so nothing is claimed — never "cloud".
        assert res["on_this_network"] is None
        # The router really refused before dispatch: no dispatch event, and no
        # request left for OpenRouter.
        with session_scope(client.app.state.platform.engine) as db:
            assert not reached_a_model(db, sid, "openrouter")
            kinds = {e.type for e in db.exec(_events_for(sid))}
        assert not (kinds & set(DISPATCH_EVENTS)), kinds
    assert not [u for u in outbound if "openrouter" in u], outbound


def _settled(client, sid: str) -> dict:
    """GET /sessions/{id} once the run has ended (bounded; the refusal is
    immediate, the bound only keeps a hang from waiting forever)."""
    deadline = time.monotonic() + 60
    while True:
        detail = client.get(f"/sessions/{sid}").json()
        if detail["session"]["status"] not in ("active", "queued"):
            return detail
        assert time.monotonic() < deadline, detail["session"]
        time.sleep(0.05)


def _events_for(sid: str):
    from sqlmodel import select

    return select(EventRecord).where(EventRecord.session_id == sid)


def _seed(engine, sid: str, *, provider: str = "openrouter", steps: int = 0, tokens: int = 0,
          tool: bool = False, event: str | None = None) -> None:
    with session_scope(engine) as db:
        db.add(
            Session(
                id=sid,
                task="t",
                provider=provider,
                model="meta/llama-3-8b",
                status=SessionStatus.FAILED,
                created_at=_BASE,
            )
        )
        db.add(
            AgentRun(
                id=f"run_{sid}",
                session_id=sid,
                provider=provider,
                steps=steps,
                input_tokens=tokens,
                output_tokens=0,
                created_at=_BASE,
            )
        )
        if tool:
            db.add(
                ToolInvocation(
                    id=f"inv_{sid}",
                    session_id=sid,
                    agent_run_id=f"run_{sid}",
                    tool="read_file",
                    args_json=json.dumps({"path": "a.txt"}),
                    created_at=_BASE,
                )
            )
        if event:
            db.add(
                EventRecord(
                    id=f"ev_{sid}",
                    type=event,
                    session_id=sid,
                    payload_json=json.dumps({"resolved_provider": provider}),
                    created_at=_BASE,
                )
            )
        db.commit()


@pytest.mark.parametrize(
    ("label", "kw"),
    [
        # A cloud call that went out and failed (an error answer): the router
        # published provider.routed before calling the adapter.
        ("routed then failed", {"event": EventType.PROVIDER_ROUTED}),
        ("a substitute answered", {"event": EventType.PROVIDER_FAILOVER}),
        ("a step was answered", {"event": EventType.LLM_COMPLETED}),
        ("steps", {"steps": 1}),
        ("tokens only", {"tokens": 12}),
        ("a tool call", {"tool": True}),
    ],
)
def test_a_run_that_was_sent_is_still_judged(tmp_path, label, kw):
    client = TestClient(create_app(str(tmp_path / "home")))
    engine = client.app.state.platform.engine
    _seed(engine, "sess_sent", **kw)
    body = client.get("/sessions/sess_sent/result").json()
    assert body["on_this_network"] is False, label  # a cloud API left the network


def test_the_refused_shape_seeded_says_none_and_a_foreign_event_does_not_count(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    engine = client.app.state.platform.engine
    # The router's own words for a refusal (provider.downgraded) are not a
    # dispatch.
    _seed(engine, "sess_refused", event=EventType.PROVIDER_DOWNGRADED)
    assert client.get("/sessions/sess_refused/result").json()["on_this_network"] is None
    # Another session's dispatch never speaks for this one.
    _seed(engine, "sess_other", event=EventType.PROVIDER_ROUTED)
    assert client.get("/sessions/sess_refused/result").json()["on_this_network"] is None
    assert client.get("/sessions/sess_other/result").json()["on_this_network"] is False


def test_the_demo_model_and_the_cost_guard_are_unchanged(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        db.add(Session(id="sess_demo", task="t", provider="mock", model="mock-1",
                       status=SessionStatus.COMPLETED, created_at=_BASE))
        db.commit()
    # The offline demo runs in-process: still "on this network", run or not.
    assert client.get("/sessions/sess_demo/result").json()["on_this_network"] is True
    # The finalize/coach/compaction callers still skip the receipt reads.
    _seed(engine, "sess_plain", steps=1)
    assert "on_this_network" not in session_result(engine, "sess_plain")
