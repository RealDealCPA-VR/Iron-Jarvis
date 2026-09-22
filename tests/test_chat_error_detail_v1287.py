"""chat-07 (v1.287.0): a CLOUD model that drops mid-answer says so, by name.

OpenRouter / xAI / an OpenAI key / Google all stream over httpx. A dropped
socket or a read timeout mid-answer raises ``httpx.ReadError("")`` /
``httpx.ReadTimeout("")`` — an EMPTY message. v1.232.0 gave that death the
honest "dropped mid-answer, so the reply above is incomplete" wording, but only
for a LOCAL provider; a cloud one was re-raised raw, the stream lane emitted
``{"detail": ""}`` and the page showed the placeholder "stream error" under
half an answer.

Now ``_committed_failure`` words ANY transport-shaped mid-answer death the same
way (wording only — nothing is swapped, the no-swap rule stands), and both chat
lanes' error handlers never show an empty ``str(exc)``.

The stream-lane case drives the REAL app, the REAL router and the REAL
OpenAI-compatible adapter; only the socket is fake (an ``httpx.MockTransport``
whose SSE body sends one delta and then breaks).
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.router import ModelRouter, ProviderError


class _SSEThenBreak(httpx.AsyncByteStream):
    """One real SSE text delta, then the transport breaks with *exc*."""

    def __init__(self, exc: Exception):
        self.exc = exc

    async def __aiter__(self):
        delta = {"choices": [{"index": 0, "delta": {"content": "The QBI deduction is "}}]}
        yield f"data: {json.dumps(delta)}\n\n".encode()
        raise self.exc

    async def aclose(self) -> None:
        return None


def _cloud_adapter(exc: Exception) -> OpenAIAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_SSEThenBreak(exc),
        )

    return OpenAIAdapter(
        "some/model",
        api_key="sk-test",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://openrouter.test/api/v1/chat/completions",
        provider_name="openrouter",
    )


class _Ok(LLMAdapter):
    """A connected second provider — must NEVER be called mid-answer."""

    provider, model = "claude-cli", "ok"

    def __init__(self):
        self.calls = 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text="stand-in", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "text", "text": "stand-in"}
        yield {"type": "final", "response": LLMResponse(text="stand-in", tool_calls=[], usage={})}


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


class _Bus(EventBus):
    def __init__(self):
        super().__init__()
        self.seen: list[tuple[str, dict]] = []

    async def publish(self, type, payload=None, session_id=None):
        self.seen.append((type, dict(payload or {})))
        return await super().publish(type, payload, session_id)

    def of(self, etype):
        return [p for t, p in self.seen if t == etype]


@pytest.mark.parametrize("exc", [httpx.ReadTimeout(""), httpx.ReadError("")])
async def test_a_cloud_death_mid_answer_gets_the_incomplete_wording(exc):
    bus = _Bus()
    standby = _Ok()
    router = ModelRouter(
        _Mgr({"openrouter": _cloud_adapter(exc), "claude-cli": standby}),
        default_provider="openrouter",
        event_bus=bus,
    )
    frames = []
    with pytest.raises(ProviderError) as ei:
        async for f in router.stream(
            system="", messages=[LLMMessage(role="user", content="q")], tools=[]
        ):
            frames.append(f)

    # The user DID see text first — this is the after-the-first-token case.
    assert any(f.get("type") == "text" for f in frames)
    text = str(ei.value)
    assert "openrouter" in text, text
    assert "dropped mid-answer" in text and "incomplete" in text, text
    assert "retry" in text.lower(), text
    assert "not answered" not in text  # the user is looking at partial text
    # A cloud API has no endpoint the user runs (review fix): the advice
    # names what they CAN check.
    assert "endpoint" not in text, text
    assert "internet connection" in text, text
    # WORDING ONLY: no stand-in answered (never-auto-switch), the death still
    # counts, and the local-endpoint banner stays local.
    assert standby.calls == 0
    assert bus.of(EventType.PROVIDER_FAILED)[-1]["partial"] is True
    assert bus.of(EventType.PROVIDER_DOWNGRADED) == []


def _error_frames(text: str) -> list[dict]:
    return [
        json.loads(b.split("data:", 1)[1])
        for b in text.split("\n\n")
        if b.startswith("event: error")
    ]


def test_the_stream_lane_names_the_cloud_model_that_dropped(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    router = ModelRouter(
        _Mgr({"openrouter": _cloud_adapter(httpx.ReadTimeout(""))}),
        default_provider="openrouter",
        event_bus=EventBus(),
    )
    monkeypatch.setattr(platform.router, "stream", router.stream)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "q"}]})
    errs = _error_frames(r.text)
    assert errs, r.text[-800:]
    detail = errs[-1]["detail"]
    assert "openrouter" in detail and "incomplete" in detail, detail


def test_the_stream_lane_never_emits_a_blank_error(tmp_path, monkeypatch):
    """Belt: whatever raises with an empty message, the frame says something."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform

    async def _blank(**kw):
        raise httpx.ReadTimeout("")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(platform.router, "stream", _blank)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "q"}]})
    errs = _error_frames(r.text)
    assert errs, r.text[-800:]
    assert errs[-1]["detail"] == "ReadTimeout: timeout"


def test_the_post_lane_never_answers_a_blank_error(tmp_path, monkeypatch):
    """Lock-step twin: POST /chat (chat_turn) surfaced ``detail=str(exc)``."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform

    async def _blank(**kw):
        raise httpx.ReadError("")

    monkeypatch.setattr(platform.router, "complete", _blank)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "q"}]})
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "ReadError: interrupted"
