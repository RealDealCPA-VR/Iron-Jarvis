"""Wave C (v1.203.0) C1 — guided-decoding adapter kwargs, fully offline.

Additive keyword-only params on ``LLMAdapter.complete``/``stream``:
``response_format`` / ``tool_choice`` / ``extra_body``. The contract pinned
here:

* default ``None`` ⇒ the request body is BYTE-IDENTICAL to the pre-v1.203.0
  wire payload (captured literal pins, complete + stream lanes);
* the openai-compat family (openai/ollama/custom base_urls) forwards them:
  ``response_format`` under ``"response_format"``, ``tool_choice`` under
  ``"tool_choice"``, and ``extra_body`` merged LAST so its keys WIN any
  clash — the vLLM guided_json / llama.cpp grammar escape hatch (IronCore's
  rationale, ported verbatim);
* the ChatGPT-account (subscription) backend ACCEPTS and DROPS them;
* every other adapter (anthropic/google/CLIs/mock/prompted-tools) ACCEPTS
  and IGNORES them without error or wire change, so a caller can pass them
  uniformly without knowing the adapter class;
* the base default ``stream()`` forwards them into ``complete()``.

Fake transports follow the established adapter idiom
(``test_fix_adapters_v2.py`` / ``test_chatgpt_backend.py`` /
``test_local_parity_fixes.py``) — nothing touches the network.
"""

from __future__ import annotations

import base64
import inspect
import json

import pytest

from iron_jarvis.providers.adapters import openai as oa
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.adapters.google import GoogleAdapter
from iron_jarvis.providers.adapters.grok_cli import GrokCliAdapter
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.adapters.openai import _CHATGPT_ENDPOINT, OpenAIAdapter
from iron_jarvis.providers.adapters.opencode_cli import OpencodeCliAdapter
from iron_jarvis.providers.adapters.prompted_tools import PromptedToolsAdapter
from iron_jarvis.providers.adapters.subprocess_cli import (
    ClaudeCliAdapter,
    SubprocessCliAdapter,
)
from iron_jarvis.providers.guided import GuidedToolsAdapter

_URL = "http://localhost:11434/v1/chat/completions"
_MODEL = "llama3.2-guided"
_MSG = [LLMMessage(role="user", content="hi")]

_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "tool_call",
        "schema": {"type": "object", "properties": {"tool": {"type": "string"}}},
    },
}


@pytest.fixture(autouse=True)
def _clean_module_memos():
    """The token-limit swap memo + ChatGPT ladder caches are module-level."""
    oa._COMPLETION_TOKENS_MODELS.clear()
    rejected, known = set(oa._CHATGPT_REJECTED), list(oa._CHATGPT_KNOWN_GOOD)
    oa._CHATGPT_REJECTED.clear()
    oa._CHATGPT_KNOWN_GOOD.clear()
    yield
    oa._COMPLETION_TOKENS_MODELS.clear()
    oa._CHATGPT_REJECTED.clear()
    oa._CHATGPT_REJECTED.update(rejected)
    oa._CHATGPT_KNOWN_GOOD.clear()
    oa._CHATGPT_KNOWN_GOOD.extend(known)


# --------------------------------------------------------------------------- #
# Fakes (the established adapter test idiom)
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload: dict | None = None, text: str = "") -> None:
        self._payload = payload
        self.text = text
        self.status_code = 200

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeHTTP:
    """Records each POST and returns a canned response (async post)."""

    def __init__(self, payload: dict | None = None, text: str = "") -> None:
        self._payload = payload
        self._text = text
        self.calls: list[dict] = []

    async def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return FakeResponse(self._payload, self._text)

    @property
    def last(self) -> dict:
        return self.calls[-1]


_OK = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


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


class _StreamClient:
    """Records every streamed body; always answers with a tiny SSE stream."""

    def __init__(self):
        self.bodies: list[dict] = []

    def stream(self, method, url, headers=None, json=None):
        self.bodies.append(json)
        return _FakeStreamCM(_SSEResp())


async def _collect(gen):
    return [frame async for frame in gen]


# -- Anthropic SDK stand-ins (the adapter speaks the SDK, not raw httpx) ----- #
class _Attr:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeMessages:
    def __init__(self, parent):
        self._parent = parent

    async def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        return self._parent.response


class FakeAnthropicClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)


def _anthropic_response():
    return _Attr(
        content=[_Attr(type="text", text="hi")],
        stop_reason="end_turn",
        usage=_Attr(input_tokens=1, output_tokens=1),
    )


# -- ChatGPT (Codex) backend fakes ------------------------------------------ #
def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{seg}.sig"


_TOKEN = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}})

_TEXT_SSE = "".join(
    f"data: {json.dumps(e)}\n\n"
    for e in [
        {"type": "response.created"},
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    ]
)


