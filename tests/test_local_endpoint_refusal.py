"""A DOWN local endpoint REFUSES — it never fails over to a cloud API.

Finding 13 of the 2026-08-20 sweep: the run-stage half of the v1.162.0
guarantee. ``available("ollama")`` was true whenever a base_url was merely
CONFIGURED, so the pre-run refusal never fired for a dead local server; the
connect then raised ``httpx.ConnectError``, ``is_transient_error`` classified it
transient BY TYPE, and the failover ladder shipped the entire conversation —
client tax data — to the next connected cloud provider, disclosing it only
afterwards.

Two halves, both pinned here:

* ``ModelRouter._refuses_failover`` turns an unreachable-shaped failure on a
  LOCAL primary into the same honest refusal + ``provider.downgraded`` the
  pre-run check emits. THIS is the half that actually closes the leak: it
  measures the real request, with the real credential, against the real
  endpoint, so it can be neither stale nor wrong.
* ``ProviderManager.available`` may refuse a little EARLIER off the fleet
  sampler's cached reachability — but only where the probe asked the same
  question the adapter will (see the false-negative guards below). A false
  "your endpoint isn't connected" is exactly as dishonest as the leak.

The narrowing is pinned too: cloud→cloud failover, and a local endpoint that
ANSWERED with a 429, must keep failing over exactly as before.

SECOND ROUND (the surviving leak): the guard was scoped to "never reached", but
``OpenAIAdapter._client()`` sets a 60s timeout and that one adapter serves BOTH
the ollama and custom slots. A local box that is UP but SLOW — cold-loading a
30B/70B into VRAM — raises ``httpx.ReadTimeout``: transient by TYPE, NOT
unreachable, so the same conversation went to the cloud through the same
fallback (A). Arguably the more common local failure of the two: a dead server
is a one-time ConnectError the user notices, a cold-loading one times out
silently and repeatedly. Pinned below, along with the honesty half — a server
that accepted the connection is not "isn't connected".
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import (
    ModelRouter,
    is_unreachable_error,
    local_failure_kind,
)

OLLAMA_URL = "http://127.0.0.1:11434"


# --------------------------------------------------------------------------- #
# Half 1 — availability tells the truth about a configured-but-dead endpoint.
# --------------------------------------------------------------------------- #
def _mgr(oracle=None, **kw) -> ProviderManager:
    return ProviderManager(
        default_model="m", ollama_base_url=OLLAMA_URL, dynamic_available=oracle, **kw
    )


def test_configured_endpoint_stays_available_with_no_oracle() -> None:
    """Offline/bare path is byte-for-byte unchanged (the whole test suite)."""
    assert _mgr().available("ollama") is True


def test_unprobed_endpoint_defers_to_config_presence() -> None:
    """An oracle with no opinion (sampling off / never probed) changes nothing."""
    assert _mgr(lambda name: None).available("ollama") is True


def test_a_direct_verdict_about_the_slot_is_honoured() -> None:
    """An oracle that answers for the PROVIDER NAME measured the thing the
    router actually does, so it is authoritative in both directions."""
    assert _mgr({"ollama": False}.get).available("ollama") is False
    assert _mgr({"ollama": True}.get).available("ollama") is True


def test_the_ollama_slot_ignores_the_native_api_probe() -> None:
    """FALSE-NEGATIVE GUARD (rejected-review finding). The registry seeds this
    slot as ``kind="ollama"``, so the fleet probe demands ``GET /api/ps`` —
    Ollama's NATIVE api. The slot itself is an OpenAI-compatible adapter on
    ``/v1/chat/completions``, which LM Studio / llama.cpp / vLLM serve while
    404ing ``/api/ps``. Trusting that probe would refuse a WORKING endpoint on
    every single turn, permanently."""
    assert _mgr({"fleet-ollama": False}.get).available("ollama") is True


def test_unconfigured_endpoint_is_unavailable_even_if_oracle_says_reachable() -> None:
    m = ProviderManager(default_model="m", dynamic_available=lambda n: True)
    assert m.available("ollama") is False
    assert m.available("custom") is False


def test_custom_endpoint_uses_the_same_reachability_signal() -> None:
    """A KEYLESS custom endpoint is probed exactly the way the adapter talks to
    it (OpenAI-compatible, no Authorization), so the sampler's verdict is
    measuring the same surface and may be trusted."""
    m = ProviderManager(
        default_model="m",
        custom_base_url="http://box:8000",
        dynamic_available={"fleet-custom": False}.get,
    )
    assert m.available("custom") is False


def test_a_KEYED_custom_endpoint_ignores_the_unauthenticated_probe() -> None:
    """FALSE-NEGATIVE GUARD (rejected-review finding), the sibling of the case
    above. The fleet sampler probes with NO Authorization header, and
    ``fleet/probes._fetch`` turns any non-2xx — a 401 included — into
    "unreachable". Ollama Cloud and every keyed aggregator would therefore read
    as down on every turn, forever, and the Connections page's credentialed
    verify is undone by the next sampler pass. A probe that could not READ the
    endpoint is not evidence the endpoint is DOWN."""
    m = ProviderManager(
        default_model="m",
        custom_base_url="https://ollama.com",
        credential_resolver=lambda _n: "sk-live",
        dynamic_available={"fleet-custom": False}.get,
    )
    assert m.available("custom") is True


def test_a_broken_oracle_never_breaks_availability() -> None:
    def boom(_name: str):
        raise RuntimeError("oracle blew up")

    assert _mgr(boom).available("ollama") is True


def test_the_alias_matches_the_real_fleet_seed_id() -> None:
    """CROSS-MODULE CONTRACT: the manager reads the reachability the fleet
    sampler records for the node the registry SEEDS from the same config key.
    If either side renames, this goes red instead of silently reverting the
    endpoint to 'configured == connected'."""
    cfg = SimpleNamespace(
        ollama_base_url="",
        ollama_model="",
        custom_base_url="http://box:8000",
        custom_model="house",
        fleet_nodes=[],
        home=Path("."),
    )
    registry = FleetRegistry(cfg, persist=lambda *a, **k: None)
    manager = ProviderManager(
        default_model="m",
        custom_base_url="http://box:8000",
        dynamic_available=registry.reachable,
    )
    assert manager.available("custom") is True  # unprobed → defer
    registry.set_reachable("custom", False)  # the sampler failed to reach it
    assert manager.available("custom") is False
    registry.set_reachable("custom", True)
    assert manager.available("custom") is True


# --------------------------------------------------------------------------- #
# Half 2 — the router refuses instead of failing a local endpoint over.
# --------------------------------------------------------------------------- #
class _Down(LLMAdapter):
    """A local server that is not listening: nothing ever reached it."""

    def __init__(self, provider="ollama", model="llama3.1", exc=None) -> None:
        self.provider, self.model = provider, model
        self._exc = exc or httpx.ConnectError("All connection attempts failed")

    async def complete(self, *, system, messages, tools):
        raise self._exc

    async def stream(self, *, system, messages, tools):
        raise self._exc
        yield {}  # pragma: no cover — generator marker


class _Cloud(LLMAdapter):
    def __init__(self, provider="anthropic", model="claude-x") -> None:
        self.provider, self.model = provider, model
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text="cloud answer", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        yield {"type": "delta", "text": "cloud answer"}
        yield {"type": "final", "text": "cloud answer"}


class _Manager:
    def __init__(self, adapters: dict, available=None) -> None:
        self.adapters = adapters
        self._available = set(available or adapters)

    def available(self, provider):
        return provider in self._available

    def has_available_api_provider(self):
        return any(p != "mock" for p in self._available)

    def get(self, provider, model=None):
        return self.adapters[provider]


def _msgs():
    return [LLMMessage(role="user", content="here is my client's K-1")]


def _router(mgr, default="anthropic") -> ModelRouter:
    return ModelRouter(mgr, default_provider=default, event_bus=EventBus())


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Same-adapter transient retries are real here (a ConnectError IS
    transient); only their sleeps are skipped, so the tests stay fast."""
    import iron_jarvis.providers.router as rmod

    async def _sleep(_):
        return None

    monkeypatch.setattr(rmod.asyncio, "sleep", _sleep)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("All connection attempts failed"),
        ConnectionRefusedError("[WinError 10061] actively refused it"),
        RuntimeError("ollama: connection refused"),
    ],
)
async def test_down_local_default_refuses_and_cloud_never_sees_the_turn(exc) -> None:
    """THE PRIVACY DEFECT. Chat sends no provider, so this is the default route."""
    cloud = _Cloud()
    mgr = _Manager({"ollama": _Down(exc=exc), "anthropic": cloud})
    router = _router(mgr, default="ollama")
    with pytest.raises(Exception, match="ollama isn't connected"):
        await router.complete(system="", messages=_msgs(), tools=[])
    assert cloud.calls == 0  # the conversation never left the machine
    kinds = [e.type for e in router.event_bus.history]
    assert EventType.PROVIDER_DOWNGRADED in kinds
    assert EventType.PROVIDER_FAILOVER not in kinds


