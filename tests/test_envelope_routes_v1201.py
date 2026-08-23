"""Envelope routes (Wave A2, v1.201.0): GET profile, POST background probe.

Registered on a bare FastAPI app (the tests/test_helpdocs_v1198.py idiom) —
the coordinating session owns daemon/app.py and wires
``_routes.envelope.register(app, d)`` after this lands. ``d`` is a
SimpleNamespace shaped like create_app's deps object: ``platform.config.home``,
``platform.event_bus``, ``platform.providers``, ``d.fleet``.

Offline throughout: the transport unit test drives a fake adapter, the probe
route tests monkeypatch ``run_quick_battery`` / ``seed_profile`` on the route
module (its bodies resolve the globals at call time). Background completion is
observed by polling the fake bus under a generous deadline — the TestClient
context manager keeps the portal loop alive, so the spawned task really runs
in the background while the test thread waits (no wall-clock performance
assertion anywhere; the deadline is a liveness bound only).
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.daemon.routes import envelope
from iron_jarvis.envelope.profile import CapabilityProfile
from iron_jarvis.envelope.store import save_profile
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.manager import API_PROVIDERS, ProviderManager

STAMP = "2026-08-22T00:00:00+00:00"

#: What the ONE oracle answers True for today — used to parametrize the
#: trusted-GET pin. The route itself never derives a set; it consults
#: ``providers.is_trusted_provider`` (defect 4: two oracles drift).
TRUSTED_TODAY = sorted(
    set(API_PROVIDERS)
    | {"claude-cli", "codex-cli", "grok-cli", "opencode-cli", "mock"}
)


# --------------------------------------------------------------------------- #
# Fakes (the create_app deps shape, minus everything these routes must not touch)
# --------------------------------------------------------------------------- #


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, type: str, payload=None, session_id=None):
        self.published.append((type, dict(payload or {})))

    def of(self, type: str) -> list[dict]:
        return [p for t, p in self.published if t == type]


class FakeAdapter:
    """Records exactly what ``complete`` received; answers a scripted reply."""

    provider = "ollama"
    model = "qwen3:30b"

    def __init__(self, response: LLMResponse | None = None) -> None:
        self.response = response or LLMResponse(text="OK")
        self.seen: list[dict] = []

    async def complete(self, *, system: str, messages, tools):
        # Signature mirrors LLMAdapter.complete exactly: a transport that
        # forwarded an unknown kwarg (response_format) would TypeError here.
        self.seen.append(
            {
                "system": system,
                "messages": [(m.role, m.content) for m in messages],
                "tools": list(tools),
            }
        )
        return self.response


class FakeManager:
    #: THE oracle, bound from the real manager (it reads no instance state) —
    #: so these tests exercise the exact predicate the daemon runs, and a
    #: change to the manager's taxonomy is felt here without a third copy.
    is_trusted_provider = ProviderManager.is_trusted_provider

    def __init__(self, adapter: FakeAdapter | None = None) -> None:
        self.adapter = adapter or FakeAdapter()
        self.calls: list[tuple[str, str | None]] = []

    def get(self, name: str, model: str | None = None):
        self.calls.append((name, model))
        return self.adapter


def build_app(tmp_path, *, ollama_url="http://localhost:11434", custom_url="", fleet=None):
    bus = FakeBus()
    manager = FakeManager()
    cfg = SimpleNamespace(
        home=tmp_path, ollama_base_url=ollama_url, custom_base_url=custom_url
    )
    platform = SimpleNamespace(config=cfg, event_bus=bus, providers=manager)
    d = SimpleNamespace(platform=platform, fleet=fleet)
    app = FastAPI()
    envelope.register(app, d)
    return app, bus, manager


def wait_until(pred, timeout=5.0) -> bool:
    """Poll ``pred`` from the test thread while the portal loop runs the
    background task. Liveness bound only — never a performance assertion."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


