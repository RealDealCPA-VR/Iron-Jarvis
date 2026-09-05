"""The local model is safe and understood — router half (v1.228.0, audit Wave 2, R1/R2/R5).

Three findings from the 2026-09-04 router lane, each pinned here so that
reverting its fix goes red:

R1 (S1, privacy) — ``local_primary_policy``. The v1.162.0 rule refused only a
local primary that never ANSWERED; one that answered 429/5xx/404 "still fails
over exactly as before" by design. The premise ("it answered, so it is up")
is false for a proxy: the live 2026-08-28 01:28 event was a LiteLLM proxy
answering 500 because ITS GPU box was unreachable, and the conversation went
to claude-cli. Under the new default ``refuse`` a LOCAL primary that failed
for ANY reason refuses by name (kind ``answered_error`` for the answered
shapes); ``failover`` is the old table. Cloud primaries and Auto are
untouched, both lanes lock-step.

R2 (S3, disclosure) — the failover reason is DERIVED (``failure_reason``),
never the hard-coded "rate limited"/"provider down"; ``RouteResult`` and the
stream's final frame carry ``from``/``why`` so the receipt can name what was
skipped on the default route (``requested`` is "" there by contract). The
chat-lane wire object is pinned in ``tests/test_route_disclosure_v1165.py``
(``test_parity_failover_both_lanes_identical`` asserts from/why on BOTH
lanes by exact equality).

R5 (S3, latency) — a ~2.5 s ``GET /v1/models`` liveness pre-probe before the
primary attempt on a LOCAL primary with a base_url. A dead box refuses at
once instead of after 3×60 s; a LIVE box keeps the same-adapter retry ladder
(the cold-load rescue ``local_failure_kind`` documents). Mock/cloud/Auto and
every adapter without an endpoint never probe, so the offline suite is
untouched.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from iron_jarvis.comm.notifier import format_event
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import (
    ModelRouter,
    RouteResult,
    failure_reason,
)
import iron_jarvis.providers.router as R


# --------------------------------------------------------------------------- #
# fakes (the tests/test_router_honest_failure.py shape)
# --------------------------------------------------------------------------- #
class _Fail(LLMAdapter):
    """Raises ``exc`` on every complete()/stream() call; counts calls."""

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
    out = []
    async for f in agen:
        out.append(f)
    return out


def _router(mgr, default, bus=None, *, policy="refuse", **kw) -> ModelRouter:
    return ModelRouter(
        mgr, default_provider=default, event_bus=bus or _Bus(),
        local_policy=lambda: policy, **kw,
    )


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Same-adapter retries are real; only their sleeps are skipped."""

    async def _sleep(_):
        return None

    monkeypatch.setattr(R.asyncio, "sleep", _sleep)


#: The live 2026-08-28 01:28:43 failure, verbatim shape.
_LIVE_500 = (
    "fleet-custom API error 500: litellm.InternalServerError: "
    "Hosted_vllmException - Cannot connect to host spark-049d:8888 "
    "[Connect call failed (127.0.0.1, 8888)]"
)


# =========================================================================== #
# R1 — local_primary_policy = refuse (the default)
# =========================================================================== #
@pytest.mark.parametrize("status", [500, 502, 503, 504, 529, 429, 404, 400])
@pytest.mark.parametrize("explicit", [False, True], ids=["default-route", "explicit-pick"])
async def test_local_primary_that_answered_an_error_refuses_by_name(status, explicit):
    """THE PRIVACY FIX. Transient (429/5xx) and permanent (404/400) alike:
    the conversation never reaches claude-cli, the refusal names the endpoint
    and the status, and the banner event says nothing stood in."""
    bus = _Bus()
    local = _Fail("fleet-custom", ProviderError(_LIVE_500, status_code=status))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-custom": local, "claude-cli": cloud})
    router = _router(mgr, "fleet-custom", bus, deadline_s=1.0)
    kwargs = {"provider": "fleet-custom"} if explicit else {}
    with pytest.raises(ProviderError) as ei:
        await router.complete(system="", messages=_msgs(), tools=[], **kwargs)
    msg = str(ei.value)
    assert f"fleet-custom answered HTTP {status}" in msg
    assert "no substitute used on purpose (local_primary_policy=refuse)" in msg
    assert "rate limited" not in msg
    assert cloud.calls == 0, "the conversation left the machine"
    assert not bus.of(EventType.PROVIDER_FAILOVER)
    banner = bus.of(EventType.PROVIDER_DOWNGRADED)[-1]
    assert banner["used"] == "none" and banner["requested"] == "fleet-custom"
    assert "answered with an error" in banner["reason"]
    assert "not connected" not in banner["reason"]  # it IS up — say so honestly