async def test_down_local_default_refuses_in_the_STREAM_lane() -> None:
    cloud = _Cloud()
    mgr = _Manager({"ollama": _Down(), "anthropic": cloud})
    router = _router(mgr, default="ollama")
    with pytest.raises(Exception, match="ollama isn't connected"):
        async for _ in router.stream(system="", messages=_msgs(), tools=[]):
            pass
    assert cloud.calls == 0
    assert EventType.PROVIDER_DOWNGRADED in [e.type for e in router.event_bus.history]


async def test_explicit_down_local_pick_refuses_too() -> None:
    """Verifier note (a): an EXPLICIT unpinned local pick took fallback (A),
    which runs even for a non-transient error."""
    cloud = _Cloud()
    mgr = _Manager({"custom": _Down("custom", "house-model"), "anthropic": cloud})
    router = _router(mgr)
    with pytest.raises(Exception, match="custom isn't connected"):
        await router.complete(provider="custom", system="", messages=_msgs(), tools=[])
    assert cloud.calls == 0


async def test_down_fleet_node_refuses_rather_than_reaching_for_the_cloud() -> None:
    cloud = _Cloud()
    mgr = _Manager({"fleet-spark": _Down("fleet-spark"), "anthropic": cloud})
    router = _router(mgr, default="fleet-spark")
    with pytest.raises(Exception, match="fleet-spark isn't connected"):
        await router.complete(system="", messages=_msgs(), tools=[])
    assert cloud.calls == 0


