"""Router honesty, v1.232.0 (audit Wave 6, findings R3 + R4).

R3 - MID-STREAM: ``stream()`` never swaps providers after the first frame
(that rule stands), but the death used to be invisible: ``if committed:
raise`` sat BEFORE ``record_failure``/``provider.failed``, so the breaker,
the ledger, ``/diagnostics/reliability`` and the notifier all missed a
provider that died mid-answer while the same death one token earlier counted.
A local transport death also propagated raw - an ``httpx.ReadError`` from a
dropped socket often carries an EMPTY message, which the chat lane rendered
as a blank error line under a half-written answer. And ``provider.failover``
was published only after the alternate's stream was fully consumed, so a
client that walked away mid-answer left no record of the turn that moved.

R4 - THE BREAKER GATES THE PRIMARY: only the failover candidates ever
consulted ``ProviderHealth``, so a dead default cost every request the full
retry ladder while the breaker counted failures nobody read, and no surface
could say "in cooldown".

Converted from ``tests/_audit_20260904/test_router_failover_policy_audit.py``
(Q3/Q4 sections), which is deleted with this change.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import ModelRouter, ProviderHealth


# --------------------------------------------------------------------------- #
# fakes (same shape as tests/test_router_honest_failure.py)
# --------------------------------------------------------------------------- #
class _Fail(LLMAdapter):
    def __init__(self, provider: str, exc: Exception, model: str = "m"):
        self.provider, self.model, self.exc = provider, model, exc
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        raise self.exc

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        raise self.exc
        yield  # pragma: no cover - makes this an async generator


class _MidStreamDie(LLMAdapter):
    """Yields ONE text frame, then raises - the after-first-token failure."""

    def __init__(self, provider: str, exc: Exception, model: str = "m"):
        self.provider, self.model, self.exc = provider, model, exc
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        raise self.exc

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        yield {"type": "text", "text": "partial "}
        raise self.exc


class _Ok(LLMAdapter):
    def __init__(self, provider: str, model: str = "ok-model"):
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text=f"answer from {self.provider}", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        yield {"type": "text", "text": "tok"}
        yield {"type": "final", "response": LLMResponse(text="tok", tool_calls=[], usage={})}


class _Manager:
    def __init__(self, adapters, available=None):
        self.adapters = adapters
        self._available = set(available if available is not None else adapters)

    def available(self, p):
        return p in self._available

    def has_available_api_provider(self):
        return any(p != "mock" for p in self._available)

    def has_available_real_endpoint(self):
        return any(p.startswith("fleet-") for p in self._available)

    def runtime_provider_names(self):
        return sorted(p for p in self.adapters if p.startswith("fleet-"))

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


def _msgs():
    return [LLMMessage(role="user", content="client tax question")]


async def _drain(agen):
    return [f async for f in agen]


# =========================================================================== #
# R3 - a failure AFTER the first token still counts
# =========================================================================== #
async def test_midstream_death_records_the_failure_and_publishes_partial():
    bus = _Bus()
    health = ProviderHealth(threshold=1)
    dying = _MidStreamDie("fleet-custom", httpx.ReadError("connection lost"))
    mgr = _Manager({"fleet-custom": dying, "claude-cli": _Ok("claude-cli")})
    router = ModelRouter(mgr, default_provider="fleet-custom", event_bus=bus, health=health)

    with pytest.raises(ProviderError):
        await _drain(router.stream(system="", messages=_msgs(), tools=[]))

    failed = bus.of(EventType.PROVIDER_FAILED)
    assert failed, "mid-stream death published no provider.failed"
    assert failed[-1]["provider"] == "fleet-custom"
    # `partial: true` is what tells a reader this death happened after the user
    # had already seen text - the same event with a different meaning.
    assert failed[-1]["partial"] is True
    assert health.is_open("fleet-custom"), "the breaker never counted the mid-stream death"
    # The turn was NOT moved to another provider - that rule still stands.
    assert mgr.adapters["claude-cli"].calls == 0


async def test_midstream_local_death_gets_the_honest_incomplete_wording():
    """A dropped socket's ReadError carries an EMPTY message; the chat lane
    rendered that as a blank error line under half an answer."""
    bus = _Bus()
    dying = _MidStreamDie("fleet-custom", httpx.ReadError(""))
    mgr = _Manager({"fleet-custom": dying})
    router = ModelRouter(mgr, default_provider="fleet-custom", event_bus=bus)

    with pytest.raises(ProviderError) as ei:
        await _drain(router.stream(system="", messages=_msgs(), tools=[]))

    text = str(ei.value)
    assert text.strip(), "empty error text reached the user"
    assert "dropped mid-answer" in text
    assert "incomplete" in text
    # NOT the "was not answered" wording - the user is looking at partial text.
    assert "not answered" not in text


async def test_failover_is_published_before_the_client_can_disconnect():
    """complete() publishes provider.failover before returning; stream() used
    to publish only after the whole alternate stream was consumed, so a client
    that walked away mid-answer left no record of a turn that DID move."""
    bus = _Bus()
    primary = _Fail("openai", ProviderError("500", status_code=500))
    mgr = _Manager({"openai": primary, "claude-cli": _Ok("claude-cli")})
    router = ModelRouter(mgr, default_provider="openai", event_bus=bus, deadline_s=1.0)

    agen = router.stream(system="", messages=_msgs(), tools=[])
    first = await agen.__anext__()  # the alternate's FIRST frame
    assert first == {"type": "text", "text": "tok"}
    events = bus.of(EventType.PROVIDER_FAILOVER)
    assert events, "the turn moved to claude-cli with no failover event"
    assert (events[-1]["from"], events[-1]["to"]) == ("openai", "claude-cli")
    await agen.aclose()  # client walked away
    # And it is published ONCE, not again on the way out.
    assert len(bus.of(EventType.PROVIDER_FAILOVER)) == 1


async def test_complete_publishes_failover_for_the_same_turn():
    """The complete() half of the lock-step pair."""
    bus = _Bus()
    primary = _Fail("openai", ProviderError("500", status_code=500))
    mgr = _Manager({"openai": primary, "claude-cli": _Ok("claude-cli")})
    router = ModelRouter(mgr, default_provider="openai", event_bus=bus, deadline_s=1.0)
    await router.complete(system="", messages=_msgs(), tools=[])
    assert bus.of(EventType.PROVIDER_FAILOVER)


# =========================================================================== #
# R4 - the breaker gates the PRIMARY, and says how long
# =========================================================================== #
def _tripped_router(bus, *, cooldown=45.0):
    health = ProviderHealth(threshold=3, cooldown=cooldown)
    dead = _Fail(
        "fleet-rtx6000ada", ProviderError("fleet API error 400: bad", status_code=400)
    )
    mgr = _Manager({"fleet-rtx6000ada": dead})
    router = ModelRouter(
        mgr, default_provider="fleet-rtx6000ada", event_bus=bus, health=health
    )
    return router, health, dead


async def test_open_circuit_refuses_the_primary_by_name_with_the_seconds_left():
    bus = _Bus()
    router, health, dead = _tripped_router(bus)
    for _ in range(3):
        with pytest.raises(ProviderError):
            await router.complete(system="", messages=_msgs(), tools=[])
    assert health.is_open("fleet-rtx6000ada")
    assert dead.calls == 3

    with pytest.raises(ProviderError) as ei:
        await router.complete(system="", messages=_msgs(), tools=[])
    text = str(ei.value)
    assert dead.calls == 3, "the 4th turn was sent to a provider in cooldown"
    assert "fleet-rtx6000ada is in cooldown" in text
    assert "retry in 45 s" in text
    downgraded = bus.of(EventType.PROVIDER_DOWNGRADED)[-1]
    assert downgraded["used"] == "none"
    assert "in cooldown, retry in 45 s" in downgraded["reason"]


async def test_the_stream_lane_refuses_the_same_way():
    """MIRROR: both lanes consult the breaker (lock-step)."""
    bus = _Bus()
    router, health, dead = _tripped_router(bus)
    for _ in range(3):
        with pytest.raises(ProviderError):
            await _drain(router.stream(system="", messages=_msgs(), tools=[]))
    calls_after_trip = dead.calls
    assert health.is_open("fleet-rtx6000ada")

    with pytest.raises(ProviderError) as ei:
        await _drain(router.stream(system="", messages=_msgs(), tools=[]))
    assert dead.calls == calls_after_trip, "stream() called a provider in cooldown"
    assert "is in cooldown, retry in" in str(ei.value)


async def test_a_half_open_circuit_is_allowed_through_as_the_probe_it_is():
    bus = _Bus()
    now = [1000.0]
    health = ProviderHealth(threshold=1, cooldown=30.0, clock=lambda: now[0])
    dead = _Fail("fleet-custom", ProviderError("400", status_code=400))
    mgr = _Manager({"fleet-custom": dead})
    router = ModelRouter(
        mgr, default_provider="fleet-custom", event_bus=bus, health=health
    )
    with pytest.raises(ProviderError):
        await router.complete(system="", messages=_msgs(), tools=[])
    assert health.is_open("fleet-custom")
    now[0] += 31.0  # cooldown elapsed -> HALF-OPEN
    with pytest.raises(ProviderError) as ei:
        await router.complete(system="", messages=_msgs(), tools=[])
    assert dead.calls == 2, "the half-open probe never reached the provider"
    assert "cooldown" not in str(ei.value).lower()


async def test_auto_route_is_never_refused_by_the_breaker():
    """Auto is the one route that may substitute, and it already filters
    candidates by the breaker - refusing it here would break the substitution
    the user explicitly asked for."""
    bus = _Bus()
    health = ProviderHealth(threshold=1)
    health.record_failure("fleet-custom")
    mgr = _Manager({"fleet-custom": _Ok("fleet-custom"), "claude-cli": _Ok("claude-cli")})
    router = ModelRouter(mgr, default_provider="fleet-custom", event_bus=bus, health=health)
    res = await router.complete(system="", messages=_msgs(), tools=[], provider="auto")
    assert "answer from" in res.response.text


def test_health_rows_carry_the_circuit_state(tmp_path):
    """The composer's preflight reads these rows: {open, retry_in_s}."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    rows = client.get("/health").json()["providers"]
    assert rows, "no provider rows on /health"
    for row in rows:
        assert row["circuit"] == {"open": False, "retry_in_s": 0}

    health = app.state.platform.router.health
    name = str(rows[0]["provider"])
    for _ in range(health.threshold):
        health.record_failure(name)
    row = next(
        r for r in client.get("/health").json()["providers"] if r["provider"] == name
    )
    assert row["circuit"]["open"] is True
    assert 0 < row["circuit"]["retry_in_s"] <= int(health.cooldown) + 1
