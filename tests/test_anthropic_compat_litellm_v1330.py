"""The Anthropic-compatible adapter against a LiteLLM proxy's REAL shapes (v1.330.0).

v1.329.0's ``AnthropicFleetAdapter`` was proven only against a fake server
written from the Anthropic docs. The user's own DGX Sparks run a LiteLLM proxy
that answers ``POST /v1/messages``; every reply body and SSE stream below was
RECORDED from it on 2026-10-10 (model ``glm``) and is replayed here offline
(``httpx.MockTransport``, no network) so CI keeps what the live run proved:

* message ids are ``chatcmpl-…`` (plain) and ``msg_<uuid>`` (stream), and
  ``content`` blocks carry an extra ``provider_specific_fields: null``;
* usage splits a cache hit OUT of ``input_tokens`` (42 + 128 cached = the 170
  an uncached call reports) and the stream puts the real counts on
  ``message_delta`` after a zeroed ``message_start``;
* a streamed reply opens with an EMPTY text block, then a ``thinking`` block
  (``signature: ""``), then the answer's text block;
* an error is ``{"error": {"message", "type": null, "param", "code": "400"}}``.

ONE REAL BUG: LiteLLM's proxy reports a failure that happens after the stream
opened as ``data: {"error": {...}}`` with no ``type`` and no ``event:`` line
(litellm/proxy ``async_sse_data_generator``, v1.72-v1.77 stable). The adapter
skipped it as an unknown event and then raised "stopped sending before the
reply was finished", so the proxy's own words ("upstream box unreachable")
were lost and the turn read as a dropped connection. It is now a typed
``ProviderError`` carrying the proxy's status (``code``) and message. That
shape was read from LiteLLM's source, not provoked live (the proxy answered
every bad request with an HTTP error before streaming).
"""

from __future__ import annotations

import json

import httpx
import pytest

from iron_jarvis.fleet.anthropic_compat import AnthropicFleetAdapter
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError

BASE = "http://spark-proxy.test:4000"
KEY = "sk-litellm-proxy-key-0123456789"

# --------------------------------------------------------------------------- #
# recorded from the user's LiteLLM proxy (2026-10-10, model "glm")
# --------------------------------------------------------------------------- #
PLAIN = {
    "id": "chatcmpl-9effaf05105a47d8aa17c4f7", "type": "message", "role": "assistant",
    "model": "glm", "stop_sequence": None, "usage": {"input_tokens": 18, "output_tokens": 7},
    "content": [{"type": "text", "text": "Hi there, friend!"}], "stop_reason": "end_turn",
}
TOOL_CACHED = {
    "id": "chatcmpl-94fe5da0408741669b50c5c2", "type": "message", "role": "assistant",
    "model": "glm", "stop_sequence": None,
    "usage": {"input_tokens": 42, "output_tokens": 12, "cache_read_input_tokens": 128},
    "content": [{"type": "tool_use", "id": "call_df5df8be3e874071be53303b", "name": "get_time",
                 "input": {"city": "Paris"}, "provider_specific_fields": None}],
    "stop_reason": "tool_use",
}
THINKING_ONLY = {
    "id": "chatcmpl-5ed2f494d3be4672b004a9f5", "type": "message", "role": "assistant",
    "model": "glm", "stop_sequence": None, "usage": {"input_tokens": 15, "output_tokens": 16},
    "content": [{"type": "thinking", "thinking": "Let me analyze this conversation start.",
                 "signature": None}],
    "stop_reason": "max_tokens",
}
ERROR_400 = {
    "error": {
        "message": "litellm.BadRequestError: Hosted_vllmException - {\"error\": {\"message\": "
        "\"This server's maximum context length is 1048576 tokens\", \"type\": "
        "\"invalid_request_error\"}}. Received Model Group=glm",
        "type": None, "param": None, "code": "400",
    }
}
_START = ('{"type": "message_start", "message": {"id": "msg_a7fdef99-e44b-4765-b720-7b0a6ea0f5f8", '
          '"type": "message", "role": "assistant", "content": [], "model": "GLM-5.3-Flash-EXL3", '
          '"stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 0, "output_tokens": 0, '
          '"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}')