# --------------------------------------------------------------------------- #
# 1. None kwargs ⇒ byte-identical wire payload (the captured-request pins)
# --------------------------------------------------------------------------- #
#: The pre-v1.203.0 chat/completions body for (system="s", one user msg, no
#: tools) — captured before the guided-decoding params existed. If a default
#: ever adds/renames a key, these literals go red.
_BASELINE_COMPLETE = {
    "model": _MODEL,
    "max_tokens": 4096,
    "messages": [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
    ],
}
_BASELINE_STREAM = {
    **_BASELINE_COMPLETE,
    "stream": True,
    "stream_options": {"include_usage": True},
}


def _adapter(http) -> OpenAIAdapter:
    return OpenAIAdapter(model=_MODEL, base_url=_URL, api_key=None, http=http)


async def test_complete_none_kwargs_body_byte_identical():
    http_default = FakeHTTP(_OK)
    await _adapter(http_default).complete(system="s", messages=_MSG, tools=[])

    http_none = FakeHTTP(_OK)
    await _adapter(http_none).complete(
        system="s",
        messages=_MSG,
        tools=[],
        response_format=None,
        tool_choice=None,
        extra_body=None,
    )

    assert http_default.last["json"] == _BASELINE_COMPLETE  # the captured pin
    # byte-identical, not merely dict-equal:
    assert json.dumps(http_default.last["json"], sort_keys=True) == json.dumps(
        http_none.last["json"], sort_keys=True
    )
    for body in (http_default.last["json"], http_none.last["json"]):
        assert "response_format" not in body
        assert "tool_choice" not in body


async def test_stream_none_kwargs_body_byte_identical():
    client_default = _StreamClient()
    a = _adapter(None)
    a._client = lambda: client_default  # noqa: SLF001
    await _collect(a.stream(system="s", messages=_MSG, tools=[]))

    client_none = _StreamClient()
    b = _adapter(None)
    b._client = lambda: client_none  # noqa: SLF001
    await _collect(
        b.stream(
            system="s",
            messages=_MSG,
            tools=[],
            response_format=None,
            tool_choice=None,
            extra_body=None,
        )
    )

    assert client_default.bodies[0] == _BASELINE_STREAM  # the captured pin
    assert json.dumps(client_default.bodies[0], sort_keys=True) == json.dumps(
        client_none.bodies[0], sort_keys=True
    )


# --------------------------------------------------------------------------- #
# 2. The openai-compat family forwards the knobs
# --------------------------------------------------------------------------- #
async def test_complete_response_format_lands_under_the_right_key():
    http = FakeHTTP(_OK)
    await _adapter(http).complete(
        system="s", messages=_MSG, tools=[], response_format=_SCHEMA
    )
    body = http.last["json"]
    assert body["response_format"] == _SCHEMA
    # nothing ELSE moved: strip the new key and the baseline is intact
    rest = {k: v for k, v in body.items() if k != "response_format"}
    assert rest == _BASELINE_COMPLETE


async def test_complete_tool_choice_lands_str_and_dict():
    http = FakeHTTP(_OK)
    await _adapter(http).complete(
        system="s", messages=_MSG, tools=[], tool_choice="required"
    )
    assert http.last["json"]["tool_choice"] == "required"

    forced = {"type": "function", "function": {"name": "emit"}}
    await _adapter(http).complete(
        system="s", messages=_MSG, tools=[], tool_choice=forced
    )
    assert http.last["json"]["tool_choice"] == forced


async def test_complete_extra_body_merges_last_and_wins_clashes():
    http = FakeHTTP(_OK)
    winner = {"type": "json_schema", "json_schema": {"name": "guided_wins"}}
    await _adapter(http).complete(
        system="s",
        messages=_MSG,
        tools=[],
        response_format={"type": "json_object"},  # the clashing portable form
        extra_body={
            "response_format": winner,  # extra_body is applied LAST — it wins
            "guided_json": {"type": "object"},  # vLLM escape hatch rides along
        },
    )
    body = http.last["json"]
    assert body["response_format"] == winner
    assert body["guided_json"] == {"type": "object"}


async def test_stream_forwards_knobs_and_extra_body_wins():
    client = _StreamClient()
    a = _adapter(None)
    a._client = lambda: client  # noqa: SLF001
    winner = {"type": "json_schema", "json_schema": {"name": "guided_wins"}}
    frames = await _collect(
        a.stream(
            system="s",
            messages=_MSG,
            tools=[],
            response_format={"type": "json_object"},
            tool_choice="required",
            extra_body={"response_format": winner, "guided_json": {"type": "object"}},
        )
    )
    body = client.bodies[0]
    assert body["response_format"] == winner
    assert body["tool_choice"] == "required"
    assert body["guided_json"] == {"type": "object"}
    assert body["stream"] is True  # still a streaming request
    assert [f for f in frames if f["type"] == "text"], "the stream still streams"


