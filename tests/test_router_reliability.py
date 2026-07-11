"""Best-in-class router reliability: typed transient classification, capability-
aware routing (never land tools on a text-only adapter), the circuit breaker,
and identity-based failover dedup. All offline, no network."""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    parse_retry_after,
)
from iron_jarvis.providers.router import (
    ProviderHealth,
    ModelRouter,
    is_transient_error,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make the same-adapter retry backoff instant so failover tests are fast."""
    import iron_jarvis.providers.router as rmod

    async def _instant(_):
        return None

    monkeypatch.setattr(rmod.asyncio, "sleep", _instant)


# --------------------------------------------------------------------------- #
# Typed transient classification (#1 / #2).
# --------------------------------------------------------------------------- #
def test_provider_error_transient_by_status():
    assert ProviderError("x", status_code=429).transient is True
    assert ProviderError("x", status_code=503).transient is True
    assert ProviderError("x", status_code=400).transient is False
    assert ProviderError("x", status_code=401).transient is False
    # Explicit override wins (subprocess timeout has no status).
    assert ProviderError("timed out", transient=True).transient is True


def test_is_transient_by_type_and_status_not_bare_digits():
    # Typed status → classified by the SET, not the string body.
    assert is_transient_error(ProviderError("boom", status_code=429))
    assert not is_transient_error(ProviderError("boom", status_code=400))
    # Type-based: builtin timeout / connection drops are transient.
    assert is_transient_error(asyncio.TimeoutError())
    assert is_transient_error(ConnectionError("reset"))
    # Phrase fallback for untyped errors (underscore-joined wording included).
    assert is_transient_error(RuntimeError("Error code: 429 - rate_limit_error"))
    assert is_transient_error(RuntimeError("overloaded_error"))
    # A bare 3-digit code embedded in a NON-transient body (token count / id)
    # must NOT be treated as transient — no bare-digit matching.
    assert not is_transient_error(RuntimeError("model produced 429 tokens; invalid schema"))
    assert not is_transient_error(RuntimeError("api error 400: bad request"))


def test_parse_retry_after_seconds_and_garbage():
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date") is None


# --------------------------------------------------------------------------- #
# Circuit breaker (#4).
# --------------------------------------------------------------------------- #
def test_circuit_opens_after_threshold_then_half_opens():
    clock = [0.0]
    h = ProviderHealth(threshold=3, cooldown=30.0, clock=lambda: clock[0])
    assert h.allow("openai")
    h.record_failure("openai")
    h.record_failure("openai")
    assert h.allow("openai")  # 2 fails < threshold
    h.record_failure("openai")
    assert h.is_open("openai")  # 3rd fail OPENs the circuit
    # Still open before cooldown elapses.
    clock[0] = 20.0
    assert h.is_open("openai")
    # HALF-OPEN once the cooldown passes: one probe allowed.
    clock[0] = 31.0
    assert h.allow("openai")
    # A successful probe CLOSES it (counters reset).
    h.record_success("openai")
    assert h.allow("openai")


def test_circuit_reopens_on_failed_probe():
    clock = [0.0]
    h = ProviderHealth(threshold=1, cooldown=10.0, clock=lambda: clock[0])
    h.record_failure("x")
    assert h.is_open("x")
    clock[0] = 11.0
    assert h.allow("x")  # half-open
    h.record_failure("x")  # probe failed → reopen for a fresh cooldown
    assert h.is_open("x")
    clock[0] = 15.0
    assert h.is_open("x")  # cooldown restarted at t=11


# --------------------------------------------------------------------------- #
# Router fakes with capabilities.
# --------------------------------------------------------------------------- #
class _Adapter(LLMAdapter):
    def __init__(self, provider, *, tool_use=True, vision=True, fail=None, echo=None):
        self.provider = provider
        self.model = f"{provider}-m"
        self._tool_use = tool_use
        self._vision = vision
        self._fail = fail  # an exception to raise, or None
        self._echo = echo or provider
        self.calls = 0

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": self._tool_use, "vision": self._vision}

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        return LLMResponse(text=f"from {self._echo}", tool_calls=[], usage={})


class _Manager:
    def __init__(self, adapters, available=None):
        self.adapters = adapters
        self._available = set(available if available is not None else adapters)

    def available(self, p):
        return p in self._available

    def has_available_api_provider(self):
        return any(p != "mock" for p in self._available)

    def get(self, p, model=None):
        return self.adapters[p]


def _tools():
    return [{"name": "write_file", "description": "", "input_schema": {}}]


def _capture_bus():
    bus = EventBus()
    events: list = []
    bus.add_handler(lambda e: events.append(e))
    return bus, events


# --------------------------------------------------------------------------- #
# Capability-aware routing (#3): tools must never land on codex-cli.
# --------------------------------------------------------------------------- #
def test_tool_request_swaps_off_text_only_primary():
    # openai resolves (keyless inheritance) to a TEXT-ONLY codex-cli-like
    # adapter; a tool request must be re-routed to a tool-capable provider.
    codex = _Adapter("codex-cli", tool_use=False, vision=False)
    anth = _Adapter("anthropic", tool_use=True)
    mgr = _Manager({"openai": codex, "anthropic": anth, "mock": _Adapter("mock")})
    bus, events = _capture_bus()
    r = ModelRouter(mgr, "anthropic", bus)
    res = asyncio.run(
        r.complete(provider="openai", system="", messages=[LLMMessage("user", "hi")], tools=_tools())
    )
    assert res.provider == "anthropic"  # never the text-only codex-cli
    assert codex.calls == 0  # the text-only adapter is never even called
    routed = [e for e in events if e.type == EventType.PROVIDER_ROUTED]
    assert routed and routed[0].payload["resolved_provider"] == "anthropic"


def test_tool_failover_excludes_text_only_codex_cli():
    # Primary anthropic is rate-limited; codex-cli is connected but text-only, so
    # a tool request must fail over to grok-cli (tool-capable), skipping codex.
    anth = _Adapter("anthropic", fail=ProviderError("overloaded", status_code=429))
    codex = _Adapter("codex-cli", tool_use=False)
    grok = _Adapter("grok-cli", tool_use=True)
    mgr = _Manager(
        {"anthropic": anth, "codex-cli": codex, "grok-cli": grok, "mock": _Adapter("mock")},
        available={"anthropic", "codex-cli", "grok-cli", "mock"},
    )
    bus, events = _capture_bus()
    r = ModelRouter(mgr, "anthropic", bus)
    res = asyncio.run(
        r.complete(provider="anthropic", system="", messages=[LLMMessage("user", "hi")], tools=_tools())
    )
    assert res.provider == "grok-cli"
    assert codex.calls == 0  # text-only skipped for a tool request
    failover = [e for e in events if e.type == EventType.PROVIDER_FAILOVER]
    assert failover and failover[0].payload["to"] == "grok-cli"


def test_text_request_can_use_codex_cli_failover():
    # WITHOUT tools, the text-only codex-cli is a valid failover target.
    anth = _Adapter("anthropic", fail=ProviderError("overloaded", status_code=429))
    codex = _Adapter("codex-cli", tool_use=False)
    mgr = _Manager(
        {"anthropic": anth, "codex-cli": codex, "mock": _Adapter("mock")},
        available={"anthropic", "codex-cli", "mock"},
    )
    r = ModelRouter(mgr, "anthropic", EventBus())
    res = asyncio.run(
        r.complete(provider="anthropic", system="", messages=[LLMMessage("user", "hi")], tools=[])
    )
    assert res.provider == "codex-cli"


# --------------------------------------------------------------------------- #
# Identity dedup (#5): inherited alias must not be retried twice.
# --------------------------------------------------------------------------- #
def test_failover_dedups_default_alias_by_identity():
    # Primary = claude-cli (what a keyless "anthropic" inherits to). The default
    # provider is "anthropic", which RESOLVES to the SAME claude-cli adapter — the
    # name differs but the identity is the same, so the default fallback must be
    # skipped (not call the same dead adapter again).
    claude = _Adapter("claude-cli", fail=ProviderError("overloaded", status_code=429))
    openai = _Adapter("openai", tool_use=True)

    class _AliasMgr(_Manager):
        def get(self, p, model=None):
            # "anthropic" inherits to the claude-cli adapter (identity aliasing).
            if p == "anthropic":
                return claude
            return self.adapters[p]

    mgr = _AliasMgr(
        {"claude-cli": claude, "openai": openai, "mock": _Adapter("mock")},
        available={"anthropic", "claude-cli", "openai", "mock"},
    )
    res = asyncio.run(
        ModelRouter(mgr, "anthropic", EventBus()).complete(
            provider="claude-cli", system="", messages=[LLMMessage("user", "hi")], tools=[]
        )
    )
    assert res.provider == "openai"  # jumped past the aliased default duplicate
    # 3 = initial + 2 same-adapter retries; NO 4th call, i.e. the default-provider
    # fallback ("anthropic" → the same claude-cli identity) was correctly skipped.
    assert claude.calls == 3
    assert openai.calls == 1


# --------------------------------------------------------------------------- #
# Circuit breaker skips a dead provider on the next request (#4 wired).
# --------------------------------------------------------------------------- #
def test_open_circuit_skips_provider_on_next_request():
    dead = _Adapter("openai", fail=ProviderError("overloaded", status_code=429))
    anth = _Adapter("anthropic", tool_use=True)
    mgr = _Manager(
        {"openai": dead, "anthropic": anth, "mock": _Adapter("mock")},
        available={"openai", "anthropic", "mock"},
    )
    health = ProviderHealth(threshold=1, cooldown=30.0)  # trip immediately
    r = ModelRouter(mgr, "anthropic", EventBus(), health=health)
    # Explicit openai fails → fails over to anthropic, and openai's circuit OPENs.
    res = asyncio.run(
        r.complete(provider="openai", system="", messages=[LLMMessage("user", "hi")], tools=[])
    )
    assert res.provider == "anthropic"
    assert health.is_open("openai")
