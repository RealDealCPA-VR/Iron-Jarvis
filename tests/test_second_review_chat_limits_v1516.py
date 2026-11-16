"""v1.516.0 — second review L9, L3, L8: chat limits, the summary's cost, plain words.

L9  The language rewrite had no time limit in either lane: a model that never
    answered held a finished reply open for ever. Both lanes now bound it by
    ``chat_turn._LANGUAGE_REWRITE_TIMEOUT_S`` (read at call time, shrunk here);
    when it runs out the ORIGINAL reply is kept with the "could not rewrite"
    note, as on any other failure.

L3  The automatic summary of a long chat is a paid model call whose usage was
    dropped (``app._compaction_complete`` kept only the text). It is now one
    usage row of that chat (``chat_turn.bill_summary``, the follow-ups' way),
    so the chat's own total and the Usage page count it. The Compact button's
    summary is billed the same way.

L8  Engine words in replies: "stopped after N tool rounds; M tool call(s) not
    executed" (both lanes), raw tool names in the no-answer reply, and a
    Python exception ("KeyError: 'path'") as a step's output (both lanes).

Driven through the REAL lanes on a real platform; only the model (the router
or one adapter) and the tool body (``registry.invoke``) are doubles.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.daemon.chat_turn as chat_turn
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.profile.language import rewrite_instruction
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

LANES = ("post", "stream")

#: How long a RED run may take before the guard reports it (a bound on a hang,
#: never a measurement: the green path ends as soon as the limit fires).
HANG_GUARD_S = 10.0


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


async def _run(platform, lane: str, body: ChatBody, **kw) -> tuple[dict, list]:
    """One turn on *lane*: ``(reply payload, frames)`` — the POST result or
    the stream's ``done`` frame, and the SSE frames ([] for POST)."""
    if lane == "post":
        return await run_chat_turn(platform, BUILTIN_PERSONAS, body, **kw), []
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    frames = _frames([c async for c in gen])
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-3:]
    return done[-1], frames


# =========================================================================== #
# L9 — the language rewrite has a time limit, in both lanes
# =========================================================================== #
ORIGINAL = "Voici la réponse."


class _HangingRewrite:
    """Answers the turn, then never answers the rewrite (it waits on an event
    nothing sets: only the lane's limit can end it)."""

    def __init__(self) -> None:
        self.rewrite_asked = 0
        self.never = asyncio.Event()

    @staticmethod
    def _is_rewrite(messages) -> bool:
        last = list(messages or [])[-1:] or [None]
        return getattr(last[0], "content", "") == rewrite_instruction("en")

    async def complete(self, *, system, messages, tools, **kw):
        if self._is_rewrite(messages):
            self.rewrite_asked += 1
            await self.never.wait()
        return RouteResult(LLMResponse(text=ORIGINAL, usage={"input_tokens": 4, "output_tokens": 2}),
                           "mock", "mock")

    async def stream(self, *, system, messages, tools, **kw):
        if self._is_rewrite(messages):
            self.rewrite_asked += 1
            await self.never.wait()
        resp = LLMResponse(text=ORIGINAL, usage={"input_tokens": 4, "output_tokens": 2})
        yield {"type": "text", "text": ORIGINAL}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}


@pytest.mark.parametrize("lane", LANES)
async def test_a_rewrite_that_never_answers_keeps_the_original_reply(tmp_path, monkeypatch, lane):
    platform = build_platform(str(tmp_path))
    model = _HangingRewrite()
    monkeypatch.setattr(platform.router, "complete", model.complete)
    monkeypatch.setattr(platform.router, "stream", model.stream)

    def leak(_platform, text, _user):
        return "en" if ORIGINAL in (text or "") else ""

    # Both lanes read the leak through their own module name.
    monkeypatch.setattr(chat_turn, "_language_leak", leak)
    monkeypatch.setattr(chat_routes, "_language_leak", leak)
    # The injected limit (raising=False: before the fix the constant is absent
    # and the rewrite simply hangs — the guard below reports that as red).
    monkeypatch.setattr(chat_turn, "_LANGUAGE_REWRITE_TIMEOUT_S", 0.05, raising=False)

    body = ChatBody(messages=[ChatMessageBody(role="user", content="answer please")])
    try:
        out, _frames_ = await asyncio.wait_for(_run(platform, lane, body), HANG_GUARD_S)
    except TimeoutError:
        pytest.fail(f"{lane}: the language rewrite has no time limit (the turn never ended)")
    assert model.rewrite_asked == 1, f"{lane}: the rewrite was asked once"
    reply = out["reply"]
    assert reply.startswith(ORIGINAL), f"{lane}: the original reply is kept: {reply!r}"
    assert chat_turn._rewrite_failed_note("en") in reply, reply


def test_the_limit_is_a_named_constant_read_at_call_time():
    assert chat_turn._LANGUAGE_REWRITE_TIMEOUT_S == 120.0
    src = open(chat_routes.__file__, "rb").read().decode("utf-8")
    assert "timeout_s=_chat_turn._LANGUAGE_REWRITE_TIMEOUT_S" in src


