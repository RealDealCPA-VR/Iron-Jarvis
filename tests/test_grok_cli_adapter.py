"""Offline tests for the Grok CLI adapter (mocked httpx transport, no network).

The Grok proxy speaks the OpenAI Responses API over SSE. The fake ``http``
client returns a canned SSE ``text`` body so request shaping AND SSE parsing are
exercised without a socket.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall
from iron_jarvis.providers.adapters.grok_cli import GrokCliAdapter

GOOD_SESSION = {
    "token": "tok-abc",
    "base_url": "https://cli-chat-proxy.grok.com/v1",
    "expires_at": "2999-01-01T00:00:00Z",
    "version": "0.2.82",
}


def _sse(events: list[dict]) -> str:
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
    )


def _completed(output: list[dict], usage: dict | None = None) -> str:
    return _sse(
        [
            {"type": "response.created", "response": {}},
            {
                "type": "response.completed",
                "response": {"output": output, "usage": usage or {}},
            },
        ]
    )


class FakeResponse:
    def __init__(self, text: str, status: int = 200, payload=None) -> None:
        self.text = text
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHTTP:
    def __init__(self, resp: FakeResponse) -> None:
        self._resp = resp
        self.calls: list[dict] = []

    async def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self._resp

    @property
    def last(self) -> dict:
        return self.calls[-1]


SAMPLE_TOOLS = [
    {
        "name": "write_file",
        "description": "Write a file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
]


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
async def test_text_response_and_request_shape():
    http = FakeHTTP(
        FakeResponse(
            _completed(
                [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Hi there"}],
                    },
                ],
                usage={"input_tokens": 12, "output_tokens": 3},
            )
        )
    )
    adapter = GrokCliAdapter(
        model="grok-build", http=http, session_provider=lambda: dict(GOOD_SESSION)
    )
    res = await adapter.complete(
        system="Be terse.",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[],
    )
    # request shaping
    assert http.last["url"] == (
        "https://cli-chat-proxy.grok.com/v1/responses"
    )
    assert http.last["headers"]["Authorization"] == "Bearer tok-abc"
    assert http.last["headers"]["x-grok-client-version"] == "0.2.82"
    assert http.last["headers"]["x-grok-client-identifier"] == "grok-shell"
    assert http.last["headers"]["Accept"] == "text/event-stream"
    body = http.last["json"]
    assert body["model"] == "grok-build"
    assert body["stream"] is True and body["store"] is False
    # system + user become string-content message items
    assert body["input"][0] == {
        "type": "message", "role": "system", "content": "Be terse.",
    }
    assert body["input"][1] == {
        "type": "message", "role": "user", "content": "hi",
    }
    # response parsing
    assert res.text == "Hi there"
    assert res.finish_reason == "stop"
    assert res.usage == {"input_tokens": 12, "output_tokens": 3}
    assert not res.wants_tools


async def test_tool_call_response_and_tool_shape():
    http = FakeHTTP(
        FakeResponse(
            _completed(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_9",
                        "name": "write_file",
                        "arguments": json.dumps({"path": "a.txt"}),
                    }
                ]
            )
        )
    )
    adapter = GrokCliAdapter(
        http=http, session_provider=lambda: dict(GOOD_SESSION)
    )
    res = await adapter.complete(
        system="",
        messages=[LLMMessage(role="user", content="write it")],
        tools=SAMPLE_TOOLS,
    )
    # FLAT responses tool shape (no nested "function")
    sent = http.last["json"]["tools"][0]
    assert sent["type"] == "function"
    assert sent["name"] == "write_file"
    assert sent["parameters"] == SAMPLE_TOOLS[0]["input_schema"]
    assert http.last["json"]["tool_choice"] == "auto"
    # no system item when system is empty
    assert http.last["json"]["input"][0]["role"] == "user"
    # parsed tool call
    assert res.finish_reason == "tool_use"
    assert res.wants_tools
    tc = res.tool_calls[0]
    assert tc.id == "call_9"
    assert tc.name == "write_file"
    assert tc.arguments == {"path": "a.txt"}


async def test_replays_assistant_tool_calls_and_results():
    http = FakeHTTP(
        FakeResponse(_completed([{"type": "message", "role": "assistant",
                                  "content": [{"type": "output_text", "text": "ok"}]}]))
    )
    adapter = GrokCliAdapter(http=http, session_provider=lambda: dict(GOOD_SESSION))
    await adapter.complete(
        system="",
        messages=[
            LLMMessage(role="user", content="go"),
            LLMMessage(
                role="assistant",
                tool_calls=[ToolCall("c1", "write_file", {"path": "a"})],
            ),
            LLMMessage(role="tool", tool_call_id="c1", name="write_file",
                       content="done"),
        ],
        tools=SAMPLE_TOOLS,
    )
    items = http.last["json"]["input"]
    fc = next(i for i in items if i.get("type") == "function_call")
    assert fc["call_id"] == "c1" and fc["name"] == "write_file"
    fco = next(i for i in items if i.get("type") == "function_call_output")
    assert fco == {"type": "function_call_output", "call_id": "c1", "output": "done"}


# --------------------------------------------------------------------------- #
# error paths
# --------------------------------------------------------------------------- #
async def test_missing_session_raises():
    adapter = GrokCliAdapter(http=FakeHTTP(FakeResponse("")),
                             session_provider=lambda: None)
    with pytest.raises(RuntimeError, match="grok login"):
        await adapter.complete(system="", messages=[], tools=[])


async def test_expired_session_raises():
    expired = {**GOOD_SESSION, "expires_at": "2000-01-01T00:00:00Z"}
    adapter = GrokCliAdapter(http=FakeHTTP(FakeResponse("")),
                             session_provider=lambda: expired)
    with pytest.raises(RuntimeError, match="expired"):
        await adapter.complete(system="", messages=[], tools=[])


async def test_http_error_raises_loudly():
    http = FakeHTTP(
        FakeResponse(
            "", status=426,
            payload={"error": "Your Grok CLI version (none) is outdated."},
        )
    )
    adapter = GrokCliAdapter(http=http, session_provider=lambda: dict(GOOD_SESSION))
    with pytest.raises(RuntimeError, match="426"):
        await adapter.complete(
            system="", messages=[LLMMessage("user", "hi")], tools=[]
        )


async def test_stream_without_completed_raises():
    http = FakeHTTP(FakeResponse(_sse([{"type": "response.created",
                                        "response": {}}])))
    adapter = GrokCliAdapter(http=http, session_provider=lambda: dict(GOOD_SESSION))
    with pytest.raises(RuntimeError, match="response.completed"):
        await adapter.complete(
            system="", messages=[LLMMessage("user", "hi")], tools=[]
        )