@pytest.mark.parametrize("status", [500, 429, 404])
async def test_stream_lane_refuses_the_same_way(status):
    """Chat streams; ``committed`` is False when the error arrives before the
    first frame, so this is the lane that shipped the live leak."""
    bus = _Bus()
    local = _Fail("fleet-custom", ProviderError(_LIVE_500, status_code=status))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-custom": local, "claude-cli": cloud})
    router = _router(mgr, "fleet-custom", bus, deadline_s=1.0)
    with pytest.raises(ProviderError, match=f"fleet-custom answered HTTP {status}"):
        await _drain(router.stream(system="", messages=_msgs(), tools=[]))
    assert cloud.calls == 0
    assert not bus.of(EventType.PROVIDER_FAILOVER)
    assert bus.of(EventType.PROVIDER_DOWNGRADED)[-1]["used"] == "none"


async def test_refusal_quotes_the_endpoints_own_detail_once():
    """The adapter prefixes ``"<provider> API error <status>: "``; the refusal
    already says that, so the detail must not repeat it."""
    local = _Fail("fleet-custom", ProviderError(_LIVE_500, status_code=500))
    mgr = _Manager({"fleet-custom": local, "claude-cli": _Ok("claude-cli")})
    with pytest.raises(ProviderError) as ei:
        await _router(mgr, "fleet-custom", deadline_s=1.0).complete(
            system="", messages=_msgs(), tools=[]
        )
    msg = str(ei.value)
    assert "litellm.InternalServerError" in msg
    assert msg.count("500") == 1, msg
    assert "API error 500" not in msg


async def test_a_status_less_error_on_a_local_primary_also_refuses():
    """"model 'x' not found" as a bare RuntimeError (no status) — reached
    the box, no transport shape, no status: still a local failure, still
    refused under the default policy, worded without inventing a status."""
    local = _Fail("ollama", RuntimeError("model 'llama3.1' not found"))
    cloud = _Ok("anthropic")
    mgr = _Manager({"ollama": local, "anthropic": cloud})
    with pytest.raises(ProviderError) as ei:
        await _router(mgr, "ollama").complete(system="", messages=_msgs(), tools=[])
    assert "ollama answered with an error: model 'llama3.1' not found" in str(ei.value)
    assert "HTTP" not in str(ei.value)
    assert cloud.calls == 0


async def test_a_pinned_local_pick_surfaces_the_raw_error_and_never_fails_over():
    """Strict pin outranks the policy: the pick's OWN error, verbatim."""
    local = _Fail("fleet-custom", ProviderError("busy", status_code=429))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-custom": local, "claude-cli": cloud})
    router = _router(mgr, "claude-cli", strict_pin=lambda: True, deadline_s=1.0)
    with pytest.raises(ProviderError, match="^busy$"):
        await router.complete(
            provider="fleet-custom", system="", messages=_msgs(), tools=[]
        )
    assert cloud.calls == 0


# ---- the 'failover' value: exactly the pre-v1.228.0 table ------------------ #
@pytest.mark.parametrize("status", [500, 429])
async def test_failover_policy_keeps_the_old_behaviour_and_discloses_it(status):
    bus = _Bus()
    local = _Fail("fleet-custom", ProviderError(_LIVE_500, status_code=status))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-custom": local, "claude-cli": cloud})
    router = _router(mgr, "fleet-custom", bus, policy="failover", deadline_s=1.0)
    route = await router.complete(system="", messages=_msgs(), tools=[])
    assert route.provider == "claude-cli" and route.reason == "failover"
    assert route.from_provider == "fleet-custom"
    assert route.why == f"http {status}"
    assert bus.of(EventType.PROVIDER_FAILOVER)[-1]["reason"] == f"http {status}"
    assert cloud.calls == 1