# =========================================================================== #
# L3 — the automatic summary is on the chat's own usage
# =========================================================================== #
SUMMARY = "GOAL:\n- audit the ledger\n"
SUMMARY_USAGE = {"input_tokens": 700, "output_tokens": 70}


class _SummaryAdapter(LLMAdapter):
    """The default provider's adapter, which writes the summary (the REAL
    ``app._compaction_complete`` path calls it directly)."""

    provider = "acme-test"
    model = "acme-1"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return {"tool_use": True, "vision": False}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text=SUMMARY, tool_calls=[], usage=dict(SUMMARY_USAGE))

    async def stream(self, *, system, messages, tools, **kw):  # pragma: no cover - not used
        resp = await self.complete(system=system, messages=messages, tools=tools)
        yield {"type": "final", "response": resp}


def _summary_client(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    adapter = _SummaryAdapter()
    platform.providers.register("acme-test", lambda m=None: adapter)
    real_available = platform.providers.available
    monkeypatch.setattr(platform.providers, "available",
                        lambda n: n == "acme-test" or real_available(n))
    platform.config.default_provider = "acme-test"
    platform.config.default_model = "acme-1"
    r = client.put("/settings", json={"values": {
        "model_context_windows": {"mock": 2000, "acme-test": 2000},
    }})
    assert r.status_code == 200, r.text

    # The turn itself answers through the router (a double); only the summary
    # goes through the adapter above.
    async def fake_complete(*, system, messages, tools, **kw):
        return RouteResult(LLMResponse(text="ok", usage={"input_tokens": 5, "output_tokens": 1}),
                           "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        resp = LLMResponse(text="ok", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return client, adapter


def _long_chat() -> list[dict]:
    filler = "audit the ledger carefully, line by line. " * 12
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}: {filler}"}
            for i in range(14)]
    msgs.append({"role": "user", "content": "and now the totals?"})
    return msgs


def _summary_rows(platform, tid: str) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun).where(AgentRun.thread_id == tid))
                if r.input_tokens == SUMMARY_USAGE["input_tokens"]]


@pytest.mark.parametrize("lane", LANES)
def test_the_automatic_summary_is_on_the_chats_usage(tmp_path, monkeypatch, lane):
    client, adapter = _summary_client(tmp_path, monkeypatch)
    tid = f"t-sum-{lane}"
    payload = {"messages": _long_chat(), "thread_id": tid}
    if lane == "post":
        r = client.post("/chat", json=payload)
        assert r.status_code == 200, r.text
        ctx = r.json()["context"]
    else:
        r = client.post("/chat/stream", json=payload)
        assert r.status_code == 200, r.text
        done = [d for e, d in _frames([r.text]) if e == "done"]
        assert done, r.text[-400:]
        ctx = done[-1]["context"]
    # Anti-vacuity: the summary really ran, through the real factory.
    assert ctx.get("compacted") is True and ctx.get("trigger") == "auto", ctx
    assert adapter.calls == 1

    rows = _summary_rows(client.app.state.platform, tid)
    assert len(rows) == 1, f"{lane}: the summary's usage must be on this chat (rows={rows})"
    row = rows[0]
    assert (row.session_id, row.provider, row.output_tokens) == ("chat", "acme-test", 70)
    assert row.result == chat_turn.SUMMARY_USAGE_LABEL
    used = client.get(f"/chat/threads/{tid}/usage").json()
    assert used["input_tokens"] >= 700 and any(m["provider"] == "acme-test" for m in used["models"])


def test_the_compact_buttons_summary_is_on_the_chats_usage(tmp_path, monkeypatch):
    client, adapter = _summary_client(tmp_path, monkeypatch)
    r = client.post("/chat/compact", json={"messages": _long_chat(), "thread_id": "t-manual"})
    assert r.status_code == 200, r.text
    assert adapter.calls == 1
    rows = _summary_rows(client.app.state.platform, "t-manual")
    assert len(rows) == 1 and rows[0].result == chat_turn.SUMMARY_USAGE_LABEL


async def test_a_summary_call_that_never_answered_is_not_billed(tmp_path):
    from types import SimpleNamespace

    platform = build_platform(str(tmp_path))
    d = SimpleNamespace(platform=platform)

    async def call(system, user):  # never ran: no last_usage
        return "", "", ""

    call.last_usage = None
    assert await chat_turn.bill_summary(d, call, SimpleNamespace(provider="", model=""),
                                        thread_id="t-none") is False
    with session_scope(platform.engine) as db:
        assert not list(db.exec(select(AgentRun).where(AgentRun.thread_id == "t-none")))


