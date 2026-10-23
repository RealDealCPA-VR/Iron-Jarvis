"""v1.378.0 review — the "model refusal shown honestly" fix, checked again.

Each pin here was red on the doer's commit (9da6f28d) and is green on the
fix beside it; each fix was mutation-checked (break it, watch the pin go red,
put it back byte for byte).

Everything is scripted: no real model, no network.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.chat_turn import friendly_error
from iron_jarvis.providers.adapters.base import ProviderError, ProviderRefusal
from iron_jarvis.providers.router import ModelRouter
from tests.test_provider_refusal_v1378 import (
    _Bus,
    _Healthy,
    _Mgr,
    _Refusing,
    _drain,
    _refusal,
    _user,
)


# =========================================================================== #
# 1. `partial` is what REACHED THE USER — and only the router knows that
# =========================================================================== #
async def test_complete_never_says_the_reply_above_is_incomplete():
    """complete() shows the caller nothing before it returns, so a refusal it
    raises is never `partial` — even when the adapter saw text in the declined
    message (Anthropic's complete(), claude-cli's complete() over its own
    stream, OpenAI/Gemini `_parse`). The words used to say "declined to
    finish this answer. The reply above is incomplete." over NOTHING (the
    POST lane, the phone, an agent's nudge)."""
    refused = _Refusing("claude-cli", _refusal(partial=True))
    router = ModelRouter(_Mgr({"claude-cli": refused}), default_provider="claude-cli", event_bus=_Bus())
    with pytest.raises(ProviderRefusal) as info:
        await router.complete(system="", messages=_user(), tools=[])
    assert info.value.partial is False
    words = friendly_error(info.value)
    assert words["title"] == "Claude Code declined to answer this conversation"
    assert "reply above" not in words["fix"]


async def test_stream_partial_is_whether_an_answer_frame_reached_the_caller():
    # Declined before any answer frame, though the adapter called it partial
    # (text in the declined message that never streamed).
    early = _Refusing("claude-cli", _refusal(partial=True))
    router = ModelRouter(_Mgr({"claude-cli": early}), default_provider="claude-cli", event_bus=_Bus())
    with pytest.raises(ProviderRefusal) as info:
        await _drain(router.stream(system="", messages=_user(), tools=[]))
    assert info.value.partial is False
    # Declined AFTER words reached the caller, though the adapter did not say
    # so: the text stays on screen, and the words say the rest was declined.
    late = _Refusing("claude-cli", _refusal(partial=False), head="Here is ")
    router = ModelRouter(_Mgr({"claude-cli": late}), default_provider="claude-cli", event_bus=_Bus())
    frames: list[dict] = []
    with pytest.raises(ProviderRefusal) as info:
        async for f in router.stream(system="", messages=_user(), tools=[]):
            frames.append(f)
    assert [f["text"] for f in frames if f.get("type") == "text"] == ["Here is "]
    assert info.value.partial is True


# =========================================================================== #
# 2. a failover CANDIDATE that declines ends the turn — no third provider
# =========================================================================== #
def _busy(provider: str) -> _Refusing:
    """A primary that failed for an ordinary reason (so a fallback is allowed)."""
    return _Refusing(provider, ProviderError(f"{provider} API error 503: overloaded", status_code=503))


def _broken(provider: str) -> _Refusing:
    """A primary that ANSWERED with a permanent error (fallback (A) still runs)."""
    return _Refusing(provider, ProviderError(f"{provider} API error 400: bad request", status_code=400))


class _Declines(_Refusing):
    """A candidate whose safety check declines, with no provider of its own
    on the error (OpenAI/Gemini refusals carry none — the router names it)."""

    def __init__(self, provider: str):
        super().__init__(provider, ProviderRefusal("the provider's safety check declined to answer"))


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_the_default_fallback_that_declines_is_raised_never_handed_on(lane):
    """(A): the explicit pick failed, the default stands in and DECLINES. The
    turn ends with that refusal — the next connected provider is never tried
    (sending a declined conversation on until something accepts it is the
    user's decision, never a routing fallback), and the default's breaker is
    not struck (it answered)."""
    primary = _broken("anthropic")
    default = _Declines("openai")
    third = _Healthy("google")
    bus = _Bus()
    router = ModelRouter(
        _Mgr({"anthropic": primary, "openai": default, "google": third}),
        default_provider="openai", event_bus=bus,
    )
    with pytest.raises(ProviderRefusal) as info:
        if lane == "complete":
            await router.complete(provider="anthropic", system="", messages=_user(), tools=[])
        else:
            await _drain(router.stream(provider="anthropic", system="", messages=_user(), tools=[]))
    assert default.calls == 1 and third.calls == 0
    assert info.value.failed_provider == "openai", "the words name who declined"
    assert router.health._fails.get("openai", 0) == 0, "no breaker strike: the provider answered"
    failed = [p for p in bus.of(EventType.PROVIDER_FAILED) if p["provider"] == "openai"]
    assert failed and failed[-1].get("kind") == "refusal"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_sideways_candidate_that_declines_is_raised_never_handed_on(lane):
    """(B): the primary was busy (transient), a sideways candidate stands in
    and DECLINES — the remaining candidates are never tried, nothing struck."""
    primary = _busy("claude-cli")
    candidate = _Declines("codex-cli")
    later = _Healthy("anthropic")
    bus = _Bus()
    router = ModelRouter(
        _Mgr({"claude-cli": primary, "codex-cli": candidate, "anthropic": later}),
        default_provider="claude-cli", event_bus=bus, deadline_s=0.3,
    )
    with pytest.raises(ProviderRefusal) as info:
        if lane == "complete":
            await router.complete(system="", messages=_user(), tools=[])
        else:
            await _drain(router.stream(system="", messages=_user(), tools=[]))
    assert candidate.calls == 1 and later.calls == 0
    assert info.value.failed_provider == "codex-cli"
    assert router.health._fails.get("codex-cli", 0) == 0, "no breaker strike: the provider answered"
    assert [p for p in bus.of(EventType.PROVIDER_FAILED) if p["provider"] == "codex-cli"][-1]["kind"] == "refusal"
