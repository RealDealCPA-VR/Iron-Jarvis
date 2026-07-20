"""Strict model pin (config.strict_model_pin, v1.68.0).

When ON and the caller EXPLICITLY names a provider, the router must answer
with THAT provider or fail honestly: no capability swap, no cross-provider
failover, no mock downgrade. Default-route requests keep the full
answer-if-anyone-can behavior, and with the pin OFF nothing changes (the
existing failover suites pin that contract).
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.events import EventBus
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.router import ModelRouter


class _Boom(LLMAdapter):
    def __init__(self, provider="fleet-box", model="llama3", transient=False):
        self.provider, self.model = provider, model
        self.calls = 0
        self._transient = transient

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        if self._transient:
            from iron_jarvis.providers.adapters.base import ProviderError

            raise ProviderError("endpoint overloaded", transient=True)
        raise RuntimeError("endpoint exploded")


class _NoTools(LLMAdapter):
    """A tool-incapable adapter that still ANSWERS (the pinned-pick case)."""

    def __init__(self, provider="fleet-box", model="llama3"):
        self.provider, self.model = provider, model
        self.seen_tools = None

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": False, "vision": False}

    async def complete(self, *, system, messages, tools):
        self.seen_tools = tools
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
    def __init__(self, adapters, available=None):
        self.adapters = adapters
        self._available = available or set(adapters)

    def available(self, provider):
        return provider in self._available

    def has_available_api_provider(self):
        return any(p != "mock" for p in self._available)

    def get(self, provider, model=None):
        return self.adapters[provider]


def _router(manager, *, pin: bool, default="anthropic"):
    return ModelRouter(
        manager, default, EventBus(), strict_pin=lambda: pin
    )


_MSG = [LLMMessage(role="user", content="q")]
_TOOL = [{"name": "web_search", "description": "", "input_schema": {"type": "object"}}]


async def test_pinned_explicit_pick_keeps_tools_no_capability_swap():
    local = _NoTools()
    frontier = _Capable()
    manager = _Manager(
        {"fleet-box": local, "anthropic": frontier, "mock": _Mock()},
        available={"fleet-box", "anthropic"},
    )
    r = _router(manager, pin=True)
    res = await r.complete(
        provider="fleet-box", model="llama3", system="", messages=_MSG, tools=_TOOL
    )
    # The pick answered — tools were OFFERED to it, and nothing rerouted.
    assert res.provider == "fleet-box"
    assert local.seen_tools == _TOOL
    assert frontier.calls == 0


async def test_pinned_explicit_failure_raises_no_failover():
    local = _Boom()
    frontier = _Capable()
    manager = _Manager(
        {"fleet-box": local, "anthropic": frontier, "mock": _Mock()},
        available={"fleet-box", "anthropic"},
    )
    r = _router(manager, pin=True)
    with pytest.raises(RuntimeError, match="endpoint exploded"):
        await r.complete(
            provider="fleet-box", model="llama3", system="", messages=_MSG, tools=[]
        )
    assert frontier.calls == 0  # never substituted


async def test_pinned_unavailable_pick_is_honest_not_mock():
    manager = _Manager(
        {"anthropic": _Capable(), "mock": _Mock()}, available={"anthropic"}
    )
    r = _router(manager, pin=True)
    with pytest.raises(Exception, match="strict model pin"):
        await r.complete(
            provider="fleet-gone", model=None, system="", messages=_MSG, tools=[]
        )


async def test_default_route_still_fails_over_with_pin_on():
    # Transient failure — the failover chain applies (a non-transient one
    # surfaces honestly by the existing contract, pin or no pin).
    boom = _Boom(provider="anthropic", model="claude-x", transient=True)
    ok = _Capable(provider="openai", model="gpt-ok")
    manager = _Manager(
        {"anthropic": boom, "openai": ok, "mock": _Mock()},
        available={"anthropic", "openai"},
    )
    r = _router(manager, pin=True, default="anthropic")
    # NO explicit provider — the pin must not apply to the default route.
    res = await r.complete(provider=None, model=None, system="", messages=_MSG, tools=[])
    assert res.provider == "openai"


async def test_pin_off_explicit_failure_still_fails_over():
    boom = _Boom(provider="fleet-box")
    ok = _Capable(provider="anthropic")
    manager = _Manager(
        {"fleet-box": boom, "anthropic": ok, "mock": _Mock()},
        available={"fleet-box", "anthropic"},
    )
    r = _router(manager, pin=False)
    res = await r.complete(
        provider="fleet-box", model=None, system="", messages=_MSG, tools=[]
    )
    assert res.provider == "anthropic"  # the pre-pin contract, unchanged
