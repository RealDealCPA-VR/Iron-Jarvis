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


# =========================================================================== #
# 3. a refusal is never read as "too long" — the refit retry is a retry
# =========================================================================== #
def _long_history(n: int = 8) -> list[dict]:
    words = "the quarterly ledger reconciliation notes " * 60
    out = []
    for k in range(n):
        out.append({"role": "user" if k % 2 == 0 else "assistant", "content": f"{k}: {words}"})
    if out[-1]["role"] != "user":
        out.append({"role": "user", "content": "Say hello"})
    return out


def _too_long_refusal() -> ProviderRefusal:
    """A refusal whose own words happen to contain an overflow phrase (the
    vendor's explanation rides the message)."""
    return ProviderRefusal(
        "claude-cli: Anthropic's safety check declined to answer (stop_reason refusal, "
        "category cyber): prompt is too long for this policy",
        provider="claude-cli", category="cyber", whole_conversation=True,
    )


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_refusal_is_never_shrunk_and_sent_again_as_too_long(tmp_path, monkeypatch, lane):
    """The overflow refit retries ONCE with older messages left out, and
    LEARNS a smaller window for the model. A refusal whose words match an
    overflow phrase was read as "too long": the declined conversation was sent
    again (a retry of a refusal) and every later turn of that model was
    shrunk. Both lanes (lock-step)."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon import chat_turn
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls = {"n": 0}

    async def fake_complete(**kw):
        calls["n"] += 1
        raise _too_long_refusal()

    async def fake_stream(**kw):
        calls["n"] += 1
        raise _too_long_refusal()
        yield  # pragma: no cover — a generator

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    learned_before = dict(chat_turn._LEARNED_WINDOWS)
    body = {"messages": _long_history()}
    if lane == "post":
        r = client.post("/chat", json=body)
        assert r.status_code == 502, r.text
        title = r.json()["title"]
    else:
        r = client.post("/chat/stream", json=body)
        from tests.test_provider_refusal_v1378 import _sse_frames

        [err] = [d for ev, d in _sse_frames(r.text) if ev == "error"]
        title = err["title"]
    assert calls["n"] == 1, "a declined conversation is never sent again"
    assert chat_turn._LEARNED_WINDOWS == learned_before, "no window learned from a refusal"
    assert title == "Claude Code declined to answer this conversation"


# =========================================================================== #
# 4. one-shot utilities: the refusal's sentence, never a "not connected"
# =========================================================================== #
class _OneShotRefuses:
    def __init__(self, provider: str, exc: Exception):
        self.provider, self.model, self.exc, self.calls = provider, "m", exc, 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise self.exc


def _skill_app(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    sd = tmp_path / ".ironjarvis" / "skills" / "note-taker"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: note-taker\ndescription: takes notes\n---\nWrite tidy notes.", encoding="utf-8",
    )
    return TestClient(create_app(str(tmp_path)))


@pytest.mark.parametrize(
    "provider, exc",
    [
        # A local endpoint (a LiteLLM proxy passing on a content filter).
        ("ollama", ProviderRefusal("the provider's safety check declined to answer (finish_reason content_filter)")),
        # A cloud subscription (Anthropic's classifier) — `partial` as
        # claude-cli's complete() reports it when text streamed to nobody.
        ("claude-cli", _refusal(partial=True)),
    ],
)
def test_a_one_shot_utility_answers_a_refusal_with_its_sentence(tmp_path, monkeypatch, provider, exc):
    """The one-shot utilities (skill apply, terminal assist, the workflow
    builder, share/compact, …) call the adapter directly. A refusal there
    answered the vendor's raw words ("stop_reason refusal") — and from a LOCAL
    endpoint it read as an "answered with an error" outage and raised the
    "not connected" banner. Now: the run's refusal sentence, no banner, no
    other provider tried."""
    client = _skill_app(tmp_path)
    platform = client.app.state.platform
    refusing = _OneShotRefuses(provider, exc)
    others: list[str] = []

    def get(name, model=None):
        if name == provider:
            return refusing
        others.append(name)
        return _Healthy(name)

    banners: list[tuple] = []

    async def not_connected(*a, **kw):
        banners.append((a, kw))

    monkeypatch.setattr(platform.providers, "get", get)
    monkeypatch.setattr(platform.router, "_publish_not_connected", not_connected)
    r = client.post("/skills/note-taker/apply", json={"request": "notes on the plan", "provider": provider})
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "declined to do this task" in detail, detail
    assert "stop_reason" not in detail and "content_filter" not in detail
    assert banners == [], "a refusal is not an outage"
    assert refusing.calls == 1 and others == [], "never retried, never sent elsewhere"


def test_a_one_shot_candidate_that_declines_ends_it(tmp_path, monkeypatch):
    """The one-shot failover chain (a busy primary): a candidate that DECLINES
    ends the call with its sentence — the next connected provider is never
    tried (the router's (A)/(B) rule, lock-step)."""
    from iron_jarvis.daemon import app as app_mod

    client = _skill_app(tmp_path)
    platform = client.app.state.platform
    busy = _OneShotRefuses("anthropic", ProviderError("anthropic API error 503: overloaded", status_code=503))
    declines = _OneShotRefuses("codex-cli", ProviderRefusal("codex-cli: OpenAI's policy check declined the turn"))
    later = _Healthy("google")
    adapters = {"anthropic": busy, "codex-cli": declines, "google": later}
    real_retry = app_mod._complete_with_retry

    async def once(adapter, **kw):  # no backoff sleeps in a test
        return await real_retry(adapter, attempts=1, **kw)

    monkeypatch.setattr(app_mod, "_complete_with_retry", once)
    monkeypatch.setattr(platform.providers, "available", lambda name: name in adapters)
    monkeypatch.setattr(platform.providers, "get", lambda name, model=None: adapters[name])
    r = client.post("/skills/note-taker/apply", json={"request": "notes", "provider": "anthropic"})
    assert r.status_code == 502, r.text
    assert r.json()["detail"].startswith("Codex declined to do this task"), r.text
    assert declines.calls == 1 and later.calls == 0