async def test_failover_policy_stream_lane_mirror():
    bus = _Bus()
    local = _Fail("fleet-custom", ProviderError(_LIVE_500, status_code=500))
    mgr = _Manager({"fleet-custom": local, "claude-cli": _Ok("claude-cli")})
    router = _router(mgr, "fleet-custom", bus, policy="failover", deadline_s=1.0)
    frames = await _drain(router.stream(system="", messages=_msgs(), tools=[]))
    final = frames[-1]
    assert final["provider"] == "claude-cli" and final["reason"] == "failover"
    assert final["from"] == "fleet-custom" and final["why"] == "http 500"
    assert bus.of(EventType.PROVIDER_FAILOVER)[-1]["reason"] == "http 500"


async def test_failover_policy_still_refuses_a_local_box_that_never_answered():
    """The policy widens the refusal to answered errors; it never narrows
    the v1.162.0 transport rule."""
    local = _Fail("fleet-custom", httpx.ConnectError("All connection attempts failed"))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-custom": local, "claude-cli": cloud})
    router = _router(mgr, "fleet-custom", policy="failover", deadline_s=1.0)
    with pytest.raises(ProviderError, match="fleet-custom isn't connected"):
        await router.complete(system="", messages=_msgs(), tools=[])
    assert cloud.calls == 0


@pytest.mark.parametrize("value", ["", "ask", "REFUSE", "Failover ", None])
async def test_unknown_policy_values_read_as_refuse_except_the_exact_word(value):
    """Fail-closed: only "failover" (case/space-insensitive) substitutes."""
    local = _Fail("ollama", ProviderError("busy", status_code=429))
    cloud = _Ok("anthropic")
    mgr = _Manager({"ollama": local, "anthropic": cloud})
    router = ModelRouter(
        mgr, default_provider="ollama", event_bus=_Bus(), local_policy=lambda: value,
        deadline_s=1.0,
    )
    if value and value.strip().lower() == "failover":
        assert (await router.complete(system="", messages=_msgs(), tools=[])).provider == "anthropic"
    else:
        with pytest.raises(ProviderError, match="ollama answered HTTP 429"):
            await router.complete(system="", messages=_msgs(), tools=[])
        assert cloud.calls == 0


async def test_default_policy_is_refuse_when_none_is_wired():
    """A bare ``ModelRouter(...)`` (older callers, tests) refuses too."""
    local = _Fail("ollama", ProviderError("busy", status_code=429))
    mgr = _Manager({"ollama": local, "anthropic": _Ok("anthropic")})
    router = ModelRouter(mgr, default_provider="ollama", event_bus=_Bus(), deadline_s=1.0)
    with pytest.raises(ProviderError, match="ollama answered HTTP 429"):
        await router.complete(system="", messages=_msgs(), tools=[])


# ---- what the policy must NOT touch ---------------------------------------- #
async def test_a_cloud_primary_that_answered_an_error_still_fails_over():
    bus = _Bus()
    primary = _Fail("openai", ProviderError("openai API error 500: boom", status_code=500))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"openai": primary, "claude-cli": cloud})
    route = await _router(mgr, "openai", bus, deadline_s=1.0).complete(
        system="", messages=_msgs(), tools=[]
    )
    assert route.provider == "claude-cli" and route.reason == "failover"


async def test_auto_may_still_replace_a_local_pick_that_answered_an_error():
    """Explicit Auto is the ONE exception: the user delegated the choice."""
    local = _Fail("fleet-custom", ProviderError("boom", status_code=500))
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-custom": local, "claude-cli": cloud})

    async def _auto(*_a, **_kw):
        return {"provider": "fleet-custom", "model": "m", "tier": "light"}

    router = ModelRouter(
        mgr, default_provider="auto", event_bus=_Bus(), auto_route=_auto, deadline_s=1.0,
        local_policy=lambda: "refuse",
    )
    res = await router.complete(system="", messages=_msgs(), tools=[])
    assert res.provider == "claude-cli"


