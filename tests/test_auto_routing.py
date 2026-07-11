"""Auto model routing (§6 — the routing model).

Covers the three layers that make "Auto" work end-to-end, all offline:

* ``providers/routing.py`` — the pure cost/capability logic (rank, cheapest,
  derive_tiers, parse helpers, the zero-cost heuristic pre-pass).
* ``providers/router.py`` — the ``ModelRouter`` auto branch: it consults the
  injected ``auto_route`` ONLY when the resolved provider is ``"auto"``, routes
  to the named real model + publishes ``provider.routed``, and falls back to a
  real provider (or downgrades to mock) when the decision is absent/unusable.
  With Auto off the branch is never touched.
* ``manager.get("auto")`` + the ``/routing`` and ``/settings`` HTTP surface.

NOTE ON RANKS: model_rank takes the MIN rank over matched tokens, so a cheap
suffix wins - gpt-4o-mini matches gpt-4o (2) and mini (1) -> rank 1.
These tests assert the SOURCE's real behaviour (verified by running it), not the
map's aspirational comment.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import routing as R
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter


# --------------------------------------------------------------------------- #
# routing.py — pure logic.
# --------------------------------------------------------------------------- #


def test_model_rank_ordering_and_tiers():
    # 1. The cheap/light families rank 1, mid rank 2, frontier rank 3, and the
    #    cost ordering opus > sonnet > haiku holds (the core invariant Auto uses).
    r = R.model_rank
    assert r("anthropic", "claude-opus-4-8") > r("anthropic", "claude-sonnet-4-5")
    assert r("anthropic", "claude-sonnet-4-5") > r("anthropic", "claude-haiku-4-5")
    # rank-1 family (cheap / flat-rate / local-fast).
    for prov, model in [
        ("anthropic", "claude-haiku-4-5"),
        ("google", "gemini-1.5-flash"),
        ("openai", "gpt-fast"),
        ("claude-cli", "claude-subscription"),
        ("x", "mini"),
        ("x", "nano-model"),
    ]:
        assert r(prov, model) == 1, (prov, model)
    # rank-2 mid tier.
    assert r("anthropic", "claude-sonnet-4-5") == 2
    assert r("openai", "gpt-4o") == 2
    # rank-3 frontier.
    assert r("anthropic", "claude-opus-4-8") == 3
    assert r("openai", "gpt-5.5") == 3
    assert r("xai", "grok-4") == 3
    # A cheap tier suffix wins (min-rank over matched tokens): gpt-4o-mini
    # matches both "gpt-4o" (2) and "mini" (1) → 1, a bare gpt-4o stays 2.
    assert r("openai", "gpt-4o-mini") == 1


def test_cheapest_picks_rank1_excludes_mock_and_handles_empty():
    # 2. cheapest over a mixed pool returns a rank-1 model; mock is excluded;
    #    empty / mock-only pools yield None.
    pool = [
        {"provider": "anthropic", "model": "claude-opus-4-8"},
        {"provider": "anthropic", "model": "claude-haiku-4-5"},
        {"provider": "openai", "model": "gpt-4o"},
        {"provider": "openai", "model": "gpt-4o-mini"},
    ]
    pick = R.cheapest(pool)
    assert pick is not None
    assert R.model_rank(*pick) == 1
    # Two rank-1 models (haiku + gpt-4o-mini); the tie-break preference puts
    # openai ahead of anthropic, so gpt-4o-mini wins.
    assert pick == ("openai", "gpt-4o-mini")
    assert R.cheapest([]) is None
    assert R.cheapest([{"provider": "mock", "model": "mock"}]) is None


def test_derive_tiers_over_mixed_and_single():
    # 3. light=cheapest, heavy=most capable, all three keys present.
    tiers = R.derive_tiers(
        [
            {"provider": "anthropic", "model": "claude-opus-4-8"},
            {"provider": "anthropic", "model": "claude-haiku-4-5"},
            {"provider": "openai", "model": "gpt-4o"},
        ]
    )
    assert set(tiers) == {"light", "standard", "heavy"}
    assert tiers["light"] == ("anthropic", "claude-haiku-4-5")
    assert tiers["heavy"] == ("anthropic", "claude-opus-4-8")
    assert R.model_rank(*tiers["light"]) <= R.model_rank(*tiers["heavy"])
    # A single connected model → every tier falls back to it.
    solo = R.derive_tiers([{"provider": "openai", "model": "gpt-4o"}])
    assert solo == {
        "light": ("openai", "gpt-4o"),
        "standard": ("openai", "gpt-4o"),
        "heavy": ("openai", "gpt-4o"),
    }
    assert R.derive_tiers([]) == {}


def test_parse_and_format_helpers():
    # 4. parse_pm / format_pm round-trip; parse_tiers_json parses "prov:model".
    assert R.parse_pm("openai:gpt-4o-mini") == ("openai", "gpt-4o-mini")
    pm = ("openai", "gpt-4o-mini")
    assert R.parse_pm(R.format_pm(pm)) == pm
    parsed = R.parse_tiers_json('{"light":"a:b","heavy":"c:d"}')
    assert parsed["light"] == ("a", "b")
    assert parsed["heavy"] == ("c", "d")


def test_parse_tier_extracts_word_else_default():
    # 5.
    assert R.parse_tier("The tier is HEAVY.") == "heavy"
    assert R.parse_tier("nonsense") == "standard"


def test_heuristic_tier_fast_paths():
    # 6. tools -> heavy; agent task class -> heavy; trivial short chat -> light;
    #    short-but-complex -> None (has a complexity word); code fence -> None.
    assert R.heuristic_tier([{"role": "user", "content": "hi"}], [{"name": "t"}], None) == "heavy"
    assert R.heuristic_tier([], None, "builder") == "heavy"
    assert (
        R.heuristic_tier(
            [{"role": "user", "content": "what time is it in tokyo?"}], None, None
        )
        == "light"
    )
    assert (
        R.heuristic_tier(
            [{"role": "user", "content": "Explain the tradeoffs of microservices in depth."}],
            None,
            None,
        )
        is None
    )
    assert (
        R.heuristic_tier(
            [{"role": "user", "content": "Show me ```code``` please"}], None, None
        )
        is None
    )


# --------------------------------------------------------------------------- #
# router.py — the auto branch (fakes).
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self):
        self.text = "ok"
        self.tool_calls = []
        self.wants_tools = False
        self.usage = {}


class _Adapter:
    def __init__(self, provider, model):
        self.provider = provider
        self.model = model

    async def complete(self, **kwargs):
        return _Resp()


class _Mgr:
    def __init__(self, avail):
        self._a = set(avail)

    def available(self, name):
        return name in self._a

    def has_available_api_provider(self):
        return bool(self._a - {"mock"})

    def get(self, name, model=None):
        return _Adapter(name, model or "default")


def _bus_with_capture():
    bus = EventBus()
    events: list = []
    bus.add_handler(lambda e: events.append(e))
    return bus, events


def _types(events):
    return [e.type for e in events]


def test_auto_on_routes_to_decided_model_and_emits_routed():
    # 7.
    async def decide(system, messages, tools, task_class):
        return {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "tier": "light",
            "classifier": "x:y",
        }

    bus, events = _bus_with_capture()
    r = ModelRouter(_Mgr({"openai", "anthropic"}), lambda: "auto", bus, auto_route=decide)
    res = asyncio.run(r.complete(system="s", messages=[], tools=[], task_class=None))
    assert res.provider == "openai"
    assert res.model == "gpt-4o-mini"
    routed = [e for e in events if e.type == EventType.PROVIDER_ROUTED]
    assert routed, "auto route must publish provider.routed"
    payload = routed[0].payload
    assert payload["tier"] == "light"
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["classifier"] == "x:y"


def test_auto_on_no_decision_falls_back_to_real():
    # 8.
    async def decide(*a):
        return None

    bus, events = _bus_with_capture()
    r = ModelRouter(_Mgr({"openai", "anthropic"}), lambda: "auto", bus, auto_route=decide)
    res = asyncio.run(r.complete(system="s", messages=[], tools=[], task_class=None))
    assert res.provider in {"openai", "anthropic"}
    assert res.provider != "mock"


def test_auto_on_unavailable_target_falls_back_to_real():
    # 9. decision names xai but only openai is connected.
    async def decide(*a):
        return {"provider": "xai", "model": "grok-4", "tier": "heavy", "classifier": "x:y"}

    bus, events = _bus_with_capture()
    r = ModelRouter(_Mgr({"openai"}), lambda: "auto", bus, auto_route=decide)
    res = asyncio.run(r.complete(system="s", messages=[], tools=[], task_class=None))
    assert res.provider == "openai"
    assert res.provider != "mock"


def test_auto_on_no_real_downgrades_to_mock():
    # 10.
    async def decide(*a):
        return None

    bus, events = _bus_with_capture()
    r = ModelRouter(_Mgr(set()), lambda: "auto", bus, auto_route=decide)
    res = asyncio.run(r.complete(system="s", messages=[], tools=[], task_class=None))
    assert res.provider == "mock"
    assert EventType.PROVIDER_DOWNGRADED in _types(events)
    # No real target was chosen, so no provider.routed either.
    assert EventType.PROVIDER_ROUTED not in _types(events)


def test_auto_off_never_consults_auto_route():
    # 11. With Auto off the auto branch is byte-for-byte skipped: auto_route is
    #     never invoked. A structured provider.routed still fires for the (real)
    #     resolved route, tagged reason="default" — never via the auto path.
    called: list = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("auto_route must not be called when Auto is off")

    bus, events = _bus_with_capture()
    r = ModelRouter(_Mgr({"anthropic"}), lambda: "anthropic", bus, auto_route=boom)
    res = asyncio.run(r.complete(system="s", messages=[], tools=[], task_class=None))
    assert res.provider == "anthropic"
    assert called == []
    routed = [e for e in events if e.type == EventType.PROVIDER_ROUTED]
    assert routed and routed[0].payload["reason"] == "default"
    assert routed[0].payload["resolved_provider"] == "anthropic"
    # No tier/classifier keys — those are only on the auto path.
    assert "tier" not in routed[0].payload


# --------------------------------------------------------------------------- #
# manager.get("auto") + HTTP.
# --------------------------------------------------------------------------- #


def test_manager_get_auto_resolves_concretely(monkeypatch):
    # 12. Hermetic: force locally-installed CLI detection off so resolution
    #     depends only on the injected presence, not this host's PATH.
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda binary: False)
    )
    real = ProviderManager(presence_resolver=lambda p: p == "anthropic")
    adapter = real.get("auto")
    assert adapter.provider != "auto"
    assert adapter.provider == "anthropic"
    none = ProviderManager(presence_resolver=lambda p: False)
    assert none.get("auto").provider == "mock"


def test_get_routing_reports_state(tmp_path):
    # 13.
    client = TestClient(create_app(str(tmp_path)))
    resp = client.get("/routing")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("enabled", "routing_model", "connected", "suggested", "tiers"):
        assert key in body
    assert body["enabled"] is False


def test_enable_then_disable_routing(tmp_path):
    # 14.
    client = TestClient(create_app(str(tmp_path)))
    enabled = client.post(
        "/routing/enable", json={"routing_model": "anthropic:claude-haiku-4-5"}
    ).json()
    assert enabled["enabled"] is True
    assert enabled["routing_model"] == "anthropic:claude-haiku-4-5"
    assert client.app.state.platform.config.default_provider == "auto"

    disabled = client.post(
        "/routing/disable", json={"provider": "anthropic", "model": "claude-opus-4-8"}
    ).json()
    assert disabled["enabled"] is False
    assert client.app.state.platform.config.default_provider == "anthropic"


def test_routing_config_persists_across_restart(tmp_path):
    # 15.
    client = TestClient(create_app(str(tmp_path)))
    updated = client.put(
        "/settings", json={"values": {"routing_model": "openai:gpt-4o-mini"}}
    ).json()
    assert "routing_model" in updated["updated"]
    # A fresh app on the SAME root still has the persisted routing model.
    client2 = TestClient(create_app(str(tmp_path)))
    assert client2.get("/routing").json()["routing_model"] == "openai:gpt-4o-mini"
    assert (
        client2.get("/settings").json()["settings"]["routing_model"]
        == "openai:gpt-4o-mini"
    )
