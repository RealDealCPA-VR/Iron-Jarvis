"""Local-model parity fixes (v1.70.0) — from the deep tools/connections review.

Pins the five fixes that give local endpoints frontier-grade treatment:
1. router.stream honors the STRICT PIN (it ignored it: a pinned-unavailable
   provider STREAMED the offline mock — a fabrication hole complete() never
   had — and a pinned pick with tools was silently swapped away).
2. Fleet endpoints join the failover universe (_snapshot/_first_capable/
   sideways loops saw only the static ladder — a healthy verified endpoint
   was invisible unless it was the configured default).
3. /fleet/nodes/{id}/verify now probes VISION too (a red-square image; a
   local llava node was stuck vision=False forever) and records the node as
   reachable (a green verify used to leave availability stale).
4. _consume_chat_stream falls back to parsing a plain JSON body when the
   server ignores `stream:true` (was: zero frames + empty final).
5. stream() retries once WITHOUT stream_options on a 400 from a non-hosted
   endpoint (some local gateways reject the field).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
)
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.router import ModelRouter

_MSG = [LLMMessage(role="user", content="q")]
_TOOL = [{"name": "web_search", "description": "", "input_schema": {"type": "object"}}]


class _NoTools(LLMAdapter):
    def __init__(self, provider="fleet-box", model="llama3"):
        self.provider, self.model = provider, model

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": False, "vision": False}

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text="local answer", tool_calls=[], usage={})


class _Capable(LLMAdapter):
    def __init__(self, provider="anthropic", model="claude-x"):
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text="frontier answer", tool_calls=[], usage={})


class _Mock(LLMAdapter):
    provider = "mock"
    model = "mock-1"

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text="fabricated", tool_calls=[], usage={})


class _Manager:
    def __init__(self, adapters, available=None, fleet=()):
        self.adapters = adapters
        self._available = available or set(adapters)
        self._fleet = list(fleet)

    def available(self, provider):
        return provider in self._available

    def has_available_api_provider(self):
        return any(p != "mock" for p in self._available)

    def runtime_provider_names(self):
        return list(self._fleet)

    def get(self, provider, model=None):
        return self.adapters[provider]


async def _collect(agen):
    frames = []
    async for f in agen:
        frames.append(f)
    return frames


# ------------------------------------------------------- 1. stream + pin ----


async def test_stream_pinned_unavailable_raises_never_streams_mock():
    manager = _Manager({"anthropic": _Capable(), "mock": _Mock()}, available={"anthropic"})
    r = ModelRouter(manager, "anthropic", EventBus(), strict_pin=lambda: True)
    with pytest.raises(ProviderError, match="strict model pin"):
        await _collect(
            r.stream(provider="fleet-gone", model=None, system="", messages=_MSG, tools=[])
        )


async def test_stream_pinned_pick_keeps_tools_no_swap():
    local = _NoTools()
    frontier = _Capable()
    manager = _Manager(
        {"fleet-box": local, "anthropic": frontier, "mock": _Mock()},
        available={"fleet-box", "anthropic"},
    )
    r = ModelRouter(manager, "anthropic", EventBus(), strict_pin=lambda: True)
    frames = await _collect(
        r.stream(provider="fleet-box", model="llama3", system="", messages=_MSG, tools=_TOOL)
    )
    final = next(f for f in frames if f.get("type") == "final")
    assert final["provider"] == "fleet-box"
    assert frontier.calls == 0


async def test_stream_pin_off_still_swaps_capability():
    local = _NoTools()
    frontier = _Capable()
    manager = _Manager(
        {"fleet-box": local, "anthropic": frontier, "mock": _Mock()},
        available={"fleet-box", "anthropic"},
    )
    r = ModelRouter(manager, "anthropic", EventBus(), strict_pin=lambda: False)
    frames = await _collect(
        r.stream(provider="fleet-box", model="llama3", system="", messages=_MSG, tools=_TOOL)
    )
    final = next(f for f in frames if f.get("type") == "final")
    assert final["provider"] == "anthropic"  # the pre-pin contract, unchanged


# ------------------------------------------- 2. fleet in the failover pool ---


async def test_fleet_endpoint_absorbs_failover():
    class _Boom(LLMAdapter):
        provider, model = "anthropic", "claude-x"

        async def complete(self, *, system, messages, tools):
            raise ProviderError("overloaded", transient=True)

    fleet = _Capable(provider="fleet-box", model="llama3")
    manager = _Manager(
        {"anthropic": _Boom(), "fleet-box": fleet, "mock": _Mock()},
        available={"anthropic", "fleet-box"},
        fleet=["fleet-box"],
    )
    r = ModelRouter(manager, "anthropic", EventBus())
    res = await r.complete(provider=None, model=None, system="", messages=_MSG, tools=[])
    assert res.provider == "fleet-box"  # was: invisible, request died on mock/raise
    assert fleet.calls == 1


def test_snapshot_includes_runtime_fleet_names():
    manager = _Manager(
        {"anthropic": _Capable(), "fleet-box": _Capable(provider="fleet-box")},
        available={"anthropic", "fleet-box"},
        fleet=["fleet-box"],
    )
    r = ModelRouter(manager, "anthropic", EventBus())
    assert "fleet-box" in r._snapshot()  # noqa: SLF001


# ---------------------------------------------------- 3. vision verify ------


def _client_with_node(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    node = client.post(
        "/fleet/nodes",
        json={"base_url": "http://127.0.0.1:9/v1", "label": "vlm", "routable": True},
    ).json()["node"]
    return client, node


class _ProbeAdapter:
    """Answers the ping-tool probe with a tool call, the vision probe by
    *color_answer* (or raises when color_answer is None)."""

    provider, model = "fleet-x", "llava"

    def __init__(self, color_answer):
        self._color = color_answer

    async def complete(self, *, system, messages, tools):
        if tools:  # the ping-tool probe
            return LLMResponse(
                text="", tool_calls=[ToolCall(id="1", name="ping", arguments={})]
            )
        if self._color is None:
            raise RuntimeError("image content not supported")
        assert messages[0].images, "vision probe must carry the image"
        return LLMResponse(text=self._color, tool_calls=[], usage={})


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("Red.", True), ("I cannot see images.", False)],
)
def test_verify_probes_vision_and_records(tmp_path, monkeypatch, answer, expected):
    client, node = _client_with_node(tmp_path)
    platform = client.app.state.platform
    monkeypatch.setattr(
        platform.providers, "get", lambda p, m=None: _ProbeAdapter(answer)
    )
    r = client.post(f"/fleet/nodes/{node['id']}/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool_use"] is True
    assert body["vision"] is expected
    assert body["node"]["vision"] is expected  # persisted on the node record


def test_verify_vision_error_stays_unknown_and_marks_reachable(tmp_path, monkeypatch):
    client, node = _client_with_node(tmp_path)
    platform = client.app.state.platform
    monkeypatch.setattr(
        platform.providers, "get", lambda p, m=None: _ProbeAdapter(None)
    )
    r = client.post(f"/fleet/nodes/{node['id']}/verify")
    body = r.json()
    assert body["vision"] is None and body["vision_error"]
    assert body["node"]["vision"] is None  # never permanently branded blind
    # The successful tool completion proved reachability — availability agrees.
    assert platform.fleet.reachable(f"fleet-{node['id']}") is True


# ------------------------------------- 4. non-SSE single-body fallback ------


class _PlainJSONResp:
    status_code = 200

    def __init__(self, payload):
        self._lines = json.dumps(payload, indent=2).splitlines()

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


async def test_stream_consumes_plain_json_body():
    adapter = OpenAIAdapter(model="m", base_url="http://local/v1", api_key=None)
    payload = {
        "choices": [
            {"message": {"content": "plain body answer"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    frames = await _collect(adapter._consume_chat_stream(_PlainJSONResp(payload)))  # noqa: SLF001
    assert frames[0] == {"type": "text", "text": "plain body answer"}
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"].text == "plain body answer"


# ----------------------------------- 5. stream_options 400 retry ------------


class _FakeStreamCM:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _SSEResp:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
        yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
        yield "data: [DONE]"


class _Rejecting400Resp:
    status_code = 400

    async def aread(self):
        return b'{"error":{"message":"unknown field stream_options"}}'

    def json(self):
        return {"error": {"message": "unknown field stream_options"}}

    @property
    def headers(self):
        return {}

    text = '{"error":{"message":"unknown field stream_options"}}'


async def test_stream_retries_without_stream_options_on_400():
    adapter = OpenAIAdapter(model="m", base_url="http://local/v1", api_key=None)
    seen_bodies: list[dict] = []

    class _Client:
        def stream(self, method, url, headers=None, json=None):
            seen_bodies.append(json)
            if "stream_options" in json:
                return _FakeStreamCM(_Rejecting400Resp())
            return _FakeStreamCM(_SSEResp())

    adapter._client = lambda: _Client()  # noqa: SLF001
    frames = await _collect(adapter.stream(system="", messages=_MSG, tools=[]))
    assert [f for f in frames if f["type"] == "text"], "retry must actually stream"
    assert "stream_options" in seen_bodies[0]
    assert "stream_options" not in seen_bodies[1]
