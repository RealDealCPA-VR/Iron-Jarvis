"""A local box that just answered is not probed again (v1.332.0, speed S2).

The v1.228.0 liveness pre-probe (``ModelRouter._local_liveness``, a ~2.5 s
``GET /v1/models``) ran before EVERY ``complete()`` and ``stream()`` against a
local primary: once per tool round, per final-answer nudge, per language
rewrite and per agent step. The router now remembers, per models URL, when
that box last answered (a probe or a real answer) and trusts it for
``_LIVENESS_TRUST_S`` (15 s) on the router's own clock. Every failure for that
URL forgets it, so a box that died is still refused by name on the very next
call.

Probes are COUNTED, never timed; the clock is the router's injectable
``_clock``. Same fakes shape as tests/test_local_primary_policy_v1228.py.

Mutations (each went red, then restored green):
* drop the memo check in ``_local_liveness`` -> the 6-round test probes 6 times;
* make the window never expire (drop the age comparison) -> the expiry test
  probes once where it must probe twice;
* drop complete()'s note after a success, or stream()'s note at the first
  answer frame -> that lane's 6-round test probes again once the probe's own
  15 s run out;
* drop ``_forget_answered`` from complete()'s / stream()'s primary failure
  branch -> the next call does not probe and runs the dead box;
* drop ``_forget_answered`` from ``_committed_failure`` -> the mid-answer death
  test does not probe again;
* move stream()'s note from the first answer frame to after the drain -> the
  stopped-stream test probes twice.

Review round (v1.332.0), each red then restored: drop the pre-answer
CancelledError/GeneratorExit forget in stream() or complete() -> the Stop
tests; let any box's answer count for every address -> the two-box and the
moved-address tests.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import iron_jarvis.providers.router as R
from iron_jarvis.core.events import EventBus
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
)
from iron_jarvis.providers.router import ModelRouter

_EP = "http://127.0.0.1:11434/v1/chat/completions"
_MODELS = "http://127.0.0.1:11434/v1/models"


class _Clock:
    """The router's clock, moved by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _LocalBox(LLMAdapter):
    """A local adapter with the transport surface the probe reads: an
    ``_endpoint`` and a ``_client()`` whose ``get`` counts probes. ``up``
    decides both the probe's answer and the real call's."""

    def __init__(self, provider: str = "ollama", endpoint: str = _EP) -> None:
        self.provider, self.model = provider, "llama"
        self._endpoint = endpoint
        self.up = True
        self.die_mid_answer = False
        #: Set to an Event to park the call before its first word (Stop tests).
        self.gate = None
        self.entered = None
        self.think_first = False
        self.probes = 0
        self.probed: list[str] = []
        self.calls = 0

    def capabilities(self):
        return {"tool_use": True, "vision": False}

    def _client(self):
        outer = self

        class _Http:
            async def get(self, url, **kw):
                assert url == R.ModelRouter._models_url(outer._endpoint)
                outer.probes += 1
                outer.probed.append(url)
                if not outer.up:
                    raise httpx.ConnectError("All connection attempts failed")
                return object()

        return _Http()

    async def _park(self):
        if self.gate is not None:
            if self.entered is not None:
                self.entered.set()
            await self.gate.wait()

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        await self._park()
        if not self.up:
            raise httpx.ConnectError("All connection attempts failed")
        return LLMResponse(text="answer", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        if self.think_first:
            yield {"type": "thinking", "text": "hmm"}
        await self._park()
        if not self.up:
            raise httpx.ConnectError("All connection attempts failed")
        yield {"type": "text", "text": "tok"}
        if self.die_mid_answer:
            raise httpx.ReadError("")
        yield {"type": "final", "response": LLMResponse(text="tok", tool_calls=[], usage={})}


class _Manager:
    def __init__(self, adapter):
        self.adapter = adapter

    def available(self, p):
        return p == self.adapter.provider

    def has_available_api_provider(self):
        return True

    def has_available_real_endpoint(self):
        return False

    def runtime_provider_names(self):
        return []

    def get(self, p, m=None):
        return self.adapter


def _router(box: _LocalBox, clock: _Clock) -> ModelRouter:
    router = ModelRouter(
        _Manager(box), default_provider=box.provider, event_bus=EventBus(),
        local_policy=lambda: "refuse",
        # No same-adapter retry ladder (its backoff is a real sleep): every
        # retry would blow a zero budget, so a failure surfaces at once.
        deadline_s=0.0,
    )
    router._clock = clock  # noqa: SLF001 — the injectable clock the memo reads
    return router


def _msgs():
    return [LLMMessage(role="user", content="what is in the ledger?")]


async def _call(router: ModelRouter, lane: str):
    if lane == "complete":
        return await router.complete(system="", messages=_msgs(), tools=[])
    out = []
    async for frame in router.stream(system="", messages=_msgs(), tools=[]):
        out.append(frame)
    return out


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_box_that_just_answered_is_not_probed_again(lane):
    """A six-round turn (tool rounds, nudge, rewrite) pays ONE probe. The
    rounds are 5 s apart, 25 s in all, so it is each real ANSWER that keeps
    the box trusted, not only the first probe (whose 15 s run out)."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    for _ in range(6):
        await _call(router, lane)
        clock.now += 5.0
    assert box.calls == 6, "every round still reached the model"
    assert box.probes == 1, f"{lane}: probed {box.probes} times in one 6-round turn"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_the_trust_window_expires(lane):
    """Inside the window no probe; once it has passed, the next call probes."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    await _call(router, lane)
    clock.now += R._LIVENESS_TRUST_S - 0.1
    await _call(router, lane)
    assert box.probes == 1
    clock.now += R._LIVENESS_TRUST_S + 0.1
    await _call(router, lane)
    assert box.probes == 2, f"{lane}: an answer older than the window was still trusted"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_failure_forgets_it_and_the_next_call_probes_and_refuses(lane):
    """The box dies mid-turn: the call that meets it fails (no probe, it was
    trusted), and the call after that probes again and refuses BY NAME
    without touching the model."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    await _call(router, lane)
    assert (box.probes, box.calls) == (1, 1)

    box.up = False
    clock.now += 1.0
    with pytest.raises(ProviderError):
        await _call(router, lane)
    assert box.probes == 1, "a trusted box was probed inside the window"
    calls_after_failure = box.calls

    clock.now += 1.0
    with pytest.raises(ProviderError, match="ollama isn't connected"):
        await _call(router, lane)
    assert box.probes == 2, f"{lane}: the failure did not forget the memo"
    assert box.calls == calls_after_failure, "the dead box was called instead of refused"


async def test_a_stream_that_answered_then_was_stopped_counts_as_answered():
    """Stop after the first words (the consumer closes the stream): neither
    record_success nor a failure runs, yet the box DID answer, so it is
    trusted from THAT moment: 10 s after the stopped answer (20 s after the
    only probe) the next call is still not probed."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    await _call(router, "stream")  # probed once, at t0
    assert box.probes == 1
    clock.now += 10.0
    agen = router.stream(system="", messages=_msgs(), tools=[])
    first = await agen.__anext__()
    assert first.get("type") == "text"
    await agen.aclose()  # the user pressed Stop
    clock.now += 10.0
    await _call(router, "stream")
    assert box.probes == 1, "a stream that answered and was stopped did not count"


async def test_a_stream_that_died_mid_answer_is_probed_again():
    """A death after the first token is a failure too (``_committed_failure``):
    the next call probes."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    box.die_mid_answer = True
    with pytest.raises(Exception):
        await _call(router, "stream")
    box.die_mid_answer = False
    clock.now += 1.0
    await _call(router, "stream")
    assert box.probes == 2, "a mid-answer death did not forget the memo"


async def test_a_cloud_provider_never_touches_the_memo():
    """Anti-vacuity: the memo is for LOCAL boxes only; a cloud adapter has no
    models URL and nothing is recorded."""
    box, clock = _LocalBox(provider="openai"), _Clock()
    router = _router(box, clock)
    await _call(router, "complete")
    assert box.probes == 0
    assert router._answered_at == {}  # noqa: SLF001


# --------------------------------------------------------------------------- #
# Review round (v1.332.0): per address, no failover, and a Stop before the
# first word
# --------------------------------------------------------------------------- #
class _Cloud(LLMAdapter):
    """A connected cloud provider that must never be asked."""

    def __init__(self, provider: str = "openai") -> None:
        self.provider, self.model = provider, "gpt"
        self.calls = 0

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text="from the cloud", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        yield {"type": "text", "text": "from the cloud"}
        yield {"type": "final", "response": LLMResponse(text="x", tool_calls=[], usage={})}


class _Many:
    def __init__(self, adapters: dict) -> None:
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


def _many_router(adapters: dict, default: str, clock: _Clock, policy: str = "refuse"):
    router = ModelRouter(
        _Many(adapters), default_provider=default, event_bus=EventBus(),
        local_policy=lambda: policy, deadline_s=0.0,
    )
    router._clock = clock  # noqa: SLF001
    return router


async def _call_as(router: ModelRouter, lane: str, provider: str):
    if lane == "complete":
        return await router.complete(
            system="", messages=_msgs(), tools=[], provider=provider,
        )
    return [f async for f in router.stream(
        system="", messages=_msgs(), tools=[], provider=provider,
    )]


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_two_boxes_keep_their_own_memo(lane):
    """Two fleet nodes: the first one answering says nothing about the
    second, which is probed (and refused, it is down) on its own."""
    a = _LocalBox(provider="fleet-a", endpoint="http://10.0.0.5:8000/v1")
    b = _LocalBox(provider="fleet-b", endpoint="http://10.0.0.6:8000/v1")
    b.up = False
    clock = _Clock()
    router = _many_router({"fleet-a": a, "fleet-b": b}, "fleet-a", clock)
    await _call_as(router, lane, "fleet-a")
    clock.now += 1.0
    with pytest.raises(ProviderError):
        await _call_as(router, lane, "fleet-b")
    assert (a.probes, b.probes) == (1, 1), "the second box rode the first one's memo"
    assert b.calls == 0, "a dead box was called instead of refused at its probe"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_box_moved_to_another_address_is_probed_at_the_new_one(lane):
    """The memo is keyed by ADDRESS, not by provider name: re-pointing
    ollama at another (dead) machine probes that machine and refuses."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    await _call(router, lane)
    box._endpoint = "http://192.168.1.50:11434/v1/chat/completions"  # noqa: SLF001
    box.up = False
    clock.now += 1.0
    with pytest.raises(ProviderError, match="ollama isn't connected"):
        await _call(router, lane)
    assert box.probed == [_MODELS, "http://192.168.1.50:11434/v1/models"]
    assert box.calls == 1, "the new address was trusted on the old one's answer"


@pytest.mark.parametrize("policy", ["refuse", "failover"])
@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_trusted_box_that_died_refuses_by_name_and_never_fails_over(lane, policy):
    """The memo skips the probe, so the dead box's ATTEMPT is what fails; that
    failure must still be the by-name refusal (the user's rule: an
    unreachable model refuses and names itself), and the connected cloud
    provider is never asked, under either local_primary_policy."""
    box, cloud, clock = _LocalBox(), _Cloud(), _Clock()
    router = _many_router({"ollama": box, "openai": cloud}, "ollama", clock, policy)
    await _call(router, lane)
    assert box.probes == 1
    box.up = False
    clock.now += 1.0
    with pytest.raises(ProviderError, match="ollama"):
        await _call(router, lane)
    assert box.probes == 1, "the trusted box was probed (the memo did not apply)"
    assert box.calls == 2, "the dead box's attempt is the call that fails"
    assert cloud.calls == 0, f"{lane}/{policy}: a dead local box failed over to the cloud"


async def _stopped_before_first_word(router: ModelRouter, box: _LocalBox, lane: str):
    box.gate, box.entered = asyncio.Event(), asyncio.Event()
    task = asyncio.ensure_future(_call(router, lane))
    await box.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    box.gate = box.entered = None


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_a_call_stopped_before_its_first_word_is_probed_again(lane):
    """Stop while the box has not said a word (it may be WHY the user
    pressed Stop): the box is not trusted on the next call."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    await _call(router, lane)
    clock.now += 1.0
    await _stopped_before_first_word(router, box, lane)
    clock.now += 1.0
    await _call(router, lane)
    assert box.probes == 2, f"{lane}: a call stopped before it answered kept the box trusted"


async def test_a_stream_closed_while_still_thinking_is_probed_again():
    """Closed after a thinking frame and before any answer (the consumer
    acloses: GeneratorExit, not CancelledError): not an answer either."""
    box, clock = _LocalBox(), _Clock()
    router = _router(box, clock)
    await _call(router, "stream")
    clock.now += 1.0
    box.think_first, box.gate = True, asyncio.Event()
    agen = router.stream(system="", messages=_msgs(), tools=[])
    first = await agen.__anext__()
    assert first.get("type") == "thinking"
    await agen.aclose()
    box.think_first, box.gate = False, None
    clock.now += 1.0
    await _call(router, "stream")
    assert box.probes == 2, "a stream closed before its first answer kept the box trusted"