# =========================================================================== #
# L8 — plain words in replies
# =========================================================================== #
class _Model:
    """Asks for a tool on every round that offers tools; answers in words
    when called with none (the lane's final-answer nudge). Keeps every
    message list it was sent."""

    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.sent: list[list] = []

    def _resp(self, tools) -> LLMResponse:
        self.calls.append(bool(tools))
        if tools:
            return LLMResponse(
                text="", usage={"input_tokens": 10, "output_tokens": 2},
                tool_calls=[ToolCall(id=f"c{len(self.calls)}", name="image_info",
                                     arguments={"path": "x.png"})],
            )
        return LLMResponse(text="Here is where it stands.", usage={"input_tokens": 10, "output_tokens": 2})

    async def stream(self, *, system, messages, tools, **kw):
        self.sent.append(list(messages))
        resp = self._resp(tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    async def complete(self, *, system, messages, tools, **kw):
        self.sent.append(list(messages))
        return RouteResult(self._resp(tools), "mock", "mock")


def _wired(tmp_path, monkeypatch, invoke):
    platform = build_platform(str(tmp_path))
    model = _Model()
    monkeypatch.setattr(platform.router, "stream", model.stream)
    monkeypatch.setattr(platform.router, "complete", model.complete)
    monkeypatch.setattr(platform.registry, "invoke", invoke)
    return platform, model


def _body() -> ChatBody:
    return ChatBody(messages=[ChatMessageBody(role="user", content="look at x.png")],
                    tools=["image_info"])


ENGINE_WORDS = ("tool rounds", "tool call(s)", "not executed", "round")


@pytest.mark.parametrize("lane", LANES)
async def test_the_out_of_rounds_note_is_plain(tmp_path, monkeypatch, lane):
    async def invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        return ToolResult(ok=True, output="an image, 10x10")

    platform, model = _wired(tmp_path, monkeypatch, invoke)
    out, _f = await _run(platform, lane, _body(), round_budget=3, stay_in_chat=True)
    assert model.calls == [True] * 3 + [False]  # the cap really cut a call
    reply = out["reply"]
    for word in ENGINE_WORDS:
        assert word not in reply, f"{lane}: {word!r} in {reply!r}"
    assert "I stopped after 2 model calls, the most one reply may take; " \
           "1 more step was not run" in reply


def test_the_out_of_rounds_note_counts_in_plain_words():
    assert chat_turn.rounds_stopped_note(7, 2).endswith("2 more steps were not run")
    assert chat_turn.rounds_stopped_note(7, 1).endswith("1 more step was not run")
    # Lead's review: "round" is never shown (VOCABULARY.md); a round is a model call.
    assert chat_turn.rounds_stopped_note(7, 2).startswith("I stopped after 7 model calls, ")
    assert chat_turn.rounds_stopped_note(1, 2).startswith("I stopped after 1 model call, ")
    assert "round" not in chat_turn.rounds_stopped_note(7, 2)
    for word in ENGINE_WORDS:
        assert word not in chat_turn.rounds_stopped_note(7, 2)


@pytest.mark.parametrize("lane", LANES)
async def test_a_step_that_raised_reads_as_plain_words(tmp_path, monkeypatch, lane):
    async def invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        raise KeyError("path")

    platform, model = _wired(tmp_path, monkeypatch, invoke)
    out, frames = await _run(platform, lane, _body(), round_budget=2, stay_in_chat=True)
    # What the model read back as the step's result (both lanes).
    tool_texts = [str(getattr(m, "content", "")) for sent in model.sent for m in sent
                  if getattr(m, "role", "") == "tool"]
    assert tool_texts, f"{lane}: the failed step was handed back to the model"
    for text in tool_texts:
        assert "KeyError" not in text, f"{lane}: {text!r}"
    assert any(chat_turn.STEP_CRASHED in t for t in tool_texts), tool_texts
    if lane == "stream":
        # What the person reads on the step.
        outputs = [d.get("output", "") for e, d in frames
                   if e == "tool_call" and d.get("status") == "finished"]
        assert outputs and all("KeyError" not in o for o in outputs), outputs
        assert any(chat_turn.STEP_CRASHED in o for o in outputs), outputs


def test_a_step_keeps_a_sentence_iron_jarvis_wrote():
    class Refused(Exception):
        pass

    # An exception class of this package carries our own words.
    Refused.__module__ = "iron_jarvis.fake_area"
    words = "That folder is outside the ones Iron Jarvis may use."
    assert chat_turn.step_crash_words(Refused(words)) == words


def test_the_no_answer_reply_names_steps_not_tools():
    reply = chat_turn._no_text_reply(["write_document", "excel_query"], "the result text")
    assert reply.startswith(chat_turn.NO_TEXT_LEAD)
    assert "write_document" not in reply and "excel_query" not in reply, reply
    assert "It ran these steps: " in reply and reply.endswith("Here is what came back last:\nthe result text")
    one = chat_turn._no_text_reply([], "the result text")
    assert one == f"{chat_turn.NO_TEXT_LEAD} Here is what came back last:\nthe result text"