# =========================================================================== #
# R2 — the failover reason is derived, and from/why ride the result
# =========================================================================== #
def test_failure_reason_is_derived_from_the_exception():
    assert failure_reason(httpx.ReadTimeout("t")) == "timeout"
    assert failure_reason(httpx.ConnectError("x")) == "unreachable"
    assert failure_reason(httpx.RemoteProtocolError("x")) == "interrupted"
    assert failure_reason(ProviderError("x", status_code=500)) == "http 500"
    assert failure_reason(ProviderError("x", status_code=429)) == "http 429"
    assert failure_reason(ProviderError("x", status_code=400)) == "http 400"
    assert failure_reason(RuntimeError("overloaded_error")) == "transient error"
    assert failure_reason(RuntimeError("nope")) == "error"


@pytest.mark.parametrize(
    "exc, expected",
    [
        pytest.param(httpx.ReadTimeout("read timed out"), "timeout", id="ReadTimeout"),
        pytest.param(ProviderError("openai API error 500", status_code=500), "http 500", id="500"),
        pytest.param(ProviderError("openai API error 503", status_code=503), "http 503", id="503"),
        pytest.param(ProviderError("rl", status_code=429), "http 429", id="429"),
        pytest.param(RuntimeError("overloaded_error"), "transient error", id="untyped"),
    ],
)
async def test_sideways_failover_discloses_what_happened_not_rate_limited(exc, expected):
    """(B) sideways failover used to publish "rate limited" for every
    transient shape. Event, RouteResult and the notifier line now agree."""
    bus = _Bus()
    primary = _Fail("openai", exc)
    mgr = _Manager({"openai": primary, "claude-cli": _Ok("claude-cli")})
    router = _router(mgr, "openai", bus, deadline_s=1.0)
    route = await router.complete(system="", messages=_msgs(), tools=[])
    assert route.provider == "claude-cli"
    assert (route.from_provider, route.why) == ("openai", expected)
    ev = bus.of(EventType.PROVIDER_FAILOVER)[-1]
    assert ev == {"from": "openai", "to": "claude-cli", "reason": expected}
    line = format_event({"type": EventType.PROVIDER_FAILOVER, "payload": ev})
    assert expected in line and "rate limited" not in line


@pytest.mark.parametrize(
    "exc, expected",
    [
        pytest.param(ProviderError("bad request", status_code=400), "http 400", id="400"),
        pytest.param(RuntimeError("model does not exist"), "error", id="untyped-permanent"),
    ],
)
async def test_default_fallback_discloses_the_real_reason_not_provider_down(exc, expected):
    """(A) default-provider fallback (an explicit cloud pick that answered
    with a PERMANENT error reaches the healthy default) said "provider down"
    — a guess. Both lanes now carry the derived reason."""
    bus = _Bus()
    primary = _Fail("openai", exc)
    mgr = _Manager({"openai": primary, "anthropic": _Ok("anthropic")})
    router = _router(mgr, "anthropic", bus, deadline_s=1.0)
    route = await router.complete(provider="openai", system="", messages=_msgs(), tools=[])
    assert route.provider == "anthropic" and route.reason == "failover"
    assert (route.from_provider, route.why) == ("openai", expected)
    assert bus.of(EventType.PROVIDER_FAILOVER)[-1]["reason"] == expected
    assert "provider down" not in str(bus.seen)

    bus2 = _Bus()
    router2 = _router(mgr, "anthropic", bus2, deadline_s=1.0)
    frames = await _drain(
        router2.stream(provider="openai", system="", messages=_msgs(), tools=[])
    )
    assert frames[-1]["from"] == "openai" and frames[-1]["why"] == expected
    assert bus2.of(EventType.PROVIDER_FAILOVER)[-1]["reason"] == expected


async def test_stream_lane_sideways_failover_carries_from_and_why():
    bus = _Bus()
    primary = _Fail("openai", ProviderError("openai API error 500", status_code=500))
    mgr = _Manager({"openai": primary, "claude-cli": _Ok("claude-cli")})
    router = _router(mgr, "openai", bus, deadline_s=1.0)
    frames = await _drain(router.stream(system="", messages=_msgs(), tools=[]))
    final = frames[-1]
    assert final["type"] == "final" and final["provider"] == "claude-cli"
    assert final["requested"] == "" and final["reason"] == "failover"
    assert final["from"] == "openai" and final["why"] == "http 500"
    assert bus.of(EventType.PROVIDER_FAILOVER)[-1]["reason"] == "http 500"