# -- the SLOW box: reached, then silent (second-round leak) ------------------ #
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("timed out"),
        httpx.TimeoutException("timed out"),
        asyncio.TimeoutError(),
        TimeoutError("The read operation timed out"),
    ],
)
async def test_slow_local_default_refuses_and_cloud_never_sees_the_turn(exc) -> None:
    """A LOCAL box that ACCEPTED the connection and then ran past the adapter's
    60s read timeout (cold-loading a 30B/70B) must refuse too. Before this
    round the guard asked only "was it ever reached", so this exact case
    returned ``res.provider == "anthropic"`` — the whole conversation, client
    tax data included, delivered to a cloud API."""
    cloud = _Cloud()
    mgr = _Manager({"ollama": _Down(exc=exc), "anthropic": cloud})
    router = _router(mgr, default="ollama")
    with pytest.raises(Exception) as err:
        await router.complete(system="", messages=_msgs(), tools=[])
    assert cloud.calls == 0  # the conversation never left the machine
    # HONEST WORDING: it connected, so "isn't connected" would be a fabrication
    # and would send the user to restart a server that was never down.
    assert "didn't respond in time" in str(err.value)
    assert "isn't connected" not in str(err.value)
    kinds = [e.type for e in router.event_bus.history]
    assert EventType.PROVIDER_DOWNGRADED in kinds
    assert EventType.PROVIDER_FAILOVER not in kinds
    banner = [
        e for e in router.event_bus.history if e.type == EventType.PROVIDER_DOWNGRADED
    ][0]
    assert banner.payload.get("used") == "none"
    assert "not connected" not in (banner.payload.get("reason") or "")


