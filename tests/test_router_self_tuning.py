"""Self-tuning router (§6 phase-1), offline + deterministic.

Verifies the opt-in local-preference hook: it is a no-op by default, it never
prefers an unavailable local model, it prefers a capable local model only on the
default route, and an explicit non-default provider choice bypasses it entirely.
No network and no DB — we assert on `_resolve`'s routing decision directly.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.events import EventBus
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter

OLLAMA_URL = "http://127.0.0.1:11434/v1"


def _manager(ollama_url: str | None = None) -> ProviderManager:
    return ProviderManager(default_model="m", ollama_base_url=ollama_url)


def _router(manager: ProviderManager, **kw) -> ModelRouter:
    return ModelRouter(manager, "mock", EventBus(), **kw)


def test_default_routing_unchanged_without_oracle() -> None:
    r = _router(_manager())
    adapter, wanted, downgraded = r._resolve(None, None)
    assert wanted == "mock"
    assert not downgraded
    assert adapter.provider == "mock"


def test_oracle_off_when_local_unavailable() -> None:
    # Oracle nominates ollama, but no base_url => unavailable => normal routing.
    r = _router(_manager(), local_oracle=lambda tc: ("ollama", "llama3.1"))
    adapter, wanted, downgraded = r._resolve(None, None, task_class="coder")
    assert wanted == "mock"
    assert adapter.provider == "mock"


def test_oracle_none_pick_is_noop() -> None:
    # Oracle present but declines (returns None) => byte-for-byte unchanged.
    r = _router(_manager(OLLAMA_URL), local_oracle=lambda tc: None)
    adapter, wanted, downgraded = r._resolve(None, None, task_class="coder")
    assert wanted == "mock"
    assert adapter.provider == "mock"


def test_oracle_prefers_capable_local_on_default_route() -> None:
    r = _router(_manager(OLLAMA_URL), local_oracle=lambda tc: ("ollama", "llama3.1"))
    adapter, wanted, downgraded = r._resolve(None, None, task_class="coder")
    assert wanted == "ollama"
    assert not downgraded
    assert adapter.provider == "ollama"


def test_explicit_non_default_provider_bypasses_oracle() -> None:
    # User explicitly picked a non-default provider: honor it (here anthropic is
    # unavailable offline => downgrades to mock, NOT ollama).
    r = _router(_manager(OLLAMA_URL), local_oracle=lambda tc: ("ollama", "llama3.1"))
    adapter, wanted, downgraded = r._resolve("anthropic", None, task_class="coder")
    assert adapter.provider == "mock"
    assert downgraded


def test_oracle_exception_never_breaks_routing() -> None:
    def boom(_tc: str | None) -> tuple[str, str]:
        raise RuntimeError("oracle blew up")

    r = _router(_manager(OLLAMA_URL), local_oracle=boom)
    adapter, wanted, downgraded = r._resolve(None, None, task_class="coder")
    assert adapter.provider == "mock"


# --- a DOWN self-tuned local pick REFUSES: never a fabricated mock answer,
#     and (2026-08-20) never a silent hand-off to the cloud default either ---
from iron_jarvis.providers.adapters.base import LLMResponse  # noqa: E402


class _Adapter:
    def __init__(self, provider: str, *, ok: bool = True) -> None:
        self.provider = provider
        self.model = "m"
        self._ok = ok
        self.calls = 0

    async def complete(self, *, system, messages, tools) -> LLMResponse:
        self.calls += 1
        if not self._ok:
            raise ConnectionError(f"{self.provider} down")
        return LLMResponse(text=f"hi from {self.provider}")


class _FakeManager:
    def __init__(self, adapters: dict, available: dict) -> None:
        self._a = adapters
        self._avail = available

    def get(self, provider: str, model=None):
        return self._a[provider]

    def available(self, provider: str) -> bool:
        return bool(self._avail.get(provider, False))


async def test_failed_local_pick_refuses_and_never_answers_from_the_cloud() -> None:
    """REWRITTEN 2026-08-20 (finding 13). This test used to assert the opposite
    — that a DOWN self-tuned LOCAL pick falls back to the cloud default — and
    that expectation was wrong, not merely outdated. Its point was "never a
    fabricated mock answer", which still holds; but the fallback it pinned is
    the privacy leak itself: the local pick is unreachable, so the ENTIRE
    conversation (this box holds client tax documents) went to a cloud API with
    no consent, disclosed only afterwards as an amber "failover" chip. That is
    exactly the substitution the v1.162.0 refusal exists to prevent — "moving a
    chat from a local endpoint to a cloud API is the user's privacy decision,
    not a routing fallback" (asked and confirmed 2026-08-11) — and the refusal
    had been implemented only as the PRE-RUN availability check, which a
    configured-but-dead endpoint sails straight through.

    Cloud→cloud failover is untouched (test_router_honest_failure.py::
    test_fallback_to_default_uses_defaults_own_model), and a local endpoint
    that ANSWERED (429/500) still fails over — see
    tests/test_local_endpoint_refusal.py.
    """
    cloud = _Adapter("anthropic", ok=True)
    adapters = {
        "ollama": _Adapter("ollama", ok=False),  # self-tuned local pick is DOWN
        "anthropic": cloud,  # healthy cloud default
        "mock": _Adapter("mock", ok=True),
    }
    mgr = _FakeManager(adapters, {"ollama": True, "anthropic": True, "mock": True})
    r = ModelRouter(mgr, "anthropic", EventBus(), local_oracle=lambda tc: ("ollama", "llama3.1"))
    with pytest.raises(Exception, match="ollama isn't connected"):
        await r.complete(system="s", messages=[], tools=[], task_class="coder")
    assert cloud.calls == 0  # the conversation never left the machine
    # ...and no fabricated mock answer either (the original point of this test).
    assert adapters["mock"].calls == 0
