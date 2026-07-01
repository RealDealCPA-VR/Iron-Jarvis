"""OpenAI adapter — ChatGPT (Codex) backend mode, fully offline.

A ChatGPT-account OAuth token (a JWT, not an ``sk-`` key) is NOT accepted by
api.openai.com; the adapter must route it to
``chatgpt.com/backend-api/codex/responses`` with the ``chatgpt-account-id``
header, a Responses-API body, and parse the SSE stream. API keys must keep
using Chat Completions unchanged.
"""

from __future__ import annotations

import base64
import json

from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall
from iron_jarvis.providers.adapters.openai import (
    _CHATGPT_ENDPOINT,
    _ENDPOINT,
    OpenAIAdapter,
)


# --- fakes -----------------------------------------------------------------


class FakeResp:
    def __init__(self, text="", status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class FakeHTTP:
    """Async ``post`` recorder returning a queue of canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self.responses.pop(0)

    @property
    def last(self):
        return self.calls[-1]


def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{seg}.sig"


#: A ChatGPT access token carrying the account-id claim the backend requires.
_TOKEN = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}})


def _sse(events: list[dict]) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def _completed(output, usage=None) -> dict:
    return {
        "type": "response.completed",
        "response": {"output": output, "usage": usage or {"input_tokens": 12, "output_tokens": 5}},
    }


_TEXT_SSE = _sse(
    [
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "Hel"},
        _completed(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello from codex"}],
                }
            ]
        ),
    ]
)


# --- routing ----------------------------------------------------------------


async def test_chatgpt_token_routes_to_codex_backend():
    http = FakeHTTP([FakeResp(text=_TEXT_SSE)])
    adapter = OpenAIAdapter(model="gpt-5-codex", api_key=_TOKEN, http=http)
    out = await adapter.complete(system="be brief", messages=[LLMMessage(role="user", content="hi")], tools=[])

    assert http.last["url"] == _CHATGPT_ENDPOINT
    h = http.last["headers"]
    assert h["Authorization"] == f"Bearer {_TOKEN}"
    assert h["chatgpt-account-id"] == "acct_123"  # from the JWT claim
    assert h["OpenAI-Beta"] == "responses=experimental"
    body = http.last["json"]
    assert body["store"] is False and body["stream"] is True
    assert body["instructions"] == "be brief"
    assert body["input"][0]["role"] == "user"
    assert out.text == "Hello from codex"
    assert out.usage == {"input_tokens": 12, "output_tokens": 5}


async def test_api_key_still_uses_chat_completions():
    http = FakeHTTP(
        [FakeResp(json_data={"choices": [{"message": {"content": "hi"}}], "usage": {}})]
    )
    adapter = OpenAIAdapter(model="gpt-4o-mini", api_key="sk-proj-abc", http=http)
    out = await adapter.complete(system="", messages=[LLMMessage(role="user", content="x")], tools=[])
    assert http.last["url"] == _ENDPOINT
    assert out.text == "hi"


async def test_incompatible_model_mapped_to_codex_default():
    http = FakeHTTP([FakeResp(text=_TEXT_SSE)])
    adapter = OpenAIAdapter(model="gpt-4o-mini", api_key=_TOKEN, http=http)
    await adapter.complete(system="", messages=[LLMMessage(role="user", content="x")], tools=[])
    # gpt-4o-mini isn't served by the Codex backend -> mapped, not 404'd.
    assert http.last["json"]["model"] == "gpt-5-codex"


async def test_token_without_account_claim_raises_actionable_error():
    bad = _jwt({"sub": "user"})  # no https://api.openai.com/auth claim
    adapter = OpenAIAdapter(model="gpt-5-codex", api_key=bad, http=FakeHTTP([]))
    try:
        await adapter.complete(system="", messages=[LLMMessage(role="user", content="x")], tools=[])
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "chatgpt_account_id" in str(exc)


# --- tools ------------------------------------------------------------------


async def test_tool_calls_parsed_and_replayed():
    call_sse = _sse(
        [
            _completed(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "read_file",
                        "arguments": json.dumps({"path": "a.txt"}),
                    }
                ]
            )
        ]
    )
    http = FakeHTTP([FakeResp(text=call_sse), FakeResp(text=_TEXT_SSE)])
    adapter = OpenAIAdapter(model="gpt-5-codex", api_key=_TOKEN, http=http)

    first = await adapter.complete(
        system="",
        messages=[LLMMessage(role="user", content="read a.txt")],
        tools=[{"name": "read_file", "description": "", "input_schema": {"type": "object"}}],
    )
    assert first.finish_reason == "tool_use"
    assert first.tool_calls[0].name == "read_file"
    assert first.tool_calls[0].arguments == {"path": "a.txt"}
    # Responses tools are FLAT (no nested "function" wrapper).
    sent_tool = http.calls[0]["json"]["tools"][0]
    assert sent_tool["type"] == "function" and sent_tool["name"] == "read_file"

    # Replay the assistant call + tool result on the next turn.
    await adapter.complete(
        system="",
        messages=[
            LLMMessage(role="user", content="read a.txt"),
            LLMMessage(role="assistant", content="", tool_calls=list(first.tool_calls)),
            LLMMessage(role="tool", content="file body", tool_call_id="call_1"),
        ],
        tools=[],
    )
    items = http.last["json"]["input"]
    kinds = [i["type"] for i in items]
    assert "function_call" in kinds  # the assistant's call is replayed...
    fco = next(i for i in items if i["type"] == "function_call_output")
    assert fco["call_id"] == "call_1" and fco["output"] == "file body"


# --- instructions-validation self-heal ---------------------------------------


async def test_invalid_instructions_retries_with_developer_message():
    http = FakeHTTP(
        [
            FakeResp(
                status_code=400,
                json_data={"error": {"message": "Instructions are not valid"}},
            ),
            FakeResp(text=_TEXT_SSE),
        ]
    )
    adapter = OpenAIAdapter(model="gpt-5-codex", api_key=_TOKEN, http=http)
    out = await adapter.complete(
        system="my system prompt", messages=[LLMMessage(role="user", content="x")], tools=[]
    )
    assert out.text == "Hello from codex"
    retry = http.calls[1]["json"]
    assert retry["instructions"] == ""  # backend rejected custom instructions
    dev = retry["input"][0]
    assert dev["role"] == "developer"  # system prompt moved into the input
    assert dev["content"][0]["text"] == "my system prompt"