async def test_a_turn_served_as_asked_carries_empty_from_and_why():
    """Additive means silent when nothing failed — the receipt keys off
    presence, so a served-as-asked turn must not name a phantom failure."""
    mgr = _Manager({"openai": _Ok("openai")})
    route = await _router(mgr, "openai").complete(system="", messages=_msgs(), tools=[])
    assert route.reason == "default" and route.from_provider == "" and route.why == ""
    frames = await _drain(_router(mgr, "openai").stream(system="", messages=_msgs(), tools=[]))
    assert frames[-1]["from"] == "" and frames[-1]["why"] == ""
    # And the 3-positional constructor older callers use still works.
    bare = RouteResult(LLMResponse(text="", tool_calls=[], usage={}), "p", "m")
    assert bare.from_provider == "" and bare.why == ""


# =========================================================================== #
# R5 — liveness pre-probe on a LOCAL primary with a base_url
# =========================================================================== #
class _Probeable(_Fail):
    """A local adapter shaped like OpenAIAdapter's transport surface: an
    ``_endpoint`` and a ``_client()`` whose ``get`` the router may call."""

    def __init__(self, provider, exc, *, endpoint, get, tool_use=True):
        super().__init__(provider, exc)
        self._endpoint = endpoint
        self._get = get
        self.probes = 0
        self._tool_use = tool_use

    def capabilities(self):
        return {"tool_use": self._tool_use, "vision": False}

    def _client(self):
        outer = self

        class _Http:
            async def get(self, url, **kw):
                outer.probes += 1
                outer.last_probe = (url, kw)
                return await outer._get(url)

        return _Http()


async def _dead(_url):
    raise httpx.ConnectError("All connection attempts failed")


async def _alive(_url):
    return object()  # any answer at all = a server is listening


_EP = "http://127.0.0.1:11434/v1/chat/completions"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_dead_local_box_refuses_before_the_first_attempt(lane):
    """THE LATENCY FIX: zero adapter calls, no retry ladder — the honest
    "isn't connected" straight from the probe, same banner event."""
    bus = _Bus()
    local = _Probeable("fleet-rtx6000ada", httpx.ReadTimeout("would burn 60 s"),
                       endpoint=_EP, get=_dead)
    cloud = _Ok("claude-cli")
    mgr = _Manager({"fleet-rtx6000ada": local, "claude-cli": cloud})
    router = _router(mgr, "fleet-rtx6000ada", bus)
    with pytest.raises(ProviderError, match="fleet-rtx6000ada isn't connected"):
        if lane == "complete":
            await router.complete(system="", messages=_msgs(), tools=[])
        else:
            await _drain(router.stream(system="", messages=_msgs(), tools=[]))
    assert local.probes == 1
    assert local.last_probe[0] == "http://127.0.0.1:11434/v1/models"
    assert local.calls == 0, "the retry ladder ran against a dead box"
    assert cloud.calls == 0
    assert bus.of(EventType.PROVIDER_DOWNGRADED)[-1]["used"] == "none"
    assert not bus.of(EventType.PROVIDER_ROUTED)  # nothing was routed


async def test_a_probe_that_hangs_refuses_as_a_timeout_not_as_disconnected(monkeypatch):
    monkeypatch.setattr(R, "_LIVENESS_TIMEOUT_S", 0.02)

    async def _hang(_url):
        await asyncio.get_running_loop().create_future()  # never resolves

    local = _Probeable("ollama", RuntimeError("unused"), endpoint=_EP, get=_hang)
    mgr = _Manager({"ollama": local, "anthropic": _Ok("anthropic")})
    with pytest.raises(ProviderError) as ei:
        await _router(mgr, "ollama").complete(system="", messages=_msgs(), tools=[])
    assert "ollama didn't respond in time" in str(ei.value)
    assert "isn't connected" not in str(ei.value)
    assert local.calls == 0


