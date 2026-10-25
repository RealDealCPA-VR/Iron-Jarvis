"""No request reaches a keyed gateway without its key (the gateway-key fix).

The user's LiteLLM gateway has required a key since 2026-10-21. Chat sent it;
the background reads of the same server did not — ~139 ``GET /model/info`` an
hour answered ``401 No api key passed in`` — and Iron Jarvis asked it the
Ollama way (``/api/tags``, ``/api/show``, ``/api/version``), which LiteLLM
404s, so the gateway's own word on what each model can do (its ``vision``
model sees) was never read.

Every test here drives REAL code against a stand-in gateway
(``tests/fixtures/fake_litellm_gateway_v1382.py``) that answers like the real
one and records every request (path, whether the key came — never the key).
The end-to-end test configures a scratch app the way the user's is (the custom
slot at the gateway, the key saved through the Connections route) and drives
every caller found: the fleet sampler's pass (probe + routing-table read),
kind detection, the add-node probe form, the model catalog's discovery, both
"Fetch available models" routes, the capability seed, the router's liveness
probe (a real chat turn), OpenCode's passthrough read, the doctor and voice —
then asserts ZERO requests without the key and ZERO ``/api/*`` requests.

The key is a fake test value and is never printed: failure messages name
paths and counts only.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import importlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import persist_config_values, resolve_home
from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet import probes
from iron_jarvis.fleet.endpoint_auth import auth_headers, node_key, root_key
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.fleet.upstreams import RouteTable, parse_model_info
from iron_jarvis.providers import discovery
from iron_jarvis.providers.adapters.base import LLMMessage
from tests.fixtures.fake_litellm_gateway_v1382 import FakeBackend, FakeGateway, model_info_body

#: A FAKE key for the stand-in (never a real one, never printed).
KEY = "test-gateway-key-v1382-not-real"

#: A 1x1 PNG (a picture turn's image).
PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d4944415478da63f8cfc0f01f0005000201ad3c5e"
        "f20000000049454e44ae426082"
    )
).decode("ascii")


@pytest.fixture
def gateway():
    backend = FakeBackend()
    backend.start()
    gw = FakeGateway(KEY, backend=backend.base)
    gw.start()
    try:
        yield gw
    finally:
        gw.stop()
        backend.stop()


@pytest.fixture(autouse=True)
def _fresh_discovery():
    discovery.clear_cache()
    yield
    discovery.clear_cache()


def _registry(tmp_path, *, nodes=(), custom_url="", vault=None) -> FleetRegistry:
    cfg = SimpleNamespace(
        home=str(tmp_path),
        fleet_nodes=[n.model_dump() for n in nodes],
        custom_base_url=custom_url,
        custom_model="",
        ollama_base_url="",
    )
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    store = dict(vault or {})
    reg.set_secret_resolver(store.get)
    return reg


# --------------------------------------------------------------------------- #
# 1. The one header helper
# --------------------------------------------------------------------------- #
def test_auth_headers_spell_the_key_the_way_each_protocol_wants_it():
    assert auth_headers(KEY) == {"Authorization": f"Bearer {KEY}"}
    assert auth_headers("") == {}
    assert auth_headers(KEY, "anthropic") == {"anthropic-version": "2023-06-01", "x-api-key": KEY}
    assert auth_headers("", "anthropic") == {"anthropic-version": "2023-06-01"}


def test_a_topology_child_never_gets_a_key_and_a_bad_vault_is_no_key():
    child = FleetNode(id="gw-vision", base_url="http://spark:8000/v1", parent_id="gw", api_key_name="custom_api_key")
    assert node_key(child, {"custom_api_key": KEY}.get) == ""
    node = FleetNode(id="gw", base_url="http://gw:4000/v1", api_key_name="custom_api_key")
    assert node_key(node, {"custom_api_key": KEY}.get) == KEY
    assert node_key(node, lambda n: 1 / 0) == ""
    assert node_key(node, None) == ""


def test_root_key_matches_one_server_and_never_another():
    assert root_key("http://100.66.161.52:4000/v1") == root_key("http://100.66.161.52:4000")
    assert root_key("HTTP://GW:4000/v1/chat/completions") == root_key("http://gw:4000")
    assert root_key("http://gw:80/v1") == root_key("http://gw/v1")
    assert root_key("http://gw:4000") != root_key("http://gw:4001")
    assert root_key("http://gw:4000") != root_key("http://other:4000")
    assert root_key("") == ""


def test_the_custom_slot_reads_the_key_the_connections_page_saves(tmp_path):
    """ONE lookup: the custom slot's fleet node names the vault entry the
    ``custom`` connection is saved under (and the ``custom`` chat adapter
    reads), so a background read and a chat call send the same key."""
    from iron_jarvis.connections.specs import BUILTIN_SPECS

    reg = _registry(tmp_path, custom_url="http://gw:4000/v1")
    seed = next(n for n in reg.seeded() if n.id == "custom")
    assert seed.api_key_name == BUILTIN_SPECS["custom"].key_secret_name == "custom_api_key"


def test_the_registry_finds_the_key_for_an_address_and_only_for_that_server(tmp_path):
    other = FleetNode(id="box", base_url="http://192.168.1.9:8000/v1", api_key_name="box_key", routable=True)
    reg = _registry(
        tmp_path, nodes=[other], custom_url="http://gw:4000/v1",
        vault={"custom_api_key": KEY, "box_key": "box-secret"},
    )
    assert reg.headers_for_url("http://gw:4000/model/info".rsplit("/model/info", 1)[0]) == {
        "Authorization": f"Bearer {KEY}"
    }
    assert reg.key_for_url("http://GW:4000") == KEY
    assert reg.key_for_url("http://gw:4001/v1") == ""  # another server: nothing
    assert reg.key_for_url("http://192.168.1.9:8000") == "box-secret"
    assert reg.slot_key("custom") == KEY and reg.slot_key("ollama") == ""


def test_a_second_entry_for_the_same_server_takes_its_saved_key(tmp_path):
    """The gateway saved as the custom slot (keyed) AND as a fleet node with
    no key name is one server with one key — the second used to be asked
    with none. A node on another server never borrows it."""
    dup = FleetNode(id="dgx", base_url="http://gw:4000", routable=True)
    far = FleetNode(id="far", base_url="http://gw2:4000", routable=True)
    reg = _registry(tmp_path, nodes=[dup, far], custom_url="http://gw:4000/v1", vault={"custom_api_key": KEY})
    assert reg.key_for(reg.get("dgx")) == KEY
    assert reg.key_for(reg.get("far")) == ""
    kid = FleetNode(id="dgx-x", base_url="http://gw:4000", parent_id="dgx")
    assert reg.key_for(kid) == ""


def test_ollama_style_addresses_only_for_an_ollama_server(tmp_path):
    lite = FleetNode(id="lite", base_url="http://lite:4000", kind="litellm")
    olla = FleetNode(id="tower", base_url="http://tower:11434", kind="ollama")
    unk = FleetNode(id="new", base_url="http://new:8000", kind="unknown")
    cfg = SimpleNamespace(
        home=str(tmp_path), fleet_nodes=[n.model_dump() for n in (lite, olla, unk)],
        custom_base_url="http://gw:4000/v1", custom_model="", ollama_base_url="http://o:11434",
    )
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    assert reg.ollama_style_ok("http://lite:4000/v1") is False
    assert reg.ollama_style_ok("http://tower:11434") is True
    assert reg.ollama_style_ok("http://o:11434/v1") is True  # the Ollama slot
    assert reg.ollama_style_ok("http://new:8000") is None  # never detected
    assert reg.ollama_style_ok("http://gw:4000") is None  # the custom slot: not known yet
    reg.set_routes("custom", parse_model_info(model_info_body()))
    assert reg.ollama_style_ok("http://gw:4000") is False  # a proxy that told us its table
    assert reg.ollama_style_ok("http://nobody:1") is None


# --------------------------------------------------------------------------- #
# 2. Capabilities from the proxy's own /model/info
# --------------------------------------------------------------------------- #
def test_model_info_capabilities_are_read_per_served_name():
    table = parse_model_info(model_info_body())
    # (v1.387.0: `max_output` joined the caps — the fixture states none.)
    assert table.caps("vision") == {"vision": True, "tool_use": True, "context_window": 262_144, "max_output": None}
    assert table.caps("glm-5.3-flash") == {"vision": False, "tool_use": True, "context_window": 1_000_000, "max_output": None}
    # a name the table does not list stays UNKNOWN (the hidden "fleet" alias)
    assert table.caps("fleet") == {"vision": None, "tool_use": None, "context_window": None, "max_output": None}
    assert table.caps("") == {"vision": None, "tool_use": None, "context_window": None, "max_output": None}


def test_capabilities_unknown_unless_stated_and_shared_deployments_must_agree():
    body = {"data": [
        {"model_name": "a", "litellm_params": {"model": "x"}, "model_info": {"supports_vision": None, "max_input_tokens": None}},
        {"model_name": "b", "litellm_params": {"model": "x"}, "model_info": {"supports_vision": True, "max_input_tokens": 1000}},
        {"model_name": "b", "litellm_params": {"model": "y"}, "model_info": {"supports_vision": False, "max_input_tokens": 500}},
        {"model_name": "c", "litellm_params": {"model": "x"}, "model_info": {"supports_vision": "yes", "max_input_tokens": "0"}},
        {"model_name": "img-*", "litellm_params": {"model": "x"}, "model_info": {"supports_vision": True, "max_input_tokens": 4096}},
    ]}
    table = parse_model_info(body)
    assert table.caps("a") == {"vision": None, "tool_use": None, "context_window": None, "max_output": None}
    assert table.caps("b") == {"vision": None, "tool_use": None, "context_window": 500, "max_output": None}
    assert table.caps("c") == {"vision": None, "tool_use": None, "context_window": None, "max_output": None}
    assert table.caps("img-big") == {"vision": True, "tool_use": None, "context_window": 4096, "max_output": None}


def test_capabilities_survive_the_saved_table_and_an_old_saved_table_reads_unknown():
    table = parse_model_info(model_info_body())
    again = RouteTable.from_json(json.loads(json.dumps(table.to_json())))
    assert again.caps("vision") == table.caps("vision")
    assert again.signature() == table.signature()
    old = RouteTable.from_json({"entries": [{"alias": "vision", "model": "m", "place": "local"}]})
    assert old.caps("vision") == {"vision": None, "tool_use": None, "context_window": None, "max_output": None}


def test_a_stated_capability_reaches_the_adapter_and_a_denial_changes_nothing():
    from iron_jarvis.fleet.adapter import FleetAdapter, node_capabilities

    rec = FleetNode(id="custom", base_url="http://gw:4000/v1")
    caps = parse_model_info(model_info_body()).caps
    assert node_capabilities(rec, "vision", caps) == {"tool_use": True, "vision": True}
    # the table says glm cannot see: the record's answer (unverified = False) stands
    assert node_capabilities(rec, "glm-5.3-flash", caps)["vision"] is False
    seen = rec.model_copy(update={"vision": True})
    assert node_capabilities(seen, "glm-5.3-flash", caps)["vision"] is True  # never forced False
    assert node_capabilities(rec, "fleet", caps) == {"tool_use": False, "vision": False}  # unknown
    assert node_capabilities(rec, "vision", lambda m: 1 / 0) == {"tool_use": False, "vision": False}
    adapter = FleetAdapter(node=rec, model="vision", caps=caps)
    assert adapter.capabilities()["vision"] is True


def test_the_context_window_ladder_reads_the_stated_window(tmp_path):
    from iron_jarvis.daemon.chat_turn import _context_window_source

    reg = _registry(tmp_path, custom_url="http://gw:4000/v1")
    reg.set_routes("custom", parse_model_info(model_info_body()))
    d = SimpleNamespace(platform=SimpleNamespace(
        config=SimpleNamespace(default_provider="custom", default_model="", model_context_windows={}),
        providers=None, fleet=reg,
    ))
    assert _context_window_source(d, "custom", "vision") == (262_144, "endpoint")
    assert _context_window_source(d, "fleet-custom", "glm-5.3-flash") == (1_000_000, "endpoint")
    assert _context_window_source(d, "custom", "fleet") == (None, "default")  # unlisted: unknown


# --------------------------------------------------------------------------- #
# 3. Each reader against the stand-in
# --------------------------------------------------------------------------- #
def test_detection_is_keyed_and_a_keyed_gateway_is_never_asked_ollamas_address(gateway):
    kind, _ = probes.detect_kind(gateway.base, headers=auth_headers(KEY))
    assert kind == "litellm"
    assert gateway.unkeyed() == [] and gateway.ollama_style() == []
    before = len(gateway.log())
    kind, reason = probes.detect_kind(gateway.base)  # no key known
    assert (kind, reason) == ("unknown", probes.NEEDS_KEY_REASON)
    asked = gateway.paths()[before:]
    assert asked == ["GET /model/info"], asked  # one refused read, then stop
    assert gateway.ollama_style() == []


def test_a_probe_pass_carries_the_key_on_every_request(gateway):
    node = FleetNode(id="gw", base_url=gateway.base + "/v1", kind="litellm")
    snap, children = probes.probe_node(node, headers=auth_headers(KEY))
    assert snap.status == "online"
    assert [c.alias for c in children] == ["glm-5.3-flash", "glm-5.3-flash-think", "glm-5.3-flash-fast", "vision"]
    assert gateway.unkeyed() == [] and gateway.ollama_style() == []
    assert {"GET /model/info", "GET /metrics"} <= set(gateway.paths())
    health = probes.litellm_child_health(node, headers=auth_headers(KEY))
    assert set(health.values()) <= {"healthy", "unknown"} and health
    assert gateway.unkeyed() == []


def test_a_refused_listing_is_never_retried_at_ollamas_address(gateway):
    """Discovery used to follow ANY error status to ``/api/tags`` — a keyed
    gateway's 401 included."""
    assert discovery._fetch("fleet-gw", lambda: "", gateway.base + "/v1", "openai") == []
    assert gateway.ollama_style() == []
    assert discovery._fetch("fleet-gw", lambda: KEY, gateway.base + "/v1", "openai") == list(
        ("glm-5.3-flash", "glm-5.3-flash-think", "glm-5.3-flash-fast", "vision")
    )
    with pytest.raises(httpx.HTTPStatusError):
        discovery.list_endpoint_models(gateway.base + "/v1", "")
    assert gateway.ollama_style() == []


