"""v1.455.0 — a streamed reply from the user's OWN model server waits out a
buffered tool call (a USER BUG).

The owner's chat on their LiteLLM proxy (glm-5.3-flash under vLLM) failed
twice the same way: the model said "Building the reconciliation workbook
now.", started one big ``write_document`` call, and vLLM's GLM tool parser
held the call's arguments back until the call was complete — the stream sent
NOTHING for well over a minute, and the OpenAI adapter's client (``timeout=
60.0``) ended it: ``ReadTimeout``, partial.

These pins drive the REAL adapters against a REAL local stand-in server
(``tests/fixtures/fake_openai_endpoint_v1387.py``, ``Reply.pause_s``) that
sends a few tokens, goes silent, then finishes. Time is scaled down: the
client's base timeout and ``reply_budget.LOCAL_STREAM_READ_S`` are patched
small, the silence sits between them.

RED on the code before v1.455.0: the streamed request carried no timeout of
its own, so the client's base read timeout ended the silent stream.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from iron_jarvis.providers import reply_budget
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.reply_budget import ReplyBudget
from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, Reply

MODEL = "glm-5.3-flash"
WRITE_DOC = {
    "name": "write_document",
    "description": "Write a document.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "sheets": {"type": "object"}},
        "required": ["path"],
    },
}
_MSG = [LLMMessage(role="user", content="create a reconciliation excel")]


def _workbook_call() -> Reply:
    args = json.dumps({"path": "recon.xlsx", "sheets": {"Recon": [["a", 1]] * 200}})
    return Reply(
        thinking="Write via write_document with sheets dict.",
        text="Building the reconciliation workbook now.",
        calls=[("write_document", args)],
        finish="tool_calls",
        pause_s=1.5,
    )


def _local_adapter(url: str, *, base_read: float) -> OpenAIAdapter:
    """The adapter the provider manager builds for the ``custom`` slot (a
    reply budget), on a client whose base timeouts are ``base_read`` — the
    real one's 60 seconds, scaled down."""
    return OpenAIAdapter(
        model=MODEL,
        base_url=f"{url}/v1/chat/completions",
        provider_name="custom",
        reply_budget=ReplyBudget("custom", url),
        http=httpx.AsyncClient(timeout=base_read),
    )


async def _final(adapter) -> tuple[list[dict], dict | None]:
    frames: list[dict] = []
    final = None
    async for frame in adapter.stream(system="You are Jarvis.", messages=_MSG, tools=[WRITE_DOC]):
        frames.append(frame)
        if frame.get("type") == "final":
            final = frame["response"]
    return frames, final


async def test_a_buffered_tool_call_is_waited_out_on_the_users_model_server(monkeypatch):
    """The owner's two failures, replayed: tokens, ~1.5 s of silence (the
    buffered call), then the whole call. The client's base read is 0.5 s (the
    old 60 s); the streamed request's own read bound (5 s here, 600 s live)
    carries it through. RED before v1.455.0: ``httpx.ReadTimeout``."""
    monkeypatch.setattr(reply_budget, "LOCAL_STREAM_READ_S", 5.0)
    with FakeEndpoint(lambda body: _workbook_call()) as srv:
        adapter = _local_adapter(srv.url, base_read=0.5)
        frames, final = await _final(adapter)
    assert any(f.get("type") == "text" for f in frames), "the words before the silence arrived"
    assert final is not None
    assert [c.name for c in final.tool_calls] == ["write_document"]
    assert final.tool_calls[0].arguments["path"] == "recon.xlsx"


async def test_the_long_read_is_the_constant_not_a_fixed_number(monkeypatch):
    """The bound is ``LOCAL_STREAM_READ_S``, read at call time: a silence
    longer than it still ends the stream (an honest timeout, never a hang)."""
    monkeypatch.setattr(reply_budget, "LOCAL_STREAM_READ_S", 0.3)
    with FakeEndpoint(lambda body: _workbook_call()) as srv:
        adapter = _local_adapter(srv.url, base_read=60.0)
        with pytest.raises(httpx.ReadTimeout):
            await _final(adapter)


async def test_stop_during_the_silence_ends_the_stream_at_once(monkeypatch):
    """A cancel (the user's Stop) during a long silent gap ends the read
    promptly — the long bound never makes Stop wait."""
    monkeypatch.setattr(reply_budget, "LOCAL_STREAM_READ_S", 600.0)

    def _long_silence(body):
        r = _workbook_call()
        r.pause_s = 30.0
        return r

    with FakeEndpoint(_long_silence) as srv:
        adapter = _local_adapter(srv.url, base_read=60.0)
        got_text = asyncio.Event()

        async def _run():
            async for frame in adapter.stream(system="", messages=_MSG, tools=[WRITE_DOC]):
                if frame.get("type") == "text":
                    got_text.set()

        task = asyncio.create_task(_run())
        await asyncio.wait_for(got_text.wait(), 10)
        t0 = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - t0 < 2.0


async def test_only_the_streamed_read_of_a_budgeted_endpoint_is_widened():
    """Connect, write and pool stay 60 s; the read is the long bound; a
    hosted provider (no reply budget) is byte-identical — no timeout of its
    own, so the client's 60 s; and a NOT-streamed local request keeps 60 s."""
    seen: list[dict] = []
    sse = (
        'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions.get("timeout") or {}))
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
        })

    def client():
        return httpx.AsyncClient(timeout=60.0, transport=httpx.MockTransport(handler))

    local = OpenAIAdapter(model=MODEL, base_url="http://box.local:4000/v1/chat/completions",
                          provider_name="custom", reply_budget=ReplyBudget("custom", "x"),
                          http=client())
    hosted = OpenAIAdapter(model="gpt-x", api_key="k", http=client())
    async for _ in local.stream(system="", messages=_MSG, tools=[]):
        pass
    async for _ in hosted.stream(system="", messages=_MSG, tools=[]):
        pass
    await local.complete(system="", messages=_MSG, tools=[])
    local_stream, hosted_stream, local_post = seen
    assert local_stream == {"connect": 60.0, "read": reply_budget.LOCAL_STREAM_READ_S,
                            "write": 60.0, "pool": 60.0}
    assert reply_budget.LOCAL_STREAM_READ_S == 600.0
    assert hosted_stream == {"connect": 60.0, "read": 60.0, "write": 60.0, "pool": 60.0}
    assert local_post["read"] == 60.0


async def test_an_anthropic_compatible_node_widens_its_streamed_read_too():
    """``fleet/anthropic_compat`` had the same 60 s stream read."""
    from iron_jarvis.fleet.anthropic_compat import AnthropicFleetAdapter
    from iron_jarvis.fleet.registry import FleetNode

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions.get("timeout") or {}))
        return httpx.Response(200, json={
            "id": "m1", "type": "message", "role": "assistant", "model": "glm",
            "content": [{"type": "text", "text": "Hello."}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })

    node = FleetNode(id="spark", label="Spark", base_url="http://100.64.0.7:4000",
                     source="user", routable=True, default_model="glm", protocol="anthropic")
    adapter = AnthropicFleetAdapter(
        node=node, credential=lambda: "k",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    async for _ in adapter.stream(system="", messages=_MSG, tools=[]):
        pass
    assert seen[-1]["read"] == reply_budget.LOCAL_STREAM_READ_S
    assert seen[-1]["connect"] == 60.0