# --------------------------------------------------------------------------- #
# The single trusted oracle (defect 4: the route derives NOTHING itself)
# --------------------------------------------------------------------------- #


def test_route_consumes_the_managers_oracle_so_new_clis_need_no_route_edit(tmp_path):
    """The drift pin. ``some-future-cli`` exists nowhere in the route module —
    only the manager's ``*-cli`` rule can trust it. If this GET ever answers
    trusted:false, the route has grown a private trusted set again."""
    app, _bus, _mgr = build_app(tmp_path)
    client = TestClient(app)
    r = client.get("/envelope/some-future-cli/whatever-model")
    assert r.status_code == 200, r.text
    assert r.json()["trusted"] is True
    assert r.json()["profile"]["source"] == "trusted"
    # ...and its probe refuses through the same oracle, before any base_url look.
    r = client.post("/envelope/some-future-cli/whatever-model/probe")
    assert r.status_code == 400, r.text
    assert "by construction" in r.json()["detail"]
    # Local endpoints stay untrusted through the same single wiring.
    for local in ("ollama", "custom", "fleet-x"):
        r = client.get(f"/envelope/{local}/m")
        assert r.status_code == 200, r.text
        assert r.json()["trusted"] is False, local


# --------------------------------------------------------------------------- #
# GET
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("provider", TRUSTED_TODAY)
def test_trusted_get_returns_full_scores_by_construction(tmp_path, provider):
    """Includes ``mock`` (the manager's verdict, consumed not re-derived):
    mock is trusted so the offline suite and the first-run demo see zero
    envelope behavior — the same reason the manager trusts it."""
    app, _bus, _mgr = build_app(tmp_path)
    r = TestClient(app).get(f"/envelope/{provider}/some-model")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trusted"] is True
    assert body["stored"] is False
    prof = body["profile"]
    assert prof["source"] == "trusted"
    assert prof["probed_at"] is None  # granted, never measured — and it says so
    assert prof["tool_protocols"] == {"native": 1.0, "strict_json": 1.0}
    assert prof["json_adherence"] == 1.0


def test_local_get_with_no_store_returns_the_floor_default(tmp_path):
    app, _bus, _mgr = build_app(tmp_path)
    r = TestClient(app).get("/envelope/ollama/qwen3:30b")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trusted"] is False
    assert body["stored"] is False
    prof = body["profile"]
    assert prof["source"] == "default"
    assert prof["probed_at"] is None
    assert prof["tool_protocols"] == {}
    assert prof["context_window"] == 8192
    assert prof["honest_context"] == 4096
    assert prof["chars_per_token"] == 4.0


def test_local_get_returns_the_stored_measurement(tmp_path):
    saved = CapabilityProfile(
        model_id="qwen3:30b",
        provider="ollama",
        source="probed",
        probed_at=STAMP,
        tool_protocols={"native": 0.97, "strict_json": 1.0},
        json_adherence=0.9,
        chars_per_token=3.6,
    )
    save_profile(tmp_path, saved)
    app, _bus, _mgr = build_app(tmp_path)
    r = TestClient(app).get("/envelope/ollama/qwen3:30b")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trusted"] is False
    assert body["stored"] is True
    assert body["profile"]["source"] == "probed"
    assert body["profile"]["tool_protocols"]["native"] == 0.97
    assert body["profile"]["chars_per_token"] == 3.6