# --------------------------------------------------------------------------- #
# 3. ChatGPT-account (subscription) backend: accepted, dropped
# --------------------------------------------------------------------------- #
async def test_chatgpt_backend_accepts_and_drops_the_knobs():
    http = FakeHTTP(text=_TEXT_SSE)
    adapter = OpenAIAdapter(model="gpt-5-codex", api_key=_TOKEN, http=http)
    out = await adapter.complete(
        system="be brief",
        messages=_MSG,
        tools=[],
        response_format=_SCHEMA,
        tool_choice="required",
        extra_body={"guided_json": {"type": "object"}},
    )
    assert out.text == "hello"  # no raise: accepted…
    assert http.last["url"] == _CHATGPT_ENDPOINT
    body = http.last["json"]
    assert "response_format" not in body  # …and dropped
    assert "guided_json" not in body
    assert body["tool_choice"] == "auto"  # the backend's own field, untouched


# --------------------------------------------------------------------------- #
# 4. Non-openai-compat adapters: accepted, ignored, zero wire change
# --------------------------------------------------------------------------- #
async def test_anthropic_accepts_kwargs_without_wire_change():
    plain = AnthropicAdapter(api_key="sk-ant")
    plain_client = FakeAnthropicClient(_anthropic_response())
    plain._client = lambda: plain_client  # noqa: SLF001
    await plain.complete(system="s", messages=_MSG, tools=[])

    guided = AnthropicAdapter(api_key="sk-ant")
    guided_client = FakeAnthropicClient(_anthropic_response())
    guided._client = lambda: guided_client  # noqa: SLF001
    res = await guided.complete(
        system="s",
        messages=_MSG,
        tools=[],
        response_format=_SCHEMA,
        tool_choice="required",
        extra_body={"guided_json": {}},
    )
    assert res.text == "hi"  # no raise
    assert guided_client.calls == plain_client.calls  # byte-for-byte same request


async def test_mock_accepts_kwargs_and_output_is_unchanged():
    plain = await MockLLMAdapter().complete(system="", messages=_MSG, tools=[])
    guided = await MockLLMAdapter().complete(
        system="",
        messages=_MSG,
        tools=[],
        response_format=_SCHEMA,
        tool_choice="required",
        extra_body={"guided_json": {}},
    )
    assert guided.text == plain.text

    frames = await _collect(
        MockLLMAdapter().stream(
            system="", messages=_MSG, tools=[], response_format=_SCHEMA
        )
    )
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"].text == plain.text


# --------------------------------------------------------------------------- #
# 5. Uniform-caller guarantee across EVERY adapter class
# --------------------------------------------------------------------------- #
_ADAPTER_CLASSES = [
    AnthropicAdapter,
    ClaudeCliAdapter,
    GoogleAdapter,
    GrokCliAdapter,
    GuidedToolsAdapter,
    MockLLMAdapter,
    OpenAIAdapter,
    OpencodeCliAdapter,
    PromptedToolsAdapter,
    SubprocessCliAdapter,
]

_KNOBS = ("response_format", "tool_choice", "extra_body")


@pytest.mark.parametrize("cls", _ADAPTER_CLASSES, ids=lambda c: c.__name__)
def test_every_adapter_signature_accepts_the_guided_kwargs(cls):
    """A caller must be able to pass the knobs uniformly: every adapter's own
    complete (and stream, where it overrides the base) declares all three."""
    for name in ("complete", "stream"):
        fn = cls.__dict__.get(name)
        if fn is None:
            continue  # inherits the base default, which declares them
        params = inspect.signature(fn).parameters
        for knob in _KNOBS:
            assert knob in params, f"{cls.__name__}.{name} missing {knob}="
            assert params[knob].default is None, f"{cls.__name__}.{name} {knob} default"


def test_base_declares_the_knobs():
    for name in ("complete", "stream"):
        params = inspect.signature(getattr(LLMAdapter, name)).parameters
        for knob in _KNOBS:
            assert knob in params and params[knob].default is None


# --------------------------------------------------------------------------- #
# 6. The base default stream() forwards the knobs into complete()
# --------------------------------------------------------------------------- #
async def test_base_default_stream_forwards_kwargs_to_complete():
    seen: dict = {}

    class _Rec(LLMAdapter):
        provider = "rec"
        model = "rec-1"

        async def complete(self, **kw):  # noqa: D102 — test double
            seen.update(kw)
            return LLMResponse(text="ok")

    frames = await _collect(
        _Rec().stream(
            system="s",
            messages=_MSG,
            tools=[],
            response_format=_SCHEMA,
            tool_choice="required",
            extra_body={"guided_json": {}},
        )
    )
    assert frames[-1]["response"].text == "ok"
    assert seen["response_format"] == _SCHEMA
    assert seen["tool_choice"] == "required"
    assert seen["extra_body"] == {"guided_json": {}}


async def test_base_default_stream_spares_narrow_legacy_subclasses():
    """None knobs are NOT forwarded as keys, so a subclass still declaring the
    narrow pre-v1.203.0 complete(system=, messages=, tools=) keeps streaming."""

    class _Legacy(LLMAdapter):
        provider = "legacy"
        model = "legacy-1"

        async def complete(self, *, system, messages, tools):  # noqa: D102
            return LLMResponse(text="legacy ok")

    frames = await _collect(_Legacy().stream(system="s", messages=_MSG, tools=[]))
    assert frames[-1]["response"].text == "legacy ok"