async def test_slow_local_default_refuses_in_the_STREAM_lane() -> None:
    """THE LANE THAT MATTERS: chat streams, and ``committed`` is still False
    when the first token never arrives — so a slow box is squarely in scope."""
    cloud = _Cloud()
    mgr = _Manager(
        {"ollama": _Down(exc=httpx.ReadTimeout("timed out")), "anthropic": cloud}
    )
    router = _router(mgr, default="ollama")
    with pytest.raises(Exception, match="didn't respond in time"):
        async for _ in router.stream(system="", messages=_msgs(), tools=[]):
            pass
    assert cloud.calls == 0
    assert EventType.PROVIDER_DOWNGRADED in [e.type for e in router.event_bus.history]


async def test_a_local_connection_that_breaks_MID_REQUEST_refuses() -> None:
    """The server died while we were talking to it (``httpx.TransportError``
    family). Nothing was answered, so nothing may be substituted — and the
    message says what happened rather than claiming a disconnected endpoint."""
    cloud = _Cloud()
    exc = httpx.RemoteProtocolError("Server disconnected without sending a response")
    mgr = _Manager({"custom": _Down("custom", "house", exc=exc), "anthropic": cloud})
    with pytest.raises(Exception, match="dropped mid-request") as err:
        await _router(mgr).complete(
            provider="custom", system="", messages=_msgs(), tools=[]
        )
    assert cloud.calls == 0
    assert "custom" in str(err.value)


async def test_timeout_refuses_but_a_429_from_the_SAME_local_box_fails_over() -> None:
    """THE TWO RULES MUST NOT COLLAPSE INTO ONE. Silence is a privacy question
    (nothing answered → refuse); a 429 came FROM the server, so the box is up
    and rate-limit arbitrage is ordinary reliability work."""
    slow = _Manager({"ollama": _Down(exc=httpx.ReadTimeout("timed out")), "anthropic": _Cloud()})
    with pytest.raises(Exception, match="didn't respond in time"):
        await _router(slow, default="ollama").complete(
            system="", messages=_msgs(), tools=[]
        )
    assert slow.adapters["anthropic"].calls == 0

    busy = _Manager(
        {
            "ollama": _Boom("ollama", "llama3.1", ProviderError("busy", status_code=429)),
            "anthropic": _Cloud(),
        }
    )
    res = await _router(busy, default="ollama").complete(
        system="", messages=_msgs(), tools=[]
    )
    assert res.provider == "anthropic"
    assert res.reason == "failover"


async def test_a_CLOUD_primary_timeout_still_fails_over() -> None:
    """The broadening is LOCAL-ONLY: a slow cloud API is a reliability problem,
    not a privacy one, and must keep reaching the healthy default."""
    cloud = _Cloud()
    mgr = _Manager({"openai": _Boom(exc=httpx.ReadTimeout("timed out")), "anthropic": cloud})
    res = await _router(mgr).complete(
        provider="openai", model="gpt-dead", system="", messages=_msgs(), tools=[]
    )
    assert res.provider == "anthropic"
    assert cloud.calls == 1


