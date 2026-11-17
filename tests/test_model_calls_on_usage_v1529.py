"""v1.529.0 — every model call is on a usage row (owner decisions 3 and 4).

3. A model call made INSIDE A TOOL (``view_image``, a screenshot's question,
   reading text in a picture) was on no usage row: the lanes count only their
   own completions. The router's one seam (``eval.call_ledger.note_model_call``)
   now gives it a row of its own, visible on the Usage page (``GET /usage``).
4. A chat reply's row is written once, at its end, so a hard kill mid-reply
   lost the calls already made. Each counted call is now an open record; the
   end row deletes them in its own transaction, and the next start folds what
   an earlier daemon left into one row per reply.

Everything runs through the real router, the real lanes, the real registry
and the real agent runtime; only the model adapter is a stand-in.
"""

from __future__ import annotations

import struct
import threading
import zlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, OpenModelCall
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import call_ledger
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import ToolContext

#: What each kind of call reports (distinct, so every sum is unambiguous).
TOOL_ASK = {"input_tokens": 300, "output_tokens": 11}  # the round that asks for the tool
PICTURE = {"input_tokens": 5000, "output_tokens": 40}  # the model call INSIDE view_image
ANSWER = {"input_tokens": 700, "output_tokens": 23}  # a plain answer (an ordinary reply's one call)


