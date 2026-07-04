"""Router: honest failure over fabrication + correct fallback model (v1.10.13)."""

from __future__ import annotations

import pytest

from iron_jarvis.core.events import EventBus
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.router import ModelRouter


class _Boom(LLMAdapter):
    provider = "anthropic"
    model = "claude-x"

    def __init__(self, provider="anthropic", model="claude-x"):
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        raise RuntimeError("api error 400: nope")


class _Ok(LLMAdapter):
    provider = "openai"
    model = "gpt-ok"

    def __init__(self, provider="openai", model="gpt-ok"):
        self.provider, self.model = provider, model
        self.last_model_asked = None

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text="real answer", tool_calls=[], usage={})


class _Mock(LLMAdapter):
    provider = "mock"
    model = "mock-1"

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text="fabricated", tool_calls=[], usage={})


class _Manager:
    """Minimal manager: provider -> adapter; records the model arg passed."""

    def __init__(self, adapters, available=None):
        self.adapters = adapters
        self._available = available or set(adapters)
        self.get_calls: list[tuple[str, str | None]] = []

    def available(self, provider):
        return provider in self._available

    def has_available_api_provider(self):
        return any(p != "mock" for p in self._available)

    def get(self, provider, model=None):
        self.get_calls.append((provider, model))
        return self.adapters[provider]


def _msgs():
    return [LLMMessage(role="user", content="hi")]


@pytest.mark.asyncio
async def test_explicit_real_provider_failure_raises_not_mock():
    mgr = _Manager({"anthropic": _Boom(), "mock": _Mock()})
    router = ModelRouter(mgr, default_provider="anthropic", event_bus=EventBus())
    with pytest.raises(RuntimeError):
        await router.complete(
            provider="anthropic", model="claude-x", system="", messages=_msgs(), tools=[]
        )


@pytest.mark.asyncio
async def test_fallback_to_default_uses_defaults_own_model():
    # openai (explicit) fails -> falls to default anthropic; must NOT pass
    # openai's model id to anthropic (get called with model=None).
    mgr = _Manager({"openai": _Boom("openai", "gpt-dead"), "anthropic": _Ok("anthropic", "claude-good"), "mock": _Mock()})
    router = ModelRouter(mgr, default_provider="anthropic", event_bus=EventBus())
    res = await router.complete(
        provider="openai", model="gpt-dead", system="", messages=_msgs(), tools=[]
    )
    assert res.provider == "anthropic"
    assert res.response.text == "real answer"
    # The fallback get() must have been called WITHOUT the failed model id.
    assert ("anthropic", None) in mgr.get_calls


@pytest.mark.asyncio
async def test_mock_requested_still_runs_mock():
    mgr = _Manager({"mock": _Mock()})
    router = ModelRouter(mgr, default_provider="mock", event_bus=EventBus())
    res = await router.complete(system="", messages=_msgs(), tools=[])
    assert res.provider == "mock"


@pytest.mark.asyncio
async def test_unavailable_provider_still_downgrades_to_mock_prerun():
    # Availability downgrade (not connected) is a PRE-RUN decision and keeps the
    # mock path (with the PROVIDER_DOWNGRADED signal) — that's not fabrication
    # of a failed real call.
    mgr = _Manager({"mock": _Mock(), "xai": _Boom("xai")}, available={"mock"})
    router = ModelRouter(mgr, default_provider="mock", event_bus=EventBus())
    res = await router.complete(provider="xai", system="", messages=_msgs(), tools=[])
    assert res.provider == "mock"
