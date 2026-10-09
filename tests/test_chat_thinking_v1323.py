"""v1.323.0 (wave B) — the model's REASONING is shown, never answered with.

A new adapter stream frame ``{"type": "thinking", "text": <delta>}`` carries a
model's reasoning text: Anthropic ``thinking_delta`` events, claude-cli's
thinking deltas, the OpenAI-compatible ``delta.reasoning_content`` /
``delta.reasoning`` spellings (vLLM / Ollama / LiteLLM), and a local model's
leading ``<think>…</think>`` block, split off ONCE in the router
(``ThinkSplitter`` streamed, ``split_think_text`` on a ``complete()``).

The rules pinned here:

  * /chat/stream emits ``event: thinking`` ``{"text": delta}`` from EVERY
    round; the reasoning NEVER joins ``done.reply`` or the next round's
    messages (display-only);
  * a ``<think>`` block split across deltas is reasoning, not answer, and the
    final response's text is the answer alone;
  * POST /chat returns ``thinking`` (capped, "" when none) beside the reply;
  * a ``thinking`` frame does not COMMIT a stream — a provider that dies
    after reasoning but before a word of answer can still be retried.

Driven through the REAL app (``create_app``) and, where the router matters,
the REAL ``ModelRouter``; only the model is a double.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
)
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.adapters.subprocess_cli import ClaudeCliAdapter
from iron_jarvis.providers.router import ModelRouter, RouteResult
from iron_jarvis.tools.base import ToolResult

REASONING = "I should check the page first."


def _events(text: str, name: str) -> list[dict]:
    """Every SSE frame of one event kind, decoded."""
    out = []
    for block in text.split("\n\n"):
        if block.startswith(f"event: {name}\n"):
            out.append(json.loads(block.split("data:", 1)[1]))
    return out


def _done(text: str) -> dict:
    frames = _events(text, "done")
    assert frames, text[-800:]
    return frames[-1]


async def _drain(agen) -> list[dict]:
    return [f async for f in agen]


def _user(text: str = "q") -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


# --------------------------------------------------------------------------- #
# 1. the stream lane: thinking frames out, never into the reply or the history
# --------------------------------------------------------------------------- #


async def _ok_invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
    return ToolResult(ok=True, output="page text")


def test_stream_lane_emits_thinking_from_every_round_and_keeps_it_out_of_the_reply(
    tmp_path, monkeypatch
):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    monkeypatch.setattr(platform.registry, "invoke", _ok_invoke)
    sent: list[list[str]] = []

    async def fake_stream(*, system, messages, tools, **kw):
        sent.append([m.content or "" for m in messages])
        if len(sent) == 1:
            yield {"type": "thinking", "text": "I should check "}
            yield {"type": "thinking", "text": "the page first."}
            call = ToolCall(id="c1", name="image_info", arguments={"path": "p.png"})
            yield {"type": "final", "response": LLMResponse(
                text="", tool_calls=[call], usage={}, thinking=REASONING,
            ), "provider": "mock", "model": "mock"}
        else:
            yield {"type": "thinking", "text": "Now answer."}
            yield {"type": "text", "text": "The page says hello."}
            yield {"type": "final", "response": LLMResponse(
                text="The page says hello.", usage={}, thinking="Now answer.",
            ), "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "what does the page say?"}],
        "tools": ["image_info"],
    })
    assert r.status_code == 200, r.text
    thinking = [f["text"] for f in _events(r.text, "thinking")]
    # From EVERY round, in order, as it streamed.
    assert thinking == ["I should check ", "the page first.", "Now answer."]
    tokens = "".join(f["text"] for f in _events(r.text, "token"))
    assert tokens == "The page says hello."
    done = _done(r.text)
    assert done["reply"].startswith("The page says hello.")
    for frag in ("I should check", "Now answer"):
        assert frag not in done["reply"], done["reply"]
    # Round 2's messages (the replayed assistant turn + tool result) carry no
    # reasoning: it never joins the next completion's history.
    assert len(sent) == 2
    assert not any("I should check" in c for c in sent[1]), sent[1]


def test_post_lane_returns_thinking_and_keeps_it_out_of_the_reply(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform

    async def fake_complete(**kw):
        return RouteResult(
            LLMResponse(text="Answer.", thinking=REASONING), "mock", "mock",
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thinking"] == REASONING
    assert REASONING not in body["reply"]
    assert body["reply"].startswith("Answer.")


def test_post_lane_thinking_is_empty_when_none_and_capped(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    answers = [
        # A test double's response object with no `thinking` attribute at all.
        RouteResult(SimpleNamespace(text="Plain.", tool_calls=[], usage={}), "mock", "mock"),
        RouteResult(LLMResponse(text="Long.", thinking="x" * 50_000), "mock", "mock"),
    ]

    async def fake_complete(**kw):
        return answers.pop(0)

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    first = client.post("/chat", json={"messages": [{"role": "user", "content": "a"}]}).json()
    assert first["thinking"] == ""
    second = client.post("/chat", json={"messages": [{"role": "user", "content": "b"}]}).json()
    assert len(second["thinking"]) == chat_turn.THINKING_CAP == 20_000


# --------------------------------------------------------------------------- #
# 2. <think> from a local model — split across deltas, in the REAL router
# --------------------------------------------------------------------------- #


class _ThinkTagAdapter(LLMAdapter):
    """A local reasoning model that writes its reasoning inline."""

    provider, model = "custom", "qwen3-32b"
    PIECES = ["  <th", "ink>Let me ", "add 2 and 2.</th", "ink>\n\nIt is", " 4."]

    async def complete(self, *, system, messages, tools, **kw):
        return LLMResponse(text="".join(self.PIECES), usage={})

    async def stream(self, *, system, messages, tools, **kw):
        for piece in self.PIECES:
            yield {"type": "text", "text": piece}
        yield {"type": "final", "response": LLMResponse(text="".join(self.PIECES), usage={})}


class _Mgr:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, p):
        return p in self.adapters

    def has_available_api_provider(self):
        return True

    def has_available_real_endpoint(self):
        return False

    def runtime_provider_names(self):
        return []

    def get(self, p, m=None):
        return self.adapters[p]


def _router(adapter) -> ModelRouter:
    return ModelRouter(
        _Mgr({adapter.provider: adapter}), default_provider=adapter.provider, event_bus=EventBus()
    )


async def test_router_splits_a_think_block_split_across_deltas():
    frames = await _drain(_router(_ThinkTagAdapter()).stream(
        system="", messages=_user(), tools=[]
    ))
    thinking = "".join(f["text"] for f in frames if f["type"] == "thinking")
    text = "".join(f["text"] for f in frames if f["type"] == "text")
    assert thinking == "Let me add 2 and 2."
    assert text == "It is 4."
    final = frames[-1]
    assert final["type"] == "final"
    assert final["response"].text == "It is 4."
    assert final["response"].thinking == "Let me add 2 and 2."
    # No answer frame ever carried a piece of a tag.
    assert all("<" not in f["text"] for f in frames if f["type"] in ("text", "thinking"))


async def test_router_complete_strips_a_think_block_from_a_final_reply():
    route = await _router(_ThinkTagAdapter()).complete(system="", messages=_user(), tools=[])
    assert route.response.text == "It is 4."
    assert route.response.thinking == "Let me add 2 and 2."


async def test_a_think_tag_later_in_a_reply_is_text():
    class _Mentions(_ThinkTagAdapter):
        PIECES = ["Use the <think>", " tag like this."]

    frames = await _drain(_router(_Mentions()).stream(system="", messages=_user(), tools=[]))
    assert [f for f in frames if f["type"] == "thinking"] == []
    assert frames[-1]["response"].text == "Use the <think> tag like this."


def test_think_block_through_both_real_lanes(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    real = _router(_ThinkTagAdapter())
    monkeypatch.setattr(platform.router, "stream", real.stream)
    monkeypatch.setattr(platform.router, "complete", real.complete)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "2+2?"}]})
    assert "".join(f["text"] for f in _events(r.text, "thinking")) == "Let me add 2 and 2."
    assert "".join(f["text"] for f in _events(r.text, "token")) == "It is 4."
    done = _done(r.text)
    assert done["reply"].startswith("It is 4.") and "think" not in done["reply"]
    post = client.post("/chat", json={"messages": [{"role": "user", "content": "2+2?"}]}).json()
    assert post["reply"].startswith("It is 4.") and "think" not in post["reply"]
    assert post["thinking"] == "Let me add 2 and 2."


# --------------------------------------------------------------------------- #
# 3. a thinking frame does not commit the stream
# --------------------------------------------------------------------------- #


class _ThinksThenBlips(LLMAdapter):
    """Reasons, then a transient 503 BEFORE any answer; answers on the retry."""

    provider, model = "openrouter", "some/model"

    def __init__(self):
        self.calls = 0

    async def complete(self, *, system, messages, tools, **kw):  # pragma: no cover
        raise AssertionError("streamed only")

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "thinking", "text": "pondering"}
        if self.calls == 1:
            raise ProviderError("overloaded", status_code=503)
        yield {"type": "text", "text": "answer"}
        yield {"type": "final", "response": LLMResponse(text="answer", usage={})}


async def test_a_death_after_thinking_but_before_any_answer_is_not_committed():
    adapter = _ThinksThenBlips()
    frames = await _drain(_router(adapter).stream(system="", messages=_user(), tools=[]))
    assert adapter.calls == 2, "the transient blip was not retried"
    assert frames[-1]["type"] == "final" and frames[-1]["response"].text == "answer"


# --------------------------------------------------------------------------- #
# 4. each adapter's thinking, through its existing test seam
# --------------------------------------------------------------------------- #


async def test_the_default_single_chunk_stream_carries_the_responses_thinking():
    class _CompleteOnly(LLMAdapter):
        provider, model = "x", "y"

        async def complete(self, *, system, messages, tools, **kw):
            return LLMResponse(text="Answer.", thinking="Because.")

    frames = await _drain(_CompleteOnly().stream(system="", messages=_user(), tools=[]))
    assert [(f["type"], f.get("text")) for f in frames[:-1]] == [
        ("thinking", "Because."), ("text", "Answer."),
    ]


class _AnthropicStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


class _AnthropicMessages:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def stream(self, **kw):
        @asynccontextmanager
        async def cm():
            yield _AnthropicStream(self._events, self._final)
        return cm()


def _delta(kind: str, **fields):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=kind, **fields))


async def test_anthropic_stream_turns_thinking_deltas_into_thinking_frames(monkeypatch):
    events = [
        SimpleNamespace(type="message_start"),
        _delta("thinking_delta", thinking="Step one. "),
        _delta("thinking_delta", thinking="Step two."),
        _delta("signature_delta", signature="sig"),
        _delta("text_delta", text="Done"),
        _delta("text_delta", text="."),
        SimpleNamespace(type="message_stop"),
    ]
    final = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="Step one. Step two.", signature="sig"),
            SimpleNamespace(type="text", text="Done."),
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2),
    )
    a = AnthropicAdapter(model="claude-opus-4-8", api_key="sk-ant-test")
    monkeypatch.setattr(a, "_client", lambda: SimpleNamespace(messages=_AnthropicMessages(events, final)))
    frames = await _drain(a.stream(system="", messages=_user(), tools=[]))
    assert [(f["type"], f.get("text")) for f in frames[:-1]] == [
        ("thinking", "Step one. "), ("thinking", "Step two."), ("text", "Done"), ("text", "."),
    ]
    resp = frames[-1]["response"]
    assert resp.text == "Done." and resp.thinking == "Step one. Step two."


def _sse_adapter(chunks: list[dict]) -> OpenAIAdapter:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    return OpenAIAdapter(
        "qwen3-32b",
        api_key="sk-test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="http://fleet.test/v1/chat/completions",
        provider_name="custom",
    )


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


async def test_openai_compatible_reasoning_content_streams_as_thinking():
    a = _sse_adapter([
        _chunk({"reasoning_content": "Think "}),
        _chunk({"reasoning_content": "hard."}),
        _chunk({"content": "Yes."}, "stop"),
    ])
    frames = await _drain(a.stream(system="", messages=_user(), tools=[]))
    assert [(f["type"], f.get("text")) for f in frames[:-1]] == [
        ("thinking", "Think "), ("thinking", "hard."), ("text", "Yes."),
    ]
    resp = frames[-1]["response"]
    assert resp.text == "Yes." and resp.thinking == "Think hard."


async def test_openai_compatible_reasoning_spelling_streams_as_thinking():
    a = _sse_adapter([
        _chunk({"reasoning": "Ollama thinks."}),
        _chunk({"content": "Ok."}, "stop"),
    ])
    frames = await _drain(a.stream(system="", messages=_user(), tools=[]))
    assert [(f["type"], f.get("text")) for f in frames[:-1]] == [
        ("thinking", "Ollama thinks."), ("text", "Ok."),
    ]
    assert frames[-1]["response"].text == "Ok."


async def test_claude_cli_thinking_deltas_become_thinking_frames():
    events = [
        {"type": "stream_event", "event": {"type": "content_block_delta",
                                            "delta": {"type": "thinking_delta", "thinking": "hmm"}}},
        {"type": "stream_event", "event": {"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": "Hello"}}},
        {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": "hmm", "signature": "sig"},
            {"type": "text", "text": "Hello"},
        ]}},
        {"type": "stream_event", "event": {"type": "message_stop"}},
        {"type": "result", "subtype": "success", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]

    def runner(argv, stdin=None):
        return 0, "\n".join(json.dumps(e) for e in events), ""

    a = ClaudeCliAdapter(model="claude-opus-4-8", runner=runner, which=lambda b: "claude.exe")
    frames = await _drain(a.stream(system="", messages=_user(), tools=[]))
    assert [(f["type"], f.get("text")) for f in frames[:-1]] == [
        ("thinking", "hmm"), ("text", "Hello"),
    ]
    resp = frames[-1]["response"]
    assert resp.text == "Hello" and resp.thinking == "hmm"
