"""v1.352.0 lane 2A (readiness audit G16, G19) — a helper agent's reply
comes home with the right words.

G16: a run the user STOPPED is not a failure. When the daemon's terminal path
appends the line to the chat, a stopped run with no summary says "The agent
was stopped before it finished." and a failed one "The agent could not finish
this. Try again or open the run." (the page's own finalize says the same two
sentences; ``tests/test_integration_pins_v1345.py`` pins that).

G19: the card the line carries is the RECEIPT read — the same one
``GET /sessions/{id}/result`` answers — so the chat's run card and the
settled helper-agent row can name the route note and the plain action lines
whichever side wrote the line. ``on_this_network`` rides only when the
caller hands over the provider manager: judged without the endpoint settings,
a local model would read as cloud, and absent beats wrong.

Driven on the REAL orchestrator over ``build_platform`` (the v1.345.0 shape):
the real registry, the real ``write_file`` tool, the real ledger behind
``session_result``, the real append.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from iron_jarvis.agents import chat_delivery
from iron_jarvis.agents.chat_delivery import (
    FAILED_SENTENCE,
    NO_RESPONSE,
    STOPPED_SENTENCE,
    content_for_session,
    delivery_result,
)
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.outcome import session_result
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, ChatThreadRecord, Session, SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["read_file", "write_file"])
SEED = [{"role": "user", "content": "@builder write me a brief", "at": "seed"}]


def _platform(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    return p, Orchestrator(p)


def _script(p, responses):
    p.providers.register("mock", lambda model=None: MockLLMAdapter(script=list(responses)))


def _thread(engine) -> str:
    rec = ChatThreadRecord(title="brief", messages_json=json.dumps(SEED))
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _messages(engine, thread_id) -> list[dict]:
    with session_scope(engine) as db:
        rec = db.get(ChatThreadRecord, thread_id)
        assert rec is not None
        return json.loads(rec.messages_json or "[]")


def _session_row(engine, sid) -> Session:
    with session_scope(engine) as db:
        row = db.get(Session, sid)
        assert row is not None
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# G16 — the words for how it ended
# --------------------------------------------------------------------------- #


def test_a_stopped_run_and_a_failed_run_end_in_different_plain_words():
    def s(status, summary=""):
        return SimpleNamespace(id="s", status=status, summary=summary, agent_type=AgentType.BUILDER)

    assert content_for_session(s(SessionStatus.CANCELLED)) == STOPPED_SENTENCE
    assert STOPPED_SENTENCE == "The agent was stopped before it finished."
    assert content_for_session(s(SessionStatus.FAILED)) == FAILED_SENTENCE
    assert FAILED_SENTENCE == "The agent could not finish this. Try again or open the run."
    # neither sentence is the other, and neither is a status word in brackets
    assert STOPPED_SENTENCE != FAILED_SENTENCE
    for sentence in (STOPPED_SENTENCE, FAILED_SENTENCE):
        assert "(" not in sentence and "cancelled" not in sentence and "failed" not in sentence
    # any other end short of "completed" is not called a stop
    assert content_for_session(s(SessionStatus.ACTIVE)) == FAILED_SENTENCE
    # a summary still wins, whatever the status; a completed run is unchanged
    assert content_for_session(s(SessionStatus.CANCELLED, "  Session cancelled by the user. ")) == (
        "Session cancelled by the user."
    )
    assert content_for_session(s(SessionStatus.COMPLETED)) == NO_RESPONSE


async def test_a_stop_lands_in_its_chat_with_a_cancelled_receipt(tmp_path):
    """The user pressed Stop: the real cancel path appends ONE line whose card
    says the run was cancelled (the dashboard reads that as a neutral
    "Stopped") and carries the receipt's reads."""
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session("go", AgentType.BUILDER, chat_thread_id=tid)
    await orch._finalize_cancelled(s)
    msgs = _messages(p.engine, tid)
    assert len(msgs) == 2
    line = msgs[-1]
    assert line["sessionId"] == s.id
    assert line["receipt"]["status"] == "cancelled"
    # the receipt read: its route note and action lines ride along
    assert "route_note" in line["receipt"] and "activity" in line["receipt"]


# --------------------------------------------------------------------------- #
# G19 — the delivered card is the receipt read
# --------------------------------------------------------------------------- #


async def test_the_delivered_card_carries_the_receipts_plain_action_lines(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session("write me a brief", AgentType.BUILDER, chat_thread_id=tid)
    _script(
        p,
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "brief.md", "content": "# Brief"})],
                finish_reason="tool_use",
            ),
            LLMResponse(text="Wrote the brief.", finish_reason="stop"),
        ],
    )
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    line = _messages(p.engine, tid)[-1]
    receipt = line["receipt"]
    # the same reads GET /sessions/{id}/result makes...
    full = session_result(p.engine, s.id, receipt=True)
    assert receipt["route_note"] == full["route_note"] == ""
    assert receipt["activity"] == full["activity"]
    # ...in plain words, never the tool id
    texts = [a["text"] for a in receipt["activity"]]
    assert texts and any("brief.md" in t for t in texts), texts
    assert not any("write_file" in t for t in texts), texts
    # who served it (the row's pair) and the ledger's per-tool counts
    assert receipt["provider"] == "mock"
    assert receipt["tools_used"] == [{"tool": "write_file", "count": 1}]
    # judged with no endpoint settings, "on this network" is LEFT OUT
    assert "on_this_network" not in receipt


def test_with_the_provider_manager_the_card_says_where_it_ran(tmp_path):
    """``delivery_result`` keeps ``on_this_network`` only when it can judge it:
    with the manager the mock reads as on this network (it runs in-process),
    without it the key is absent rather than a guess."""
    p, _orch = _platform(tmp_path)
    with session_scope(p.engine) as db:
        db.add(Session(id="sess_where", task="t", status=SessionStatus.COMPLETED, provider="mock", model="m"))
        db.commit()
    judged = delivery_result(p.engine, "sess_where", manager=p.providers, config=p.config)
    assert judged["on_this_network"] is True
    blind = delivery_result(p.engine, "sess_where")
    assert "on_this_network" not in blind
    # the rest of the receipt is the same either way
    for key in ("route_note", "activity", "provider", "model", "steps", "status"):
        assert judged[key] == blind[key], key


def test_deliver_session_line_hands_the_manager_through(tmp_path, monkeypatch):
    """The optional ``manager``/``config`` reach the receipt read (the
    orchestrator can pass its own to make the card say where)."""
    p, _orch = _platform(tmp_path)
    tid = _thread(p.engine)
    with session_scope(p.engine) as db:
        db.add(
            Session(
                id="sess_hand",
                task="t",
                status=SessionStatus.CANCELLED,
                provider="mock",
                options_json=json.dumps({"chat_thread_id": tid}),
            )
        )
        db.commit()
    row = _session_row(p.engine, "sess_hand")
    out = chat_delivery.deliver_session_line(p.engine, row, manager=p.providers, config=p.config)
    assert out["ok"] is True
    line = _messages(p.engine, tid)[-1]
    assert line["content"] == STOPPED_SENTENCE
    assert line["receipt"]["on_this_network"] is True