def test_model_ids_with_colon_and_slash_round_trip(tmp_path):
    """The {model:path} converter must survive both separators — ``qwen3:30b``
    and a slash-carrying id — on GET and on POST (where a literal ``/probe``
    suffix follows the greedy converter)."""
    model = "qwen3:30b/instruct"
    save_profile(
        tmp_path,
        CapabilityProfile(model_id=model, provider="ollama", source="probed", probed_at=STAMP),
    )
    app, bus, _mgr = build_app(tmp_path)

    async def fake_battery(profile, transport, **kw):
        return CapabilityProfile(
            model_id=profile.model_id, provider=profile.provider,
            source="probed", probed_at=STAMP,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(envelope, "run_quick_battery", fake_battery)
        with TestClient(app) as client:
            r = client.get(f"/envelope/ollama/{model}")
            assert r.status_code == 200, r.text
            assert r.json()["model"] == model
            assert r.json()["profile"]["model_id"] == model

            r = client.post(f"/envelope/ollama/{model}/probe")
            assert r.status_code == 200, r.text
            assert r.json()["model"] == model
            assert bus.of(envelope.PROBE_STARTED)[0]["model"] == model
            assert wait_until(lambda: bus.of(envelope.PROBE_COMPLETED))
            assert bus.of(envelope.PROBE_COMPLETED)[0]["model"] == model

    # A slash-carrying TRUSTED id too (openrouter's namespaced models).
    r = TestClient(app).get("/envelope/openrouter/x-ai/grok-code-fast-1")
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "x-ai/grok-code-fast-1"
    assert r.json()["trusted"] is True


# --------------------------------------------------------------------------- #
# POST refusals
# --------------------------------------------------------------------------- #


def test_probe_refuses_trusted_providers_with_an_honest_400(tmp_path):
    # mock refuses through THIS branch (trusted per the manager's oracle —
    # zero envelope behavior for the offline suite/demo), NOT the no-base-url
    # one; the wording ("gets the trusted envelope by construction") is the
    # oracle's verdict verbatim, so it reads honestly for mock too.
    app, bus, _mgr = build_app(tmp_path)
    client = TestClient(app)
    for provider in ("anthropic", "claude-cli", "mock"):
        r = client.post(f"/envelope/{provider}/whatever/probe")
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert "nothing to measure" in detail
        assert provider in detail
        assert "by construction" in detail
        assert "base_url" not in detail  # the trusted branch, not the endpoint one
    assert bus.published == []  # a refusal starts nothing and says nothing


def test_probe_refuses_when_no_base_url_is_configured(tmp_path):
    # custom_url="" — the custom provider is not configured; a typo'd name has
    # no endpoint either. Both: honest 400, no events.
    app, bus, _mgr = build_app(tmp_path, custom_url="")
    client = TestClient(app)
    for provider in ("custom", "no-such-provider"):
        r = client.post(f"/envelope/{provider}/some-model/probe")
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert provider in detail
        assert "base_url" in detail
    assert bus.published == []


def test_probe_resolves_a_fleet_nodes_base_url(tmp_path):
    class FakeFleet:
        def get(self, node_id):
            if node_id == "spark":
                return SimpleNamespace(base_url="http://10.0.0.5:8000/v1")
            return None

    app, bus, _mgr = build_app(tmp_path, fleet=FakeFleet())

    async def fake_battery(profile, transport, **kw):
        return CapabilityProfile(
            model_id=profile.model_id, provider=profile.provider,
            source="probed", probed_at=STAMP,
        )

    async def fake_seed(provider, model, base_url, **kw):
        fake_seed.calls.append((provider, model, base_url))
        return None

    fake_seed.calls = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(envelope, "run_quick_battery", fake_battery)
        mp.setattr(envelope, "seed_profile", fake_seed)
        with TestClient(app) as client:
            # A DELETED node is a no-base-url refusal, not a crash.
            r = client.post("/envelope/fleet-gone/llama-70b/probe")
            assert r.status_code == 400, r.text
            # A live node probes against ITS base_url.
            r = client.post("/envelope/fleet-spark/llama-70b/probe")
            assert r.status_code == 200, r.text
            assert wait_until(lambda: bus.of(envelope.PROBE_COMPLETED))
    assert fake_seed.calls == [("fleet-spark", "llama-70b", "http://10.0.0.5:8000/v1")]


# --------------------------------------------------------------------------- #
# POST: background run, events, seeding, the pin
# --------------------------------------------------------------------------- #


def test_probe_backgrounds_seeds_first_and_publishes_both_events(tmp_path):
    app, bus, mgr = build_app(tmp_path)
    seen: dict = {}

    async def fake_seed(provider, model, base_url, **kw):
        seen["seed"] = (provider, model, base_url)
        return CapabilityProfile(
            model_id=model, provider=provider, source="seeded",
            tool_protocols={"native": 0.95},
        )

    async def fake_battery(profile, transport, **kw):
        seen["base"] = profile
        seen["home"] = kw.get("home")
        seen["transport"] = transport
        return CapabilityProfile(
            model_id=profile.model_id, provider=profile.provider,
            source="probed", probed_at=STAMP,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(envelope, "seed_profile", fake_seed)
        mp.setattr(envelope, "run_quick_battery", fake_battery)
        with TestClient(app) as client:
            r = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r.status_code == 200, r.text
            assert r.json() == {
                "started": True,
                "provider": "ollama",
                "model": "qwen3:30b",
                "source": "default",  # nothing stored, seed not yet claimed
            }
            # started is published IN the request, before the battery lands.
            assert bus.of(envelope.PROBE_STARTED) == [
                {"provider": "ollama", "model": "qwen3:30b", "source": "default"}
            ]
            assert wait_until(lambda: bus.of(envelope.PROBE_COMPLETED))
            assert bus.of(envelope.PROBE_COMPLETED) == [
                {"provider": "ollama", "model": "qwen3:30b", "source": "probed"}
            ]

    # Seeded from the RAW config slot; the seed became the battery's base.
    assert seen["seed"] == ("ollama", "qwen3:30b", "http://localhost:11434")
    assert seen["base"].source == "seeded"
    assert seen["base"].tool_protocols == {"native": 0.95}
    # home is forwarded so the battery persists under keep-last-good rules.
    assert seen["home"] == tmp_path
    # THE PIN: the adapter came from manager.get with exactly this
    # provider+model — no router, no failover candidates, no one-shot helper
    # (the fake platform has neither a .router nor d._one_shot_complete, so
    # touching either would have blown up the task instead of completing).
    assert mgr.calls == [("ollama", "qwen3:30b")]


def test_probe_with_a_stored_profile_skips_seeding_and_reports_its_source(tmp_path):
    save_profile(
        tmp_path,
        CapabilityProfile(
            model_id="qwen3:30b", provider="ollama", source="probed", probed_at=STAMP,
            tool_protocols={"native": 0.9},
        ),
    )
    app, bus, _mgr = build_app(tmp_path)
    seen: dict = {}

    async def exploding_seed(provider, model, base_url, **kw):
        raise AssertionError("a stored profile must not be re-seeded")

    async def fake_battery(profile, transport, **kw):
        seen["base"] = profile
        return CapabilityProfile(
            model_id=profile.model_id, provider=profile.provider,
            source="partial", probed_at=STAMP,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(envelope, "seed_profile", exploding_seed)
        mp.setattr(envelope, "run_quick_battery", fake_battery)
        with TestClient(app) as client:
            r = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r.status_code == 200, r.text
            assert r.json()["source"] == "probed"  # what the record says NOW
            assert wait_until(lambda: bus.of(envelope.PROBE_COMPLETED))
    assert seen["base"].source == "probed"
    assert seen["base"].tool_protocols == {"native": 0.9}
    assert bus.of(envelope.PROBE_STARTED)[0]["source"] == "probed"
    assert bus.of(envelope.PROBE_COMPLETED)[0]["source"] == "partial"


def test_a_battery_that_blows_up_reports_probe_failed_and_frees_the_key(tmp_path):
    app, bus, _mgr = build_app(tmp_path)

    async def fake_seed(provider, model, base_url, **kw):
        return None  # endpoint answered neither introspection call

    calls = {"n": 0}

    async def battery(profile, transport, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom mid-battery")
        return CapabilityProfile(
            model_id=profile.model_id, provider=profile.provider,
            source="probed", probed_at=STAMP,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(envelope, "seed_profile", fake_seed)
        mp.setattr(envelope, "run_quick_battery", battery)
        with TestClient(app) as client:
            r = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r.status_code == 200, r.text
            assert wait_until(lambda: bus.of(envelope.PROBE_COMPLETED))
            failed = bus.of(envelope.PROBE_COMPLETED)[0]
            assert failed["source"] == "probe_failed"
            assert "boom mid-battery" in failed["error"]
            # The key was released — a re-probe is accepted, not 409'd.
            r = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r.status_code == 200, r.text
            assert wait_until(lambda: len(bus.of(envelope.PROBE_COMPLETED)) == 2)
            assert bus.of(envelope.PROBE_COMPLETED)[1]["source"] == "probed"


def test_second_concurrent_probe_for_the_same_pair_is_409(tmp_path):
    app, bus, _mgr = build_app(tmp_path)
    flag = {"hold": True}

    async def fake_seed(provider, model, base_url, **kw):
        return None

    async def held_battery(profile, transport, **kw):
        while flag["hold"]:
            await asyncio.sleep(0.005)
        return CapabilityProfile(
            model_id=profile.model_id, provider=profile.provider,
            source="probed", probed_at=STAMP,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(envelope, "seed_profile", fake_seed)
        mp.setattr(envelope, "run_quick_battery", held_battery)
        with TestClient(app) as client:
            r1 = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r1.status_code == 200, r1.text
            # Same pair, mid-flight: refused with the honest detail.
            r2 = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r2.status_code == 409, r2.text
            assert "already running" in r2.json()["detail"]
            assert "ollama/qwen3:30b" in r2.json()["detail"]
            # A DIFFERENT model is not gated by this key.
            r3 = client.post("/envelope/ollama/other:7b/probe")
            assert r3.status_code == 200, r3.text
            # Exactly one started event for the gated pair (the 409 fired none).
            gated = [
                p for p in bus.of(envelope.PROBE_STARTED) if p["model"] == "qwen3:30b"
            ]
            assert len(gated) == 1
            flag["hold"] = False
            assert wait_until(lambda: len(bus.of(envelope.PROBE_COMPLETED)) == 2)
            # Completed released the key: the SAME pair probes again cleanly.
            r4 = client.post("/envelope/ollama/qwen3:30b/probe")
            assert r4.status_code == 200, r4.text
            assert wait_until(lambda: len(bus.of(envelope.PROBE_COMPLETED)) == 3)


# --------------------------------------------------------------------------- #
# The transport (unit — this is the no-failover, no-pollution seam)
# --------------------------------------------------------------------------- #


def test_probe_transport_maps_the_reply_and_drops_unknown_kwargs():
    adapter = FakeAdapter(
        response=LLMResponse(
            text='{"tool": "get_weather"}',
            tool_calls=[
                ToolCall(id="c1", name="get_weather",
                         arguments={"city": "Paris", "units": "celsius"})
            ],
            usage={"input_tokens": 11, "output_tokens": 3},
        )
    )
    transport = envelope.probe_transport(adapter)
    reply = asyncio.run(
        transport(
            [
                {"role": "system", "content": "sys prompt"},
                {"role": "user", "content": "call the tool"},
            ],
            tools=[{"type": "function"}],
            # Wave C parameter: the adapter's complete() signature has no such
            # kwarg, so FORWARDING it would TypeError — dropping it is the pin.
            response_format={"type": "json_schema"},
        )
    )
    assert reply.text == '{"tool": "get_weather"}'
    assert reply.tool_calls == [
        {"name": "get_weather", "arguments": {"city": "Paris", "units": "celsius"}}
    ]
    assert reply.usage == {"input_tokens": 11, "output_tokens": 3}
    # The system turn was split out; the user turn crossed as an LLMMessage;
    # tools were forwarded verbatim.
    assert adapter.seen == [
        {
            "system": "sys prompt",
            "messages": [("user", "call the tool")],
            "tools": [{"type": "function"}],
        }
    ]
