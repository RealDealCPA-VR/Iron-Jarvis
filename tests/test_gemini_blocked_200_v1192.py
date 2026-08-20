"""Gemini answers HTTP 200 when it REFUSES — that must not parse as success.

Finding 29 (docs/TOFIX-2026-08-20.md): ``GoogleAdapter._parse`` guarded only
``status >= 400``. A safety block comes back 200 with ``promptFeedback.
blockReason`` and no candidates (or a candidate with ``finishReason``
SAFETY/RECITATION and empty ``content.parts``), which defaulted through
``(candidates or [{}])[0]`` into ``LLMResponse(text="", finish_reason="stop")``
— a blank reply presented as SUCCESS: the router records health, chat renders an
empty bubble, an agent step ends as if the model chose to say nothing. The
streaming assembly in ``_stream_sse`` had the identical hole.

Both paths now raise a typed :class:`ProviderError` naming the reason, and it is
PERMANENT so it never triggers a retry/failover storm.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from iron_jarvis.providers.adapters.google import GoogleAdapter
from iron_jarvis.providers.router import is_transient_error


# --------------------------------------------------------------------------- #
# transports
# --------------------------------------------------------------------------- #
class _Resp:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _PostHTTP:
    """Non-streaming transport; records every POST it served."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    async def post(self, url, *, headers=None, json=None):  # noqa: A002
        self.calls.append({"url": url, "json": json or {}})
        return _Resp(self._payload)


class _StreamCM:
    def __init__(self, resp) -> None:
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _SSEResp:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    async def aiter_lines(self):
        for c in self._chunks:
            yield "data: " + json.dumps(c)


class _StreamHTTP:
    """Streaming transport that ALSO serves the non-streaming fallback POST."""

    def __init__(self, chunks: list[dict], post_payload: dict | None = None) -> None:
        self._chunks = chunks
        self._post_payload = post_payload or {}
        self.posts: list[dict] = []

    def stream(self, method, url, *, headers=None, json=None):  # noqa: A002
        return _StreamCM(_SSEResp(self._chunks))

    async def post(self, url, *, headers=None, json=None):  # noqa: A002
        self.posts.append({"url": url, "json": json or {}})
        return _Resp(self._post_payload)


async def _collect(agen):
    return [f async for f in agen]


_PROMPT = [LLMMessage(role="user", content="quote this flagged K-1 passage")]


# --------------------------------------------------------------------------- #
# complete()
# --------------------------------------------------------------------------- #
async def test_prompt_level_safety_block_raises_instead_of_blank_reply():
    http = _PostHTTP({"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []})
    adapter = GoogleAdapter(api_key="g-test", http=http)
    with pytest.raises(ProviderError) as ei:
        await adapter.complete(system="", messages=_PROMPT, tools=[])
    assert "SAFETY" in str(ei.value)
    # PERMANENT: a content block is deterministic — retrying only replays it.
    assert ei.value.transient is False
    assert is_transient_error(ei.value) is False


async def test_candidate_finish_reason_recitation_with_no_parts_raises():
    http = _PostHTTP(
        {
            "candidates": [{"content": {"parts": []}, "finishReason": "RECITATION"}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 0},
        }
    )
    adapter = GoogleAdapter(api_key="g-test", http=http)
    with pytest.raises(ProviderError) as ei:
        await adapter.complete(system="", messages=_PROMPT, tools=[])
    assert "RECITATION" in str(ei.value)
    assert is_transient_error(ei.value) is False


async def test_block_reason_message_is_carried_into_the_error():
    http = _PostHTTP(
        {
            "promptFeedback": {
                "blockReason": "PROHIBITED_CONTENT",
                "blockReasonMessage": "blocked by the safety filter",
            }
        }
    )
    adapter = GoogleAdapter(api_key="g-test", http=http)
    with pytest.raises(ProviderError) as ei:
        await adapter.complete(system="", messages=_PROMPT, tools=[])
    assert "blocked by the safety filter" in str(ei.value)


async def test_empty_body_with_no_candidates_still_refuses():
    adapter = GoogleAdapter(api_key="g-test", http=_PostHTTP({}))
    with pytest.raises(ProviderError):
        await adapter.complete(system="", messages=_PROMPT, tools=[])


async def test_normal_answers_are_untouched():
    """The guard must fire ONLY when nothing usable came back."""
    ok = _PostHTTP(
        {
            "candidates": [
                {"content": {"parts": [{"text": "bonjour"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
        }
    )
    res = await GoogleAdapter(api_key="g", http=ok).complete(
        system="", messages=_PROMPT, tools=[]
    )
    assert res.text == "bonjour" and res.finish_reason == "stop"
    assert res.usage == {"input_tokens": 3, "output_tokens": 4}

    # A tool call with no text is a perfectly good answer, MAX_TOKENS included.
    tool = _PostHTTP(
        {
            "candidates": [
                {
                    "content": {"parts": [{"functionCall": {"name": "write_file"}}]},
                    "finishReason": "MAX_TOKENS",
                }
            ]
        }
    )
    res = await GoogleAdapter(api_key="g", http=tool).complete(
        system="", messages=_PROMPT, tools=[]
    )
    assert res.finish_reason == "tool_use" and res.tool_calls[0].name == "write_file"


# --------------------------------------------------------------------------- #
# stream() — the same defect, lock-step
# --------------------------------------------------------------------------- #
async def test_blocked_sse_raises_instead_of_an_empty_final_frame():
    http = _StreamHTTP([{"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}])
    adapter = GoogleAdapter(api_key="g-test", http=http)
    with pytest.raises(ProviderError) as ei:
        await _collect(adapter.stream(system="", messages=_PROMPT, tools=[]))
    assert "SAFETY" in str(ei.value)
    assert is_transient_error(ei.value) is False
    # A deterministic refusal must NOT be re-spent on the non-streaming path.
    assert http.posts == []


async def test_blocked_sse_finish_reason_raises():
    http = _StreamHTTP(
        [{"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}]
    )
    with pytest.raises(ProviderError):
        await _collect(
            GoogleAdapter(api_key="g", http=http).stream(
                system="", messages=_PROMPT, tools=[]
            )
        )


async def test_streaming_text_still_streams():
    http = _StreamHTTP(
        [
            {"candidates": [{"content": {"parts": [{"text": "bon"}]}}]},
            {
                "candidates": [
                    {"content": {"parts": [{"text": "jour"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
            },
        ]
    )
    frames = await _collect(
        GoogleAdapter(api_key="g", http=http).stream(
            system="", messages=_PROMPT, tools=[]
        )
    )
    assert [f["text"] for f in frames if f["type"] == "text"] == ["bon", "jour"]
    final = frames[-1]["response"]
    assert final.text == "bonjour" and final.finish_reason == "stop"
    assert final.usage == {"input_tokens": 2, "output_tokens": 3}


async def test_empty_stream_still_degrades_to_the_non_streaming_path():
    """An SSE body that yields nothing at all is not a BLOCK — the existing
    fall-back to complete() must survive (only a real block skips it)."""
    http = _StreamHTTP(
        [],
        post_payload={
            "candidates": [
                {"content": {"parts": [{"text": "recovered"}]}, "finishReason": "STOP"}
            ]
        },
    )
    frames = await _collect(
        GoogleAdapter(api_key="g", http=http).stream(
            system="", messages=_PROMPT, tools=[]
        )
    )
    assert frames[-1]["response"].text == "recovered"
    assert len(http.posts) == 1