async def test_a_live_box_keeps_the_same_adapter_retry_ladder():
    """The cold-load rescue stays: the probe answered, so the box is up and
    the 60 s read timeouts are worth retrying (3 attempts, then the honest
    timeout refusal)."""
    local = _Probeable("ollama", httpx.ReadTimeout("cold-loading a 70B"),
                       endpoint=_EP, get=_alive)
    mgr = _Manager({"ollama": local, "anthropic": _Ok("anthropic")})
    with pytest.raises(ProviderError, match="ollama didn't respond in time"):
        await _router(mgr, "ollama").complete(system="", messages=_msgs(), tools=[])
    assert local.probes == 1 and local.calls == 3


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(lambda: (_ for _ in ()).throw(httpx.HTTPStatusError(
            "404", request=None, response=None)), id="http-error-raised"),
        pytest.param(lambda: (_ for _ in ()).throw(AttributeError("fake client")), id="fake-client"),
        pytest.param(lambda: (_ for _ in ()).throw(TypeError("no kw")), id="odd-transport"),
    ],
)
async def test_any_answer_or_inconclusive_probe_lets_the_real_attempt_decide(answer):
    """Only connect-shaped failures/timeouts count as dead. A 404/401 came
    from a listening server; a fake client or an odd transport is
    inconclusive — a false "isn't connected" would be a new lie."""

    async def _get(_url):
        return answer()

    local = _Probeable("custom", RuntimeError("unused"), endpoint=_EP, get=_get)
    ok_local = _Ok("custom")
    ok_local._endpoint = _EP  # noqa: SLF001 — mirror the adapter surface
    ok_local._client = local._client  # noqa: SLF001
    mgr = _Manager({"custom": ok_local})
    route = await _router(mgr, "custom").complete(system="", messages=_msgs(), tools=[])
    assert route.provider == "custom" and ok_local.calls == 1


async def test_cloud_mock_and_auto_never_probe():
    class _NeverProbe(_Ok):
        _endpoint = "https://api.openai.com/v1/chat/completions"

        def _client(self):
            raise AssertionError("a cloud provider was probed")

    cloud = _NeverProbe("openai")
    route = await _router(_Manager({"openai": cloud}), "openai").complete(
        system="", messages=_msgs(), tools=[]
    )
    assert route.provider == "openai"

    # Auto: the one route that may replace a dead local pick — no pre-probe.
    local = _Probeable("fleet-custom", httpx.ConnectError("dead"), endpoint=_EP, get=_dead)

    async def _auto(*_a, **_kw):
        return {"provider": "fleet-custom", "model": "m", "tier": "light"}

    router = ModelRouter(
        _Manager({"fleet-custom": local, "claude-cli": _Ok("claude-cli")}),
        default_provider="auto", event_bus=_Bus(), auto_route=_auto, deadline_s=1.0,
    )
    res = await router.complete(system="", messages=_msgs(), tools=[])
    assert res.provider == "claude-cli" and local.probes == 0


async def test_an_adapter_without_an_endpoint_never_probes():
    """Every fake in the offline suite: no ``_endpoint`` → untouched."""
    local = _Ok("fleet-custom")
    route = await _router(_Manager({"fleet-custom": local}), "fleet-custom").complete(
        system="", messages=_msgs(), tools=[]
    )
    assert route.provider == "fleet-custom" and local.calls == 1


async def test_the_probe_reaches_through_the_prompted_tools_wrap():
    """A tool-carrying request on a text-only local adapter is wrapped; the
    probe must find the transport adapter underneath."""
    local = _Probeable("fleet-custom", RuntimeError("unused"), endpoint=_EP, get=_dead,
                       tool_use=False)
    mgr = _Manager({"fleet-custom": local})
    spec = [{"name": "t", "description": "", "input_schema": {"type": "object"}}]
    with pytest.raises(ProviderError, match="fleet-custom isn't connected"):
        await _router(mgr, "fleet-custom").complete(system="", messages=_msgs(), tools=spec)
    assert local.probes == 1 and local.calls == 0


def test_models_url_derivation():
    f = ModelRouter._models_url
    assert f("http://box:8000") == "http://box:8000/v1/models"
    assert f("http://box:8000/v1") == "http://box:8000/v1/models"
    assert f("http://box:8000/v1/chat/completions") == "http://box:8000/v1/models"
    assert f("https://ollama.com/v1/") == "https://ollama.com/v1/models"
    assert f("") is None and f("not a url") is None