# -- the narrowing: what must KEEP failing over ------------------------------ #
class _Boom(LLMAdapter):
    def __init__(self, provider="openai", model="gpt-dead", exc=None) -> None:
        self.provider, self.model = provider, model
        self._exc = exc or RuntimeError("api error 400: nope")

    async def complete(self, *, system, messages, tools):
        raise self._exc


async def test_cloud_to_cloud_failover_is_untouched() -> None:
    """Guards test_fallback_to_default_uses_defaults_own_model's contract: a
    fix that distinguishes local endpoints must not remove failover wholesale."""
    cloud = _Cloud()
    mgr = _Manager({"openai": _Boom(), "anthropic": cloud})
    res = await _router(mgr).complete(
        provider="openai", model="gpt-dead", system="", messages=_msgs(), tools=[]
    )
    assert res.provider == "anthropic"
    assert cloud.calls == 1


async def test_local_endpoint_that_ANSWERED_still_fails_over() -> None:
    """A 429 came FROM the server, so the endpoint is up and reachable —
    ordinary rate-limit arbitrage, not a privacy transfer."""
    cloud = _Cloud()
    busy = _Boom("ollama", "llama3.1", ProviderError("rate limited", status_code=429))
    mgr = _Manager({"ollama": busy, "anthropic": cloud})
    res = await _router(mgr, default="ollama").complete(
        system="", messages=_msgs(), tools=[]
    )
    assert res.provider == "anthropic"
    assert res.reason == "failover"


async def test_auto_route_may_still_replace_a_down_local_pick() -> None:
    """Explicit Auto is the ONE exception: the user delegated model choice."""
    cloud = _Cloud()
    mgr = _Manager({"ollama": _Down(), "anthropic": cloud})

    async def _auto(*_a, **_kw):
        return {"provider": "ollama", "model": "llama3.1", "tier": "light"}

    router = ModelRouter(
        mgr, default_provider="auto", event_bus=EventBus(), auto_route=_auto
    )
    res = await router.complete(system="", messages=_msgs(), tools=[])
    assert res.provider == "anthropic"


# -- the predicate ----------------------------------------------------------- #
def test_is_unreachable_error_reads_reached_vs_never_reached() -> None:
    assert is_unreachable_error(httpx.ConnectError("All connection attempts failed"))
    assert is_unreachable_error(ConnectionRefusedError("refused"))
    assert is_unreachable_error(RuntimeError("ollama is not running"))
    # A server that ANSWERED is reachable, whatever it answered.
    assert not is_unreachable_error(ProviderError("rate limited", status_code=429))
    assert not is_unreachable_error(ProviderError("bad key", status_code=401))
    assert not is_unreachable_error(RuntimeError("model 'llama3.1' not found"))


def test_local_failure_kind_separates_silence_from_an_answer() -> None:
    """The LOCAL-side predicate. It is a superset of is_unreachable_error — and
    is_unreachable_error is left UNWIDENED on purpose, because it is also the
    cloud-side predicate and this rule is a local-only privacy rule."""
    assert local_failure_kind(httpx.ConnectError("All connection attempts failed")) == (
        "unreachable"
    )
    assert local_failure_kind(ConnectionRefusedError("refused")) == "unreachable"
    # Reached, then silent — the case that survived the first repair round.
    assert local_failure_kind(httpx.ReadTimeout("timed out")) == "timeout"
    assert local_failure_kind(asyncio.TimeoutError()) == "timeout"
    # Reached, then the transport broke.
    assert local_failure_kind(httpx.RemoteProtocolError("server disconnected")) == (
        "interrupted"
    )
    assert local_failure_kind(httpx.ReadError("broken")) == "interrupted"
    # IT ANSWERED, SO IT IS UP — unchanged, and checked before everything else.
    assert local_failure_kind(ProviderError("rate limited", status_code=429)) is None
    assert local_failure_kind(ProviderError("gateway timeout", status_code=504)) is None
    assert local_failure_kind(RuntimeError("model 'llama3.1' not found")) is None
