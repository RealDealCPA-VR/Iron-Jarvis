"""Hosted OpenAI reasoning models want ``max_completion_tokens``, not ``max_tokens``.

api.openai.com answers the o-series / gpt-5 families with a PERMANENT 400
("Unsupported parameter: 'max_tokens' … use 'max_completion_tokens' instead"),
so every request for a modern reasoning model hard-failed while gpt-4 worked —
and the app's own discovery offers those ids to the picker.

The fix is ERROR-DRIVEN, never a model list (ids rot — see the retired ChatGPT
ladder in the same module): send ``max_tokens``, and when THAT server names the
other field, swap it, retry once, and remember the endpoint+model. Pinned here
for BOTH lanes (complete + stream), plus the no-regression case for
OpenAI-compatible local servers, which keep getting ``max_tokens``.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.providers.adapters import openai as oa
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from iron_jarvis.providers.adapters.openai import OpenAIAdapter

_MSG = [LLMMessage(role="user", content="q")]

_UNSUPPORTED = (
    "Unsupported parameter: 'max_tokens' is not supported with this model. "
    "Use 'max_completion_tokens' instead."
)


@pytest.fixture(autouse=True)
def _clean_memo():
    """The swap memo is module-level (adapters are rebuilt per request)."""
    oa._COMPLETION_TOKENS_MODELS.clear()
    yield
    oa._COMPLETION_TOKENS_MODELS.clear()


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    async def aread(self):
        return self.text.encode()


def _error(message: str) -> dict:
    return {"error": {"message": message}}


_OK = {
    "choices": [{"message": {"content": "reasoned answer"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
}


class _PostClient:
    """Records every posted body; replies from a scripted queue."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.bodies.append(json)
        return self.responses.pop(0)


# ------------------------------------------------------------- complete() ---


async def test_complete_swaps_to_max_completion_tokens_on_the_400():
    adapter = OpenAIAdapter(model="gpt-5", api_key="sk-test", max_tokens=1234)
    client = _PostClient([_Resp(400, _error(_UNSUPPORTED)), _Resp(200, _OK)])
    adapter._client = lambda: client  # noqa: SLF001

    out = await adapter.complete(system="", messages=_MSG, tools=[])

    assert out.text == "reasoned answer"  # was: ProviderError 400, every time
    assert len(client.bodies) == 2
    assert client.bodies[0]["max_tokens"] == 1234
    assert client.bodies[1]["max_completion_tokens"] == 1234
    assert "max_tokens" not in client.bodies[1]
    assert client.bodies[1]["model"] == "gpt-5"


async def test_complete_remembers_the_swap_for_the_next_adapter():
    first = OpenAIAdapter(model="o3", api_key="sk-test", max_tokens=99)
    c1 = _PostClient([_Resp(400, _error(_UNSUPPORTED)), _Resp(200, _OK)])
    first._client = lambda: c1  # noqa: SLF001
    await first.complete(system="", messages=_MSG, tools=[])

    # Adapters are rebuilt per request: the next one must not re-pay the 400.
    second = OpenAIAdapter(model="o3", api_key="sk-test", max_tokens=99)
    c2 = _PostClient([_Resp(200, _OK)])
    second._client = lambda: c2  # noqa: SLF001
    await second.complete(system="", messages=_MSG, tools=[])

    assert len(c2.bodies) == 1
    assert c2.bodies[0]["max_completion_tokens"] == 99
    assert "max_tokens" not in c2.bodies[0]

    # …and the memo is per endpoint+model, not global.
    other = OpenAIAdapter(model="gpt-4o", api_key="sk-test", max_tokens=99)
    c3 = _PostClient([_Resp(200, _OK)])
    other._client = lambda: c3  # noqa: SLF001
    await other.complete(system="", messages=_MSG, tools=[])
    assert c3.bodies[0]["max_tokens"] == 99


async def test_complete_does_not_retry_an_unrelated_400():
    adapter = OpenAIAdapter(model="gpt-5", api_key="sk-test")
    client = _PostClient([_Resp(400, _error("Invalid value for 'model'"))])
    adapter._client = lambda: client  # noqa: SLF001

    with pytest.raises(ProviderError) as err:
        await adapter.complete(system="", messages=_MSG, tools=[])
    assert err.value.status_code == 400
    assert len(client.bodies) == 1  # no blind second request
    assert ("https://api.openai.com/v1/chat/completions", "gpt-5") not in (
        oa._COMPLETION_TOKENS_MODELS
    )


