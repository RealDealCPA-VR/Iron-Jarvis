"""A per-call pin on the router (v1.324.0).

A pack's model request (MCP sampling) is answered by the chat turn's OWN
model or refused. ``router.complete(..., pin=True)`` is the strict model pin
for that one call — with the user's global pin OFF, a failing explicit pick
must raise, never reach the default (A) or the sideways chain (B): the
pack's text would otherwise leave for a provider the user never chose
(the v1.162.0 rule). ``stream()`` takes the same flag (lock-step).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.core.events import EventBus
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import ModelRouter

ROOT = Path(__file__).resolve().parents[1]


class _Overloaded(LLMAdapter):
    """A CLOUD pick that fails transiently — the case that fails over today."""

    def __init__(self, provider="openai", model="gpt-x"):
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        raise ProviderError("overloaded", transient=True)

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise ProviderError("overloaded", transient=True)
        yield {}  # pragma: no cover — makes this an async generator


class _Ok(LLMAdapter):
    def __init__(self, provider, model="m"):
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text="someone else answered", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "text", "text": "someone else answered"}
        yield {"type": "final", "text": "someone else answered", "tool_calls": [], "usage": {}}


class _Manager:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, provider):
        return provider in self.adapters

    def has_available_api_provider(self):
        return True

    def get(self, provider, model=None):
        return self.adapters[provider]


def _setup():
    picked = _Overloaded()
    default = _Ok("anthropic")
    other = _Ok("gemini")
    manager = _Manager({"openai": picked, "anthropic": default, "gemini": other})
    # The user's global pin is OFF: only the per-call flag can stop failover.
    router = ModelRouter(manager, "anthropic", EventBus(), strict_pin=lambda: False)
    return router, picked, default, other


_MSG = [LLMMessage(role="user", content="q")]


async def test_without_the_pin_an_explicit_cloud_pick_fails_over():
    """The control: today's contract, so the next test proves something."""
    router, picked, default, other = _setup()
    res = await router.complete(
        provider="openai", model="gpt-x", system="", messages=_MSG, tools=[]
    )
    assert res.provider != "openai"
    assert default.calls + other.calls == 1


async def test_a_pinned_call_raises_and_reaches_no_other_provider():
    router, picked, default, other = _setup()
    with pytest.raises(ProviderError):
        await router.complete(
            provider="openai", model="gpt-x", system="", messages=_MSG, tools=[],
            pin=True,
        )
    assert picked.calls >= 1
    assert default.calls == 0 and other.calls == 0


async def test_a_pinned_stream_raises_and_reaches_no_other_provider():
    router, picked, default, other = _setup()
    with pytest.raises(ProviderError):
        async for _ in router.stream(
            provider="openai", model="gpt-x", system="", messages=_MSG, tools=[],
            pin=True,
        ):
            pass
    assert default.calls == 0 and other.calls == 0


def test_sampling_asks_the_router_for_a_pinned_call():
    src = (ROOT / "src/iron_jarvis/daemon/mcp_turn.py").read_text(encoding="utf-8")
    call = re.search(r"router\.complete\((.*?)\n\s*\)", src, re.S)
    assert call is not None, "mcp_turn no longer calls router.complete"
    assert "pin=True" in call.group(1)