STREAM_THINKING = [
    _START,
    '{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}',
    '{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ""}}',
    '{"type": "content_block_stop", "index": 0}',
    '{"type": "content_block_start", "index": 1, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}',
    '{"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": "Digits"}}',
    '{"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": " only."}}',
    '{"type": "content_block_stop", "index": 1}',
    '{"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}}',
    '{"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "1"}}',
    '{"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "\\n"}}',
    '{"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "2\\n3"}}',
    '{"type": "content_block_stop", "index": 2}',
    '{"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 22, "output_tokens": 17}}',
    '{"type": "message_stop"}',
]
STREAM_TOOL = [
    _START,
    '{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}',
    '{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ""}}',
    '{"type": "content_block_stop", "index": 0}',
    '{"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "call_71ff54044a5249cab94aa987", "name": "get_time", "input": {}}}',
    '{"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\\"city\\":\\"Paris\\"}"}}',
    '{"type": "content_block_stop", "index": 1}',
    '{"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"input_tokens": 42, "output_tokens": 12, "cache_read_input_tokens": 128}}',
    '{"type": "message_stop"}',
]


def _sse(payloads: list[str], *, events: bool = True) -> bytes:
    """SSE bytes the way the proxy writes them: ``event: <type>`` + ``data:``
    for a typed event; a bare ``data:`` line for LiteLLM's own error frame."""
    out: list[str] = []
    for p in payloads:
        kind = json.loads(p).get("type")
        if events and kind:
            out.append(f"event: {kind}")
        out.append(f"data: {p}")
        out.append("")
    return ("\n".join(out) + "\n").encode("utf-8")


def _litellm_error_frame(message: str, code: str = "500") -> str:
    """``json.dumps({"error": ProxyException.to_dict()})`` — no top-level type."""
    return json.dumps({"error": {"message": message, "type": "None", "param": "None", "code": code}})


def _adapter(respond) -> AnthropicFleetAdapter:
    node = FleetNode(id="spark", label="Spark proxy", base_url=BASE, source="user",
                     routable=True, default_model="glm", protocol="anthropic")
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False)
    return AnthropicFleetAdapter(node=node, credential=lambda: KEY, http=client)


def _json(body: dict, status: int = 200):
    return lambda r: httpx.Response(status, json=body)


def _stream_of(payloads: list[str], **kw):
    return lambda r: httpx.Response(
        200, content=_sse(payloads, **kw), headers={"content-type": "text/event-stream"}
    )


MSG = [LLMMessage(role="user", content="hi")]


async def _final(adapter: AnthropicFleetAdapter):
    frames = [f async for f in adapter.stream(system="", messages=MSG, tools=[])]
    return frames, frames[-1]["response"]


# --------------------------------------------------------------------------- #
# what the live run proved, kept offline
# --------------------------------------------------------------------------- #
async def test_recorded_plain_reply_parses():
    r = await _adapter(_json(PLAIN)).complete(system="", messages=MSG, tools=[])
    assert r.text == "Hi there, friend!"
    assert r.finish_reason == "stop"
    assert r.usage == {"input_tokens": 18, "output_tokens": 7}


async def test_recorded_tool_reply_counts_the_cached_prompt_in_the_total():
    r = await _adapter(_json(TOOL_CACHED)).complete(system="", messages=MSG, tools=[])
    assert [(c.id, c.name, c.arguments) for c in r.tool_calls] == [
        ("call_df5df8be3e874071be53303b", "get_time", {"city": "Paris"})
    ]
    assert r.finish_reason == "tool_use"
    # 42 uncached + 128 read from cache = the 170 an uncached call reports.
    assert r.usage["input_tokens"] == 170
    assert r.usage["cache_read_input_tokens"] == 128


async def test_recorded_thinking_only_reply_is_cut_off_not_a_blank_answer():
    r = await _adapter(_json(THINKING_ONLY)).complete(system="", messages=MSG, tools=[])
    assert r.text == ""
    assert r.thinking.startswith("Let me analyze")
    assert r.finish_reason == "max_tokens"


