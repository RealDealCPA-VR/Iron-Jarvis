"""v1.335.0 — each chat totals its own use.

Every chat turn's usage row was written with ``session_id="chat"`` and
nothing else, so the app could only ever say what ALL chats had used. Both
lanes (and the follow-up chips) now stamp the saved chat's id on the row, and
``GET /chat/threads/{id}/usage`` sums it — failed turns included, a flat
subscription's figure kept apart as a list-price equivalent.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState
from iron_jarvis.daemon.app import create_app


def _usage(client, tid):
    r = client.get(f"/chat/threads/{tid}/usage")
    assert r.status_code == 200, r.text
    return r.json()


def test_both_lanes_stamp_the_chat_on_the_usage_row(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    msgs = [{"role": "user", "content": "hello"}]
    assert client.post("/chat", json={"messages": msgs, "thread_id": "t-post"}).status_code == 200
    r = client.post("/chat/stream", json={"messages": msgs, "thread_id": "t-stream"})
    assert r.status_code == 200 and "event: done" in r.text, r.text[-500:]
    # An unsaved chat (no id) is counted nowhere per-chat.
    assert client.post("/chat", json={"messages": msgs}).status_code == 200

    post, stream = _usage(client, "t-post"), _usage(client, "t-stream")
    assert post["turns"] == 1 and stream["turns"] == 1
    assert post["since"] and post["models"][0]["provider"] == "mock"
    with session_scope(client.app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(AgentRun)).all() if r.session_id == "chat"]
    assert sorted(str(r.thread_id) for r in rows) == ["None", "t-post", "t-stream"]
    assert _usage(client, "nobody")["turns"] == 0


def test_the_total_sums_tokens_money_and_failed_turns(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    eng = client.app.state.platform.engine
    with session_scope(eng) as db:
        db.add(AgentRun(session_id="chat", thread_id="t1", provider="openai", model="gpt-x",
                        state=AgentState.COMPLETED, input_tokens=1000, output_tokens=200,
                        cost_usd=0.02))
        db.add(AgentRun(session_id="chat", thread_id="t1", provider="openai", model="gpt-x",
                        state=AgentState.FAILED, input_tokens=500, output_tokens=0,
                        cost_usd=0.01))
        db.add(AgentRun(session_id="chat", thread_id="t1", provider="claude-cli",
                        model="claude-sonnet-4-5", state=AgentState.COMPLETED,
                        input_tokens=300, output_tokens=100, cost_usd=0.5))
        # Someone else's chat and an agent run never leak in.
        db.add(AgentRun(session_id="chat", thread_id="t2", provider="openai", model="gpt-x",
                        state=AgentState.COMPLETED, input_tokens=9999, cost_usd=9.0))
        db.add(AgentRun(session_id="sess_1", thread_id="t1", provider="openai", model="gpt-x",
                        state=AgentState.COMPLETED, input_tokens=7777, cost_usd=7.0))
        db.commit()
    u = _usage(client, "t1")
    assert u["turns"] == 3 and u["failed_turns"] == 1
    assert u["input_tokens"] == 1800 and u["output_tokens"] == 300
    assert u["cost_usd"] == 0.03  # metered money only
    assert u["list_price_equivalent_usd"] == 0.5  # the subscription's, kept apart
    top = u["models"][0]
    assert (top["provider"], top["turns"], top["input_tokens"]) == ("openai", 2, 1500)
    assert u["models"][1]["list_price_equivalent"] is True