def _png(path) -> str:
    """A real 1x1 PNG on disk (view_image refuses what is not a picture)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    path.write_bytes(raw)
    return str(path)


class _StandIn(MockLLMAdapter):
    """The model, decided by what it is asked (never by call order, so a
    check the lane adds on its own never shifts the script):

    * a request carrying a PICTURE (view_image's question) -> :data:`PICTURE`;
    * a request that offers ``view_image`` and has no tool result yet -> a
      ``view_image`` call (:data:`TOOL_ASK`);
    * anything else -> a plain answer (:data:`ANSWER`)."""

    picture = ""
    calls: list[str] = []

    async def complete(self, *, system, messages, tools, **kw):
        if any(getattr(m, "images", None) for m in messages):
            type(self).calls.append("picture")
            return LLMResponse(text="A small green square.", usage=dict(PICTURE))
        offered = {str((t.get("function") or t).get("name") or "") for t in (tools or [])
                   if isinstance(t, dict)}
        answered = any(getattr(m, "role", "") == "tool" for m in messages)
        if "view_image" in offered and not answered:
            type(self).calls.append("ask")
            return LLMResponse(
                text="", finish_reason="tool_use", usage=dict(TOOL_ASK),
                tool_calls=[ToolCall(id="c1", name="view_image",
                                     arguments={"path": type(self).picture,
                                                "question": "What colour is it?"})],
            )
        type(self).calls.append("answer")
        return LLMResponse(text="It is green.", usage=dict(ANSWER))


def _stand_in(platform, picture: str = "") -> type[_StandIn]:
    cls = type("_Stand", (_StandIn,), {"picture": picture, "calls": []})
    platform.providers.register("mock", lambda model=None: cls())
    return cls


def _rows(engine, model):
    with session_scope(engine) as db:
        return list(db.exec(select(model)).all())


def _chat_rows(engine, thread: str):
    return [r for r in _rows(engine, AgentRun) if r.session_id == "chat" and r.thread_id == thread]


def _another_start(monkeypatch) -> None:
    """The daemon that wrote the open records is gone; this is the next one."""
    monkeypatch.setattr(call_ledger, "PROCESS_ID", "the-next-start")


# --------------------------------------------------------------------------- #
# Decision 4: an ordinary reply is unchanged, and leaves nothing open
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["/chat", "/chat/stream"])
def test_an_ordinary_reply_writes_one_row_with_the_same_totals_and_leaves_nothing_open(
    tmp_path, monkeypatch, lane
):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    _stand_in(app.state.platform)
    kept: list[dict] = []
    real_insert = call_ledger.TurnLedger._insert

    def spy(self, fields):  # the open record really was written for this reply
        kept.append(fields)
        real_insert(self, fields)

    monkeypatch.setattr(call_ledger.TurnLedger, "_insert", spy)
    body = {"messages": [{"role": "user", "content": "hello"}], "thread_id": "t-plain"}
    r = TestClient(app).post(lane, json=body)
    assert r.status_code == 200, r.text
    assert call_ledger.drain()
    rows = _chat_rows(engine, "t-plain")
    assert len(rows) == 1, [(x.input_tokens, x.result) for x in rows]
    assert (rows[0].input_tokens, rows[0].output_tokens) == (ANSWER["input_tokens"], ANSWER["output_tokens"])
    assert rows[0].state == AgentState.COMPLETED and rows[0].steps == 1
    assert kept and kept[0]["input_tokens"] == ANSWER["input_tokens"], "the call was kept while it ran"
    assert _rows(engine, OpenModelCall) == [], "the end row took the reply's open calls with it"
    # ...and the next start adds nothing for a reply whose end row was written.
    _another_start(monkeypatch)
    assert call_ledger.settle_open_calls(engine) == 0
    assert len(_chat_rows(engine, "t-plain")) == 1


# --------------------------------------------------------------------------- #
# Decision 3: a model call inside a tool is on a row of its own
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["/chat", "/chat/stream"])
def test_a_tool_that_asks_a_model_gets_its_own_usage_row_on_the_usage_page(tmp_path, lane):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    stand = _stand_in(platform, _png(tmp_path / "pic.png"))
    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "what colour is pic.png?"}],
            "thread_id": "t-pic", "tools": ["view_image"]}
    r = client.post(lane, json=body)
    assert r.status_code == 200, r.text
    assert call_ledger.drain()
    assert stand.calls.count("picture") == 1, stand.calls  # the tool really asked a model

    rows = _chat_rows(platform.engine, "t-pic")
    tool_rows = [x for x in rows if x.result == call_ledger.TOOL_WORDS["view_image"]]
    turn_rows = [x for x in rows if x not in tool_rows]
    assert len(tool_rows) == 1, [(x.input_tokens, x.result) for x in rows]
    pic = tool_rows[0]
    assert (pic.input_tokens, pic.output_tokens, pic.steps) == (
        PICTURE["input_tokens"], PICTURE["output_tokens"], 1)
    assert pic.state == AgentState.COMPLETED and pic.provider == "mock"
    # The reply's own row is unchanged: its two rounds, never the picture's call.
    assert len(turn_rows) == 1
    assert turn_rows[0].input_tokens == TOOL_ASK["input_tokens"] + ANSWER["input_tokens"]
    assert turn_rows[0].output_tokens == TOOL_ASK["output_tokens"] + ANSWER["output_tokens"]
    # The Usage page reads it.
    totals = client.get("/usage").json()["totals"]
    assert totals["input_tokens"] == TOOL_ASK["input_tokens"] + ANSWER["input_tokens"] + PICTURE["input_tokens"]
    assert _rows(platform.engine, OpenModelCall) == []


async def test_the_stream_twin_and_a_counted_scope(tmp_path):
    """The router's stream path counts a call inside a tool too; an app's
    (MCP) tool and an agent run count their own and add no row here."""
    p = build_platform(str(tmp_path))
    _stand_in(p)
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="", config=p.config,
                      event_bus=p.event_bus, engine=p.engine, thread_id="t-s")
    msgs = [LLMMessage(role="user", content="hi")]
    with call_ledger.tool_scope("read_document", ctx):
        async for _ in p.router.stream(system="s", messages=msgs, tools=[]):
            pass
    with call_ledger.tool_scope("mcp__pack__tool", ctx):
        await p.router.complete(system="s", messages=msgs, tools=[])
    with call_ledger.tool_scope("delegate", ctx), call_ledger.counted_scope():
        await p.router.complete(system="s", messages=msgs, tools=[])
    await p.router.complete(system="s", messages=msgs, tools=[])  # no tool: as before
    assert call_ledger.drain()
    rows = _rows(p.engine, AgentRun)
    assert [(r.thread_id, r.input_tokens, r.result) for r in rows] == [
        ("t-s", ANSWER["input_tokens"], call_ledger.TOOL_WORDS_DEFAULT)]


# --------------------------------------------------------------------------- #
# Decision 4: a reply cut off by a hard kill is counted at the next start
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["/chat", "/chat/stream"])
def test_a_reply_killed_after_two_calls_is_counted_once_at_the_next_start(tmp_path, monkeypatch, lane):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    stand = _stand_in(platform, _png(tmp_path / "pic.png"))
    # The kill: both rounds were answered, and the reply's end row never ran.
    monkeypatch.setattr(chat_turn, "_write_chat_usage", lambda *a, **k: None)
    body = {"messages": [{"role": "user", "content": "what colour is pic.png?"}],
            "thread_id": "t-cut", "tools": ["view_image"]}
    assert TestClient(app).post(lane, json=body).status_code == 200
    assert call_ledger.drain()
    assert stand.calls.count("ask") == 1 and stand.calls.count("answer") == 1
    assert len(_rows(platform.engine, OpenModelCall)) == 2
    lost = [x for x in _chat_rows(platform.engine, "t-cut")
            if x.result != call_ledger.TOOL_WORDS["view_image"]]
    assert lost == [], "the reply's own row was never written"

    # A reply running NOW in this daemon is never settled.
    assert call_ledger.settle_open_calls(platform.engine) == 0
    _another_start(monkeypatch)
    assert call_ledger.settle_open_calls(platform.engine) == 1
    cut = [x for x in _chat_rows(platform.engine, "t-cut")
           if x.result != call_ledger.TOOL_WORDS["view_image"]]
    assert len(cut) == 1
    row = cut[0]
    assert row.input_tokens == TOOL_ASK["input_tokens"] + ANSWER["input_tokens"]
    assert row.output_tokens == TOOL_ASK["output_tokens"] + ANSWER["output_tokens"]
    assert row.steps == 2 and row.state == AgentState.FAILED
    assert row.result == call_ledger.CUT_OFF_WORDS
    assert _rows(platform.engine, OpenModelCall) == []
    # A second start adds nothing.
    assert call_ledger.settle_open_calls(platform.engine) == 0
    assert len(_chat_rows(platform.engine, "t-cut")) == 2  # the reply + the picture


def test_an_open_write_that_lands_after_the_end_row_is_never_counted_again(tmp_path, monkeypatch):
    """The end row and a late open write are exclusive: the worker held up
    until the end row is written, then let go, writes nothing."""
    p = build_platform(str(tmp_path))
    gate = threading.Event()
    call_ledger._submit(gate.wait, 10)  # hold the one worker
    tally = call_ledger.JournaledTally(p.engine, thread_id="t-race", turn_id="turn-race")
    tally.add("openai", "gpt-4o", {"input_tokens": 50, "output_tokens": 5})
    d = type("D", (), {"platform": p})()
    chat_turn._write_chat_usage(
        d, {"provider": "openai", "model": "gpt-4o", "state": AgentState.COMPLETED, "steps": 1,
            "input_tokens": 50, "output_tokens": 5, "cost_usd": tally.cost_usd},
        thread="t-race", turn="", turn_id="turn-race", tool_session="", ledger=tally.ledger,
    )
    gate.set()
    assert call_ledger.drain()
    assert _rows(p.engine, OpenModelCall) == []
    _another_start(monkeypatch)
    assert call_ledger.settle_open_calls(p.engine) == 0
    assert [r.input_tokens for r in _chat_rows(p.engine, "t-race")] == [50]


# --------------------------------------------------------------------------- #
# The agent runtime: one row, as before; a run started by a tool not twice
# --------------------------------------------------------------------------- #
async def test_an_agent_run_keeps_one_row_and_a_tool_that_starts_one_adds_none(tmp_path):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.tools.permissions import PermissionEngine

    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine({**p.config.permissions, "spawn_agent": "allow"})
    stand = _stand_in(p)
    orch = Orchestrator(p)
    s = await orch.create_session("say hello", provider="mock", model="mock-1")
    await orch.run_session(s.id)
    assert call_ledger.drain()
    assert "picture" not in stand.calls  # no model call was made inside a tool
    used = {"ask": TOOL_ASK, "answer": ANSWER}
    runs = [r for r in _rows(p.engine, AgentRun) if r.session_id == s.id]
    assert len(runs) == 1 and runs[0].steps == len(stand.calls)
    assert runs[0].input_tokens == sum(used[c]["input_tokens"] for c in stand.calls)
    assert [r.id for r in _rows(p.engine, AgentRun)] == [runs[0].id], "nothing was made inside a tool"

    # An agent run started BY A TOOL counts its own calls once.
    p.agents_registry.register("scout", "You help. Be brief.", ["list_files"])
    ctx = ToolContext(workspace=tmp_path, session_id="parent-session", agent_run_id="parent1",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    res = await p.registry.invoke("spawn_agent", {"agent": "scout", "task": "say hello"},
                                  ctx, p.permissions)
    assert res.ok, res.error
    assert call_ledger.drain()
    extra = [r for r in _rows(p.engine, AgentRun) if r.id not in {x.id for x in runs}]
    assert extra and all(r.session_id not in ("chat", call_ledger.STEP_SESSION) for r in extra), [
        (r.session_id, r.result) for r in extra]
    assert _rows(p.engine, OpenModelCall) == []


def test_the_daemon_start_settles_a_cut_off_reply(tmp_path):
    """The start-up step is WIRED (lead's review): open calls an earlier
    daemon left are folded into one usage row when the real app starts, and
    nothing is left open."""
    from datetime import datetime, timezone

    p = build_platform(str(tmp_path))
    with session_scope(p.engine) as db:
        for tokens in (100, 200):
            db.add(OpenModelCall(
                owner="earlier-reply", process="an-earlier-daemon", session_id="chat",
                thread_id="t-boot", turn_id="turn-boot", provider="mock", model="m",
                input_tokens=tokens, output_tokens=1, cost_usd=0.0,
                started_at=datetime.now(timezone.utc),
            ))
        db.commit()
    app = create_app(str(tmp_path))
    with TestClient(app):  # runs the start-up steps
        pass
    rows = [x for x in _chat_rows(app.state.platform.engine, "t-boot")]
    assert len(rows) == 1, "the start did not count the cut-off reply's calls"
    assert rows[0].input_tokens == 300 and rows[0].result == call_ledger.CUT_OFF_WORDS
    assert _rows(app.state.platform.engine, OpenModelCall) == []