async def test_complete_keeps_max_tokens_for_a_local_server():
    adapter = OpenAIAdapter(model="llama3", base_url="http://local/v1", max_tokens=512)
    client = _PostClient([_Resp(200, _OK)])
    adapter._client = lambda: client  # noqa: SLF001
    await adapter.complete(system="", messages=_MSG, tools=[])
    assert client.bodies[0]["max_tokens"] == 512
    assert "max_completion_tokens" not in client.bodies[0]


# --------------------------------------------------------------- stream() ---


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
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies: list[dict] = []

    def stream(self, method, url, headers=None, json=None):
        self.bodies.append(json)
        return _FakeStreamCM(self.responses.pop(0))


async def _collect(agen):
    return [f async for f in agen]


async def test_stream_swaps_to_max_completion_tokens_on_the_400():
    adapter = OpenAIAdapter(model="gpt-5", api_key="sk-test", max_tokens=777)
    client = _StreamClient([_Resp(400, _error(_UNSUPPORTED)), _SSEResp()])
    adapter._client = lambda: client  # noqa: SLF001

    frames = await _collect(adapter.stream(system="", messages=_MSG, tools=[]))

    assert [f for f in frames if f["type"] == "text"], "the retry must stream"
    assert frames[-1]["response"].text == "hi"
    assert len(client.bodies) == 2
    assert client.bodies[0]["max_tokens"] == 777
    assert client.bodies[1]["max_completion_tokens"] == 777
    assert "max_tokens" not in client.bodies[1]
    # The retry keeps everything else the hosted lane needs.
    assert client.bodies[1]["stream"] is True
    assert client.bodies[1]["stream_options"] == {"include_usage": True}


async def test_stream_uses_the_memo_from_complete():
    warm = OpenAIAdapter(model="gpt-5", api_key="sk-test", max_tokens=64)
    warm_client = _PostClient([_Resp(400, _error(_UNSUPPORTED)), _Resp(200, _OK)])
    warm._client = lambda: warm_client  # noqa: SLF001
    await warm.complete(system="", messages=_MSG, tools=[])

    adapter = OpenAIAdapter(model="gpt-5", api_key="sk-test", max_tokens=64)
    client = _StreamClient([_SSEResp()])
    adapter._client = lambda: client  # noqa: SLF001
    await _collect(adapter.stream(system="", messages=_MSG, tools=[]))
    assert client.bodies[0]["max_completion_tokens"] == 64
    assert "max_tokens" not in client.bodies[0]


async def test_stream_local_400_still_falls_back_to_no_stream_options():
    """Regression pin: the pre-existing non-hosted retry is untouched."""
    adapter = OpenAIAdapter(model="m", base_url="http://local/v1", max_tokens=8)
    client = _StreamClient(
        [_Resp(400, _error("unknown field stream_options")), _SSEResp()]
    )
    adapter._client = lambda: client  # noqa: SLF001

    frames = await _collect(adapter.stream(system="", messages=_MSG, tools=[]))
    assert [f for f in frames if f["type"] == "text"]
    assert "stream_options" in client.bodies[0]
    assert "stream_options" not in client.bodies[1]
    assert client.bodies[1]["max_tokens"] == 8  # local servers keep the old name


async def test_stream_local_swap_still_leaves_the_stream_options_rung():
    """A local gateway naming max_completion_tokens: swap first, slim after."""
    adapter = OpenAIAdapter(model="m", base_url="http://local/v1", max_tokens=8)
    client = _StreamClient(
        [
            _Resp(400, _error(_UNSUPPORTED)),
            _Resp(400, _error("unknown field stream_options")),
            _SSEResp(),
        ]
    )
    adapter._client = lambda: client  # noqa: SLF001

    frames = await _collect(adapter.stream(system="", messages=_MSG, tools=[]))
    assert [f for f in frames if f["type"] == "text"]
    assert client.bodies[0]["max_tokens"] == 8
    assert client.bodies[1]["max_completion_tokens"] == 8
    # The remaining rung was swapped too — it must not resend the refused name.
    assert client.bodies[2]["max_completion_tokens"] == 8
    assert "stream_options" not in client.bodies[2]
