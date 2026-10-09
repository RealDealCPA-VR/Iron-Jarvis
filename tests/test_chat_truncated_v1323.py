"""v1.323.0 (wave B) — a reply the provider CUT OFF says so.

Every adapter now maps its "ran out of output tokens" stop to
``finish_reason="max_tokens"``: Anthropic ``stop_reason: max_tokens``, OpenAI
chat ``finish_reason: length``, the Responses API ``status: incomplete`` with
``incomplete_details.reason: max_output_tokens``, Gemini ``finishReason:
MAX_TOKENS`` (claude-cli already did). Both chat lanes carry ``truncated`` —
the stream lane on ``done``, POST /chat in its response — ALWAYS present, and
true iff the FINAL answering model call ended that way. A tool round is never
a truncation, and a test double whose response has no ``finish_reason`` reads
as not truncated.

Driven through the REAL app; the adapters through their own existing seams
(a fake SDK client, an ``httpx.MockTransport``, a fake HTTP object).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.google import GoogleAdapter
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult


def _done(text: str) -> dict:
    frames = [
        json.loads(b.split("data:", 1)[1])
        for b in text.split("\n\n")
        if b.startswith("event: done\n")
    ]
    assert frames, text[-800:]
    return frames[-1]


async def _drain(agen) -> list[dict]:
    return [f async for f in agen]


def _user() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="q")]


# --------------------------------------------------------------------------- #
# 1. the lanes
# --------------------------------------------------------------------------- #


def _stream_client(tmp_path, monkeypatch, finals):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    queue = list(finals)

    async def fake_stream(**kw):
        resp = queue.pop(0)
        if getattr(resp, "text", ""):
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    async def _invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        return ToolResult(ok=True, output="ok")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.registry, "invoke", _invoke)
    return client


def _post_client(tmp_path, monkeypatch, finals):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    queue = list(finals)

    async def fake_complete(**kw):
        return RouteResult(queue.pop(0), "mock", "mock")

    async def _invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        return ToolResult(ok=True, output="ok")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _invoke)
    return client


_ASK = {"messages": [{"role": "user", "content": "write it all"}]}
_ASK_TOOLS = {**_ASK, "tools": ["image_info"]}


def _cut() -> LLMResponse:
    return LLMResponse(text="The first half of the ans", finish_reason="max_tokens", usage={})


def _whole() -> LLMResponse:
    return LLMResponse(text="The whole answer.", finish_reason="stop", usage={})


def _tool_round(finish: str = "tool_use") -> LLMResponse:
    call = ToolCall(id="c1", name="image_info", arguments={"path": "a.png"})
    return LLMResponse(text="", tool_calls=[call], finish_reason=finish, usage={})


def test_stream_done_says_truncated_when_the_answer_ran_out_of_tokens(tmp_path, monkeypatch):
    r = _stream_client(tmp_path, monkeypatch, [_cut()]).post("/chat/stream", json=_ASK)
    done = _done(r.text)
    assert done["truncated"] is True
    assert done["reply"].startswith("The first half of the ans")


def test_stream_done_truncated_is_false_and_always_present(tmp_path, monkeypatch):
    r = _stream_client(tmp_path, monkeypatch, [_whole()]).post("/chat/stream", json=_ASK)
    assert _done(r.text)["truncated"] is False


def test_post_says_truncated_when_the_answer_ran_out_of_tokens(tmp_path, monkeypatch):
    body = _post_client(tmp_path, monkeypatch, [_cut()]).post("/chat", json=_ASK).json()
    assert body["truncated"] is True


def test_post_truncated_is_false_and_always_present(tmp_path, monkeypatch):
    body = _post_client(tmp_path, monkeypatch, [_whole()]).post("/chat", json=_ASK).json()
    assert body["truncated"] is False


def test_only_the_final_answering_call_counts_in_both_lanes(tmp_path, monkeypatch):
    # A tool round that (oddly) reports max_tokens, then a whole answer: the
    # FINAL call decides — not truncated.
    s = _stream_client(tmp_path / "s", monkeypatch, [_tool_round("max_tokens"), _whole()])
    assert _done(s.post("/chat/stream", json=_ASK_TOOLS).text)["truncated"] is False
    p = _post_client(tmp_path / "p", monkeypatch, [_tool_round("max_tokens"), _whole()])
    assert p.post("/chat", json=_ASK_TOOLS).json()["truncated"] is False
    # A whole tool round, then a cut answer: truncated.
    s2 = _stream_client(tmp_path / "s2", monkeypatch, [_tool_round(), _cut()])
    assert _done(s2.post("/chat/stream", json=_ASK_TOOLS).text)["truncated"] is True
    p2 = _post_client(tmp_path / "p2", monkeypatch, [_tool_round(), _cut()])
    assert p2.post("/chat", json=_ASK_TOOLS).json()["truncated"] is True


def _spent_thinking() -> LLMResponse:
    """A call that spent every output token before writing a word."""
    return LLMResponse(text="", finish_reason="max_tokens", usage={})


def test_the_final_answer_nudge_is_the_final_answering_call(tmp_path, monkeypatch):
    # Round 0 ran out of tokens and wrote nothing; the nudge answered whole.
    s = _stream_client(tmp_path / "s", monkeypatch, [_spent_thinking(), _whole()])
    done = _done(s.post("/chat/stream", json=_ASK).text)
    assert done["reply"].startswith("The whole answer.") and done["truncated"] is False
    p = _post_client(tmp_path / "p", monkeypatch, [_spent_thinking(), _whole()])
    body = p.post("/chat", json=_ASK).json()
    assert body["reply"].startswith("The whole answer.") and body["truncated"] is False
    # Round 0 wrote nothing; the NUDGE was cut off: truncated.
    empty = LLMResponse(text="", finish_reason="stop", usage={})
    s2 = _stream_client(tmp_path / "s2", monkeypatch, [empty, _cut()])
    assert _done(s2.post("/chat/stream", json=_ASK).text)["truncated"] is True
    p2 = _post_client(tmp_path / "p2", monkeypatch, [empty, _cut()])
    assert p2.post("/chat", json=_ASK).json()["truncated"] is True


def test_a_tool_call_is_never_a_truncation():
    from iron_jarvis.daemon.chat_turn import _truncated_by

    assert _truncated_by(_tool_round("max_tokens")) is False
    assert _truncated_by(_cut()) is True
    assert _truncated_by(_whole()) is False
    assert _truncated_by(None) is False


def test_a_double_without_finish_reason_reads_as_not_truncated(tmp_path, monkeypatch):
    bare = SimpleNamespace(text="Hi.", tool_calls=[], usage={})
    s = _stream_client(tmp_path / "s", monkeypatch, [bare])
    assert _done(s.post("/chat/stream", json=_ASK).text)["truncated"] is False
    p = _post_client(tmp_path / "p", monkeypatch, [bare])
    assert p.post("/chat", json=_ASK).json()["truncated"] is False


# --------------------------------------------------------------------------- #
# 2. the adapters map their own words to max_tokens
# --------------------------------------------------------------------------- #


class _AnthropicMessages:
    def __init__(self, stop_reason: str, content):
        self.stop_reason, self.content = stop_reason, content

    def _msg(self):
        return SimpleNamespace(
            content=self.content, stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=1, output_tokens=2),
        )

    async def create(self, **kw):
        return self._msg()

    def stream(self, **kw):
        outer = self

        class _S:
            def __aiter__(self):
                async def gen():
                    yield SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(type="text_delta", text="cut"),
                    )
                return gen()

            async def get_final_message(self):
                return outer._msg()

        @asynccontextmanager
        async def cm():
            yield _S()

        return cm()


def _anthropic(monkeypatch, stop_reason: str, content=None) -> AnthropicAdapter:
    a = AnthropicAdapter(model="claude-opus-4-8", api_key="sk-ant-test")
    content = content or [SimpleNamespace(type="text", text="cut")]
    monkeypatch.setattr(
        a, "_client", lambda: SimpleNamespace(messages=_AnthropicMessages(stop_reason, content))
    )
    return a


async def test_anthropic_max_tokens_is_max_tokens_in_complete_and_stream(monkeypatch):
    a = _anthropic(monkeypatch, "max_tokens")
    assert (await a.complete(system="", messages=_user(), tools=[])).finish_reason == "max_tokens"
    frames = await _drain(a.stream(system="", messages=_user(), tools=[]))
    assert frames[-1]["response"].finish_reason == "max_tokens"
    # The ordinary endings keep their words.
    b = _anthropic(monkeypatch, "end_turn")
    assert (await b.complete(system="", messages=_user(), tools=[])).finish_reason == "stop"
    tool = SimpleNamespace(type="tool_use", id="t1", name="read_file", input={"path": "a"})
    c = _anthropic(monkeypatch, "tool_use", [tool])
    assert (await c.complete(system="", messages=_user(), tools=[])).finish_reason == "tool_use"


def _openai(handler) -> OpenAIAdapter:
    return OpenAIAdapter(
        "some/model",
        api_key="sk-test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://openrouter.test/api/v1/chat/completions",
        provider_name="openrouter",
    )


async def test_openai_chat_length_is_max_tokens_streamed_and_not():
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "cut"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def stream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)

    frames = await _drain(_openai(stream_handler).stream(system="", messages=_user(), tools=[]))
    assert frames[-1]["response"].finish_reason == "max_tokens"

    def json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "cut"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    resp = await _openai(json_handler).complete(system="", messages=_user(), tools=[])
    assert resp.finish_reason == "max_tokens" and resp.text == "cut"


def test_openai_responses_incomplete_for_tokens_is_max_tokens():
    def sse(*events) -> str:
        return "\n".join(f"data: {json.dumps(e)}" for e in events)

    item = {"type": "message", "content": [{"type": "output_text", "text": "cut"}]}
    cut = OpenAIAdapter._parse_sse(sse({"type": "response.incomplete", "response": {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [item],
    }}))
    assert cut.finish_reason == "max_tokens" and cut.text == "cut"
    whole = OpenAIAdapter._parse_sse(sse({"type": "response.completed", "response": {
        "status": "completed", "output": [item],
    }}))
    assert whole.finish_reason == "stop"


class _PostHTTP:
    def __init__(self, data):
        self.data = data

    async def post(self, url, headers=None, json=None):
        return SimpleNamespace(status_code=200, json=lambda: self.data, text="")


async def test_google_max_tokens_is_max_tokens_but_a_call_is_tool_use():
    cut = _PostHTTP({"candidates": [
        {"content": {"parts": [{"text": "cut"}]}, "finishReason": "MAX_TOKENS"},
    ]})
    resp = await GoogleAdapter(api_key="g", http=cut).complete(system="", messages=_user(), tools=[])
    assert resp.finish_reason == "max_tokens" and resp.text == "cut"
    call = _PostHTTP({"candidates": [{
        "content": {"parts": [{"functionCall": {"name": "write_file"}}]},
        "finishReason": "MAX_TOKENS",
    }]})
    resp2 = await GoogleAdapter(api_key="g", http=call).complete(system="", messages=_user(), tools=[])
    assert resp2.finish_reason == "tool_use"