async def test_recorded_stream_with_thinking_keeps_answer_and_thinking_apart():
    frames, final = await _final(_adapter(_stream_of(STREAM_THINKING)))
    assert "".join(f["text"] for f in frames if f["type"] == "text") == "1\n2\n3"
    assert "".join(f["text"] for f in frames if f["type"] == "thinking") == "Digits only."
    assert final.text == "1\n2\n3" and final.thinking == "Digits only."
    assert final.finish_reason == "stop"
    assert final.usage["input_tokens"] == 22 and final.usage["output_tokens"] == 17


async def test_recorded_stream_tool_call_after_an_empty_text_block():
    frames, final = await _final(_adapter(_stream_of(STREAM_TOOL)))
    assert not [f for f in frames if f["type"] == "text"]  # the empty block says nothing
    assert [(c.id, c.name, c.arguments) for c in final.tool_calls] == [
        ("call_71ff54044a5249cab94aa987", "get_time", {"city": "Paris"})
    ]
    assert final.finish_reason == "tool_use"
    assert final.usage["input_tokens"] == 170 and final.usage["cache_read_input_tokens"] == 128


async def test_recorded_http_400_names_the_proxys_reason():
    with pytest.raises(ProviderError) as ei:
        await _adapter(_json(ERROR_400, 400)).complete(system="", messages=MSG, tools=[])
    assert ei.value.status_code == 400 and ei.value.transient is False
    assert "maximum context length" in str(ei.value)


# --------------------------------------------------------------------------- #
# the bug: LiteLLM's in-stream error frame (no "type", no "event:" line)
# --------------------------------------------------------------------------- #
async def test_litellm_error_frame_mid_stream_raises_the_proxys_own_words():
    payloads = STREAM_THINKING[:10] + [
        _litellm_error_frame("litellm.APIConnectionError: upstream GPU box unreachable")
    ]
    with pytest.raises(ProviderError) as ei:
        await _final(_adapter(_stream_of(payloads)))
    assert ei.value.status_code == 500
    assert "upstream GPU box unreachable" in str(ei.value)
    assert str(ei.value).startswith("fleet-spark API error 500")


async def test_litellm_error_frame_before_any_event_is_not_a_dropped_connection():
    payloads = [_litellm_error_frame("Hosted_vllmException - connection refused")]
    try:
        await _final(_adapter(_stream_of(payloads)))
    except httpx.RemoteProtocolError as exc:  # the v1.329.0 reading
        pytest.fail(f"read as a dropped connection: {exc}")
    except ProviderError as exc:
        assert exc.status_code == 500 and "connection refused" in str(exc)
    else:
        pytest.fail("an error frame produced a reply")


async def test_litellm_error_code_is_the_status_and_429_stays_transient():
    payloads = STREAM_TOOL[:2] + [_litellm_error_frame("rate limited by the proxy", code="429")]
    with pytest.raises(ProviderError) as ei:
        await _final(_adapter(_stream_of(payloads)))
    assert ei.value.status_code == 429 and ei.value.transient is True


async def test_litellm_error_frame_never_echoes_the_key():
    payloads = [_litellm_error_frame(f"bad key {KEY}", code="401")]
    with pytest.raises(ProviderError) as ei:
        await _final(_adapter(_stream_of(payloads)))
    assert KEY not in str(ei.value) and "[key hidden]" in str(ei.value)
    assert ei.value.status_code == 401


async def test_hosted_error_event_still_maps_by_its_type():
    payloads = STREAM_THINKING[:3] + [
        json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    ]
    with pytest.raises(ProviderError) as ei:
        await _final(_adapter(_stream_of(payloads)))
    assert ei.value.status_code == 529


async def test_a_200_body_that_carries_an_error_reports_its_real_status_without_the_key():
    body = {"error": {"message": f"upstream down (key {KEY})", "type": None, "param": None, "code": "503"}}
    with pytest.raises(ProviderError) as ei:
        await _adapter(_json(body)).complete(system="", messages=MSG, tools=[])
    assert ei.value.status_code == 503 and ei.value.transient is True
    assert "API error 200" not in str(ei.value)
    assert KEY not in str(ei.value)