def test_an_old_ollama_that_answers_404_is_still_asked_its_own_address(monkeypatch):
    asked = []

    def fake_get(url, headers):
        asked.append(url)
        if url.endswith("/v1/models"):
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))
        return {"models": [{"name": "llama3:8b"}]}

    monkeypatch.setattr(discovery, "_get_json", fake_get)
    assert discovery._fleet_endpoint_models("http://tower:11434", "", "openai") == ["llama3:8b"]
    assert asked[-1].endswith("/api/tags")
    asked.clear()
    with pytest.raises(httpx.HTTPStatusError):
        discovery._fleet_endpoint_models("http://tower:11434", "", "openai", ollama_ok=False)
    assert not any(u.endswith("/api/tags") for u in asked)


def test_the_typed_address_ladder_leaves_ollamas_address_out_for_a_known_proxy(monkeypatch):
    from iron_jarvis.daemon.routes.connections import _model_list_urls

    assert _model_list_urls("http://gw:4000/v1")[-1] == "http://gw:4000/api/tags"
    assert not any(u.endswith("/api/tags") for u in _model_list_urls("http://gw:4000/v1", ollama_ok=False))
    asked = []

    def fake_get(url, headers):
        asked.append(url)
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    monkeypatch.setattr(discovery, "_get_json", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        discovery.list_endpoint_models("http://gw:4000/v1", KEY, ollama_ok=False)
    assert asked == ["http://gw:4000/v1/models"]


def test_the_liveness_probe_carries_the_adapters_own_key():
    """The router's pre-probe used to GET ``/v1/models`` bare on every turn's
    first call; it now carries the key the adapter's chat calls send — the
    Bearer token for an OpenAI-compatible node, ``x-api-key`` for an
    Anthropic-compatible one."""
    from iron_jarvis.fleet.adapter import adapter_for
    from iron_jarvis.providers.router import ModelRouter

    class _Client:
        def __init__(self):
            self.calls = []

        async def get(self, url, timeout=None, headers=None):
            self.calls.append((url, dict(headers or {})))
            return httpx.Response(200)

    for protocol, want in (
        ("openai", {"Authorization": f"Bearer {KEY}"}),
        ("anthropic", {"x-api-key": KEY, "anthropic-version": "2023-06-01"}),
    ):
        node = FleetNode(id="gw", base_url="http://gw:4000/v1", protocol=protocol, routable=True)
        adapter = adapter_for(node, credential=lambda: KEY)
        client = _Client()
        adapter._http = client
        router = ModelRouter.__new__(ModelRouter)
        router._answered_at = {}
        router._clock = time.monotonic
        assert asyncio.run(router._local_liveness(adapter)) is None
        assert client.calls and client.calls[0][1] == want, protocol
        assert adapter.probe_headers() == want
    keyless = adapter_for(FleetNode(id="lan", base_url="http://lan:8000/v1"))
    assert keyless.probe_headers() == {}


def test_the_seed_reads_the_gateways_own_capabilities_and_never_api_show(gateway):
    from iron_jarvis.envelope.seed import seed_profile

    async def _seed(model, **kw):
        return await seed_profile("custom", model, gateway.base + "/v1", **kw)

    prof = asyncio.run(_seed("vision", headers=auth_headers(KEY)))
    assert prof is not None and prof.vision is True
    assert prof.context_window == 262_144 and prof.honest_context == 32768
    assert prof.tool_protocols == {"native": 0.95}
    glm = asyncio.run(_seed("glm-5.3-flash", headers=auth_headers(KEY)))
    assert glm.vision is False and glm.context_window == 1_000_000
    assert gateway.unkeyed() == [] and gateway.ollama_style() == []
    before = len(gateway.log())
    assert asyncio.run(_seed("vision")) is None  # no key known: refused, nothing guessed
    assert gateway.paths()[before:] == ["GET /model/info"]  # and nothing more is asked
    assert gateway.ollama_style() == []


def test_opencodes_proxy_read_carries_the_key_of_the_saved_endpoint(gateway, tmp_path):
    from iron_jarvis.providers import opencode as oc

    reg = _registry(tmp_path, custom_url=gateway.base + "/v1", vault={"custom_api_key": KEY})
    got = oc.local_models(
        runner=lambda argv, timeout=25.0: (0, "gw/glm-5.3-flash\ngw/vision\n", ""),
        which=lambda name: "opencode-stand-in",
        providers={"gw": gateway.base + "/v1"},
        headers_for=reg.headers_for_url,
    )
    assert got == ["gw/glm-5.3-flash", "gw/vision"]
    assert gateway.paths() == ["GET /model/info"]
    assert gateway.unkeyed() == []


def test_the_suite_never_reads_the_developers_opencode_config():
    """Measured on the user's PC before this fix: ONE bare app lifespan (what
    ~440 ``with TestClient(...)`` blocks in this suite start) sent
    ``GET <gateway>/model/info`` with no key, because ``warm_opencode`` read
    the developer's real OpenCode config, which points at the gateway.
    ``tests/conftest.py`` empties that path list for the whole session."""
    from iron_jarvis.providers import opencode as oc

    assert oc._config_paths() == []
    assert oc.config_providers() == {}


# --------------------------------------------------------------------------- #
# 4. End to end: a scratch app configured like the user's
# --------------------------------------------------------------------------- #
def _scratch_app(tmp_path, gateway):
    home = resolve_home(Path(tmp_path).resolve())
    home.mkdir(parents=True, exist_ok=True)
    persist_config_values(home, {
        "custom_base_url": gateway.base + "/v1",
        "custom_model": "glm-5.3-flash",
        "default_provider": "custom",
        "default_model": "glm-5.3-flash",
        # the loop is driven by hand (sample_once) so each caller's requests
        # are attributable; the loop runs the same _probe_one pass.
        "fleet_sampling_enabled": False,
    })
    app = create_app(str(tmp_path))
    # The key is saved the way the Connections page saves it — before
    # anything starts, as on the user's install.
    r = TestClient(app).post("/connections/custom/key", json={"key": KEY})
    assert r.status_code == 200, r.status_code
    return app


def _wait(check, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return check()


def test_every_gateway_caller_sends_the_saved_key_and_never_asks_ollamas_addresses(
    gateway, tmp_path, monkeypatch
):
    from iron_jarvis.daemon.routes import envelope as envelope_routes
    from iron_jarvis.envelope.profile import CapabilityProfile
    from iron_jarvis.providers import opencode as oc

    battery_done = []

    async def fake_battery(profile, transport, **kw):
        battery_done.append((profile.provider, profile.model_id, profile.vision, profile.context_window))
        return CapabilityProfile(model_id=profile.model_id, provider=profile.provider, source="probed")

    monkeypatch.setattr(envelope_routes, "run_quick_battery", fake_battery)

    app = _scratch_app(tmp_path, gateway)
    d = app.state.d
    reached: dict[str, list[str]] = {}

    def step(name, fn):
        n0 = len(gateway.log())
        out = fn()
        reached[name] = gateway.paths()[n0:]
        assert reached[name], f"{name} never reached the gateway"
        return out

    with TestClient(app) as client:
        # the fleet sampler's pass: the probe AND the routing-table read
        step("sampler pass", lambda: d.fleet_sampler.sample_once(force=True))
        assert "GET /model/info" in reached["sampler pass"]
        # kind detection (the Fleet page's Re-detect) — keyed, so it says LiteLLM
        out = step("detect", lambda: client.post("/fleet/nodes/custom/detect").json())
        assert out["kind"] == "litellm"
        # the next pass probes it as a LiteLLM proxy (/model/info, /metrics)
        step("litellm pass", lambda: d.fleet_sampler.sample_once(force=True))
        # the add-node form's probe: by key name, and by address alone
        step("probe form", lambda: client.post(
            "/fleet/probe", json={"base_url": gateway.base, "api_key_name": "custom_api_key"}).json())
        step("probe form (no key name)", lambda: client.post(
            "/fleet/probe", json={"base_url": gateway.base}).json())
        # the model catalog (GET /models): discovery of custom + fleet-custom
        rows = step("catalog", lambda: client.get("/models").json()["models"])
        assert {"vision", "glm-5.3-flash"} <= {m["model"] for m in rows if m["provider"] == "custom"}
        # both "Fetch available models" routes, with no key typed
        got = step("fetch models", lambda: client.post(
            "/connections/endpoints/models", json={"base_url": gateway.base + "/v1"}).json())
        assert got["error"] is None and "vision" in got["models"]
        got = step("endpoint-models", lambda: client.post(
            "/providers/endpoint-models", json={"base_url": gateway.base + "/v1", "api_key": ""}).json())
        assert "vision" in got.get("models", [])
        # the capability seed (Measure on the Connections page)
        def _envelope():
            r = client.post("/envelope/custom/vision/probe")
            assert r.status_code == 200, r.status_code
            assert _wait(lambda: battery_done)
        step("envelope seed", _envelope)
        assert battery_done[0][2:] == (True, 262_144)  # the gateway's own word
        # a real chat turn: the router's liveness probe + the completion
        def _chat():
            r = client.post("/chat", json={
                "messages": [{"role": "user", "content": "hi"}],
                "provider": "custom", "model": "glm-5.3-flash",
            })
            assert r.status_code == 200, r.status_code
        step("chat turn", _chat)
        assert "GET /v1/models" in reached["chat turn"]
        assert "POST /v1/chat/completions" in reached["chat turn"]
        # OpenCode's passthrough read of the same gateway
        cfg = tmp_path / "opencode.jsonc"
        cfg.write_text(json.dumps({"provider": {"gw": {"options": {"baseURL": gateway.base + "/v1"}}}}), "utf-8")
        monkeypatch.setattr(oc, "_config_paths", lambda: [cfg])
        monkeypatch.setattr(oc, "binary", lambda which=None: "opencode-stand-in")
        monkeypatch.setattr(oc, "_run", lambda argv, timeout=25.0: (0, "gw/glm-5.3-flash\n", ""))

        def _opencode():
            d.platform.providers.refresh_opencode()
            return d.platform.providers._opencode_allowed()
        assert step("opencode", _opencode) == ["gw/glm-5.3-flash"]
        # the doctor's custom-endpoint model check
        doctor_mod = importlib.import_module("iron_jarvis.onboarding.doctor")
        checks = step("doctor", lambda: doctor_mod.runtime_checks(d.platform))
        row = next(c for c in checks if c.get("name") == "custom_endpoint_model")
        assert row["ok"] is True
        # voice dictation through the custom endpoint
        got = step("voice", lambda: client.post(
            "/voice/transcribe", json={"audio_b64": base64.b64encode(b"RIFF....").decode(), "mime": "audio/wav"}
        ).json())
        assert got.get("text") == "hello from the stand-in"
        # a PICTURE to the gateway's vision model is served by it (not rerouted)
        router = d.platform.router
        msg = LLMMessage(
            role="user", content="what colour?", images=[{"data_b64": PNG_1PX, "media_type": "image/png"}]
        )
        res = step("vision turn", lambda: client.portal.call(functools.partial(
            router.complete, provider="fleet-custom", model="vision",
            system="", messages=[msg], tools=[],
        )))
        assert (res.provider, res.model) == ("fleet-custom", "vision"), (res.provider, res.reason)
        sent = [r for r in gateway.log() if r.get("has_image")]
        assert sent and all(r["model"] == "vision" for r in sent)

    # every caller above really asked the gateway (each step asserted it)
    assert set(reached) == {
        "sampler pass", "detect", "litellm pass", "probe form", "probe form (no key name)",
        "catalog", "fetch models", "endpoint-models", "envelope seed", "chat turn",
        "opencode", "doctor", "voice", "vision turn",
    }
    # THE DONE CONDITION
    unkeyed = gateway.unkeyed()
    assert unkeyed == [], f"{len(unkeyed)} request(s) reached the gateway without its key: {sorted(set(unkeyed))}"
    assert gateway.ollama_style() == [], f"Ollama-style requests: {sorted(set(gateway.ollama_style()))}"
    assert len(gateway.log()) >= 20
