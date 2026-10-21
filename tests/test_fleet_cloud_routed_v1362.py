"""A name a model proxy sends to the cloud is the cloud (v1.362.0).

Found on the live install (2026-10-21): the DGX Spark node is a LiteLLM proxy
on the user's tailnet. It serves ``glm-5.3-flash`` (and three variants) from a
vLLM box on that network — and ``frontier`` from OpenRouter. Jarvis judged the
whole proxy by its ADDRESS, so ``/models`` listed ``frontier`` as
``on_this_network: true``, the Local only lock let it through and the receipt
called its replies local.

This file pins the per-MODEL answer (``fleet/upstreams.py`` +
``FleetRegistry.route_place`` + ``egress.proxy_routes_to_cloud``):

* each served name is judged by its own upstream, read from the proxy's
  ``/model/info`` — a private ``api_base`` is local, no ``api_base`` is cloud
  unless LiteLLM serves that provider from the proxy's own machine;
* a name the table does not list is local only when EVERY listed upstream is;
* no table (not a proxy, never read) = the address decides, as before;
* the table is read in the background and saved, never on the routing path;
* every surface reads the one answer: the router's lock (both lanes, the
  primary and every failover candidate), the model list rows, the receipt.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from iron_jarvis.core.events import EventBus
from iron_jarvis.fleet import upstreams
from iron_jarvis.fleet.models import FleetNode, NodeSnapshot
from iron_jarvis.fleet.registry import ROUTES_FILE, FleetRegistry
from iron_jarvis.fleet.sampler import FleetSampler
from iron_jarvis.fleet.upstreams import (
    NOT_A_PROXY,
    PLACE_CLOUD,
    PLACE_LOCAL,
    UNREAD,
    RouteTable,
    parse_model_info,
    read_routes,
    upstream_place,
)
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter

PROXY = "http://100.66.161.52:4000/v1"

#: The live DGX Spark's ``/model/info``, as read on 2026-10-21 (ids shortened,
#: the extra LiteLLM params kept so the parser sees the real shape).
DGX_MODEL_INFO = {
    "data": [
        {
            "model_name": name,
            "litellm_params": {
                "model": "hosted_vllm/GLM-5.3-Flash-EXL3",
                "api_base": "http://spark-049d:8888/v1",
                "merge_reasoning_content_in_choices": True,
                "use_in_pass_through": False,
            },
            "model_info": {"id": f"id-{name}", "max_input_tokens": 1000000},
        }
        for name in ("glm-5.3-flash", "glm-5.3-flash-think", "glm-5.3-flash-fast", "vision")
    ]
    + [
        {
            "model_name": "frontier",
            "litellm_params": {"model": "openrouter/openrouter/auto-beta", "use_in_pass_through": False},
            "model_info": {"id": "id-frontier"},
        }
    ]
}

LOCAL_ONLY_INFO = {"data": [e for e in DGX_MODEL_INFO["data"] if e["model_name"] != "frontier"]}


class _Resp:
    def __init__(self, code: int, body=None, *, bad_json: bool = False):
        self.status_code = code
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


# --------------------------------------------------------------------------- #
# 1. One deployment, one verdict.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "model, api_base, place",
    [
        ("hosted_vllm/GLM", "http://spark-049d:8888/v1", PLACE_LOCAL),  # single-label LAN name
        ("hosted_vllm/brain", "http://vllm-brain:8000/v1", PLACE_LOCAL),
        ("openai/qwen", "http://192.168.1.20:8000/v1", PLACE_LOCAL),
        ("openai/qwen", "http://spark.tail1234.ts.net:8000", PLACE_LOCAL),
        ("ollama/llama3", "", PLACE_LOCAL),  # LiteLLM's default: the proxy's own machine
        ("ollama_chat/llama3", "", PLACE_LOCAL),
        ("lm_studio/x", "", PLACE_LOCAL),
        ("openrouter/openrouter/auto-beta", "", PLACE_CLOUD),
        ("anthropic/claude-x", "", PLACE_CLOUD),
        ("gpt-4o", "", PLACE_CLOUD),
        ("openai/gpt-4o", "https://api.openai.com/v1", PLACE_CLOUD),
        ("hosted_vllm/x", "https://gpu.example.com/v1", PLACE_CLOUD),
        ("", "", PLACE_CLOUD),
    ],
)
def test_each_upstream_is_judged_by_its_own_address_or_provider(model, api_base, place):
    assert upstream_place(model, api_base) == place


def test_the_live_dgx_table_says_glm_is_local_and_frontier_is_cloud():
    table = parse_model_info(DGX_MODEL_INFO)
    assert table is not None
    for name in ("glm-5.3-flash", "glm-5.3-flash-think", "glm-5.3-flash-fast", "vision"):
        assert table.place(name) == PLACE_LOCAL, name
    assert table.place("frontier") == PLACE_CLOUD
    assert table.cloud_names() == ["frontier"]
    # Nothing but the verdict is kept: the upstream ADDRESS never reaches the
    # saved table.
    assert "spark-049d" not in json.dumps(table.to_json())


def test_a_hidden_alias_is_local_only_when_every_upstream_is():
    # The daily driver's own default ("fleet") is not listed by the proxy.
    mixed = parse_model_info(DGX_MODEL_INFO)
    assert mixed.place("fleet") == PLACE_CLOUD
    assert mixed.place("") == PLACE_CLOUD
    local = parse_model_info(LOCAL_ONLY_INFO)
    assert local.place("fleet") == PLACE_LOCAL
    assert local.place("") == PLACE_LOCAL


def test_one_name_on_two_deployments_is_cloud_when_either_is():
    info = {"data": [
        {"model_name": "big", "litellm_params": {"model": "hosted_vllm/x", "api_base": "http://box:8000"}},
        {"model_name": "big", "litellm_params": {"model": "openrouter/y"}},
    ]}
    assert parse_model_info(info).place("big") == PLACE_CLOUD


def test_a_wildcard_route_decides_the_names_it_matches():
    info = {"data": [
        {"model_name": "glm", "litellm_params": {"model": "hosted_vllm/x", "api_base": "http://box:8000"}},
        {"model_name": "openrouter/*", "litellm_params": {"model": "openrouter/*"}},
    ]}
    table = parse_model_info(info)
    assert table.place("openrouter/anthropic/claude") == PLACE_CLOUD
    assert table.place("glm") == PLACE_LOCAL
    # A LOCAL wildcard decides its names too, even on a proxy that also has a
    # cloud name (without the wildcard rule they would read as unlisted).
    mixed = parse_model_info({"data": [
        {"model_name": "hosted_vllm/*", "litellm_params": {"model": "hosted_vllm/*", "api_base": "http://box:8000"}},
        {"model_name": "frontier", "litellm_params": {"model": "openrouter/auto"}},
    ]})
    assert mixed.place("hosted_vllm/qwen3") == PLACE_LOCAL
    assert mixed.place("something-else") == PLACE_CLOUD


def test_a_body_that_is_not_litellms_is_no_table():
    assert parse_model_info({"data": [{"id": "llama3"}]}) is None  # /v1/models shape
    assert parse_model_info({"object": "list"}) is None
    assert parse_model_info([]) is None
    assert parse_model_info(None) is None
    # An empty table says nothing (the address decides).
    assert RouteTable(entries=()).place("anything") is None


def test_reading_the_table_tells_a_proxy_from_a_server_and_a_blip():
    node = FleetNode(id="dgx", base_url=PROXY)
    seen = []

    def get(url, headers=None):
        seen.append(url)
        return _Resp(200, DGX_MODEL_INFO)

    table = read_routes(node, get=get)
    assert isinstance(table, RouteTable) and table.place("frontier") == PLACE_CLOUD
    # The proxy's ROOT, above /v1 — where LiteLLM serves /model/info.
    assert seen == ["http://100.66.161.52:4000/model/info"]

    assert read_routes(node, get=lambda u, headers=None: _Resp(404)) == NOT_A_PROXY
    assert read_routes(node, get=lambda u, headers=None: _Resp(200, {"data": [{"id": "x"}]})) == NOT_A_PROXY
    assert read_routes(node, get=lambda u, headers=None: _Resp(200, bad_json=True)) == NOT_A_PROXY
    # A keyed proxy (401), a server error, no answer: we could not read it.
    assert read_routes(node, get=lambda u, headers=None: _Resp(401)) == UNREAD
    assert read_routes(node, get=lambda u, headers=None: _Resp(503)) == UNREAD

    def boom(url, headers=None):
        raise OSError("connection refused")

    assert read_routes(node, get=boom) == UNREAD
    assert read_routes(FleetNode(id="x", base_url=""), get=get) == NOT_A_PROXY


# --------------------------------------------------------------------------- #
# 2. The registry holds it, saves it, and answers per provider + model.
# --------------------------------------------------------------------------- #
def _config(tmp_path, **kw):
    base = dict(
        home=tmp_path, fleet_nodes=[], ollama_base_url="", ollama_model="",
        custom_base_url=PROXY, custom_model="fleet",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _registry(tmp_path, **kw):
    return FleetRegistry(_config(tmp_path, **kw), persist=lambda *a, **k: None)


def test_the_registry_answers_for_the_custom_slot_and_its_fleet_twin(tmp_path):
    reg = _registry(tmp_path)
    assert reg.route_place("custom", "frontier") is None  # nothing read yet
    rev = reg.revision
    reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    assert reg.revision == rev + 1  # the model list is rebuilt with the new places
    for provider in ("custom", "fleet-custom"):
        assert reg.route_place(provider, "frontier") == PLACE_CLOUD
        assert reg.route_place(provider, "glm-5.3-flash") == PLACE_LOCAL
    # Not a fleet provider at all: no answer (its own rule decides).
    assert reg.route_place("claude-cli", "frontier") is None
    # The same table again moves nothing (the sampler sets it every minute).
    rev = reg.revision
    reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    assert reg.revision == rev


def test_a_blip_keeps_the_table_and_a_non_proxy_drops_it(tmp_path):
    reg = _registry(tmp_path)
    reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    reg.set_routes("custom", UNREAD)
    assert reg.route_place("custom", "frontier") == PLACE_CLOUD
    reg.set_routes("custom", NOT_A_PROXY)
    assert reg.route_place("custom", "frontier") is None


def test_a_table_read_at_another_address_does_not_describe_the_node(tmp_path):
    cfg = _config(tmp_path)
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    cfg.custom_base_url = "http://192.168.1.77:4000/v1"  # the user re-pointed the slot
    assert reg.route_place("custom", "frontier") is None


def test_the_last_table_survives_a_restart(tmp_path):
    reg = _registry(tmp_path)
    reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    saved = json.loads((tmp_path / ROUTES_FILE).read_text(encoding="utf-8"))
    assert set(saved) == {"custom"}
    assert "spark-049d" not in json.dumps(saved)  # verdicts only, never upstream addresses
    again = _registry(tmp_path)
    assert again.route_place("custom", "frontier") == PLACE_CLOUD
    assert again.route_place("custom", "glm-5.3-flash") == PLACE_LOCAL


def test_a_damaged_saved_file_is_no_tables_never_a_crash(tmp_path):
    (tmp_path / ROUTES_FILE).write_text("{not json", encoding="utf-8")
    reg = _registry(tmp_path)
    assert reg.route_place("custom", "frontier") is None


def test_removing_the_node_drops_its_table(tmp_path):
    cfg = _config(tmp_path, custom_base_url="")
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    reg.add(FleetNode(id="dgx", label="DGX Spark", base_url=PROXY, routable=True))
    reg.set_routes("dgx", parse_model_info(DGX_MODEL_INFO))
    assert reg.route_place("fleet-dgx", "frontier") == PLACE_CLOUD
    reg.remove("dgx")
    assert reg.route_place("fleet-dgx", "frontier") is None
    assert "dgx" not in json.loads((tmp_path / ROUTES_FILE).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 3. The predicate: per model, fail closed, unchanged without a table.
# --------------------------------------------------------------------------- #
def _manager(tmp_path, *, table=True, custom_model="fleet"):
    reg = _registry(tmp_path, custom_model=custom_model)
    if table:
        reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    mgr = ProviderManager(
        custom_base_url=PROXY, custom_model=custom_model,
        upstream_place=lambda name, model: reg.route_place(name, model),
    )
    return mgr, reg


def test_frontier_is_cloud_and_glm_stays_on_this_network(tmp_path):
    mgr, _ = _manager(tmp_path)
    assert egress.stays_on_this_network("custom", mgr, None, "glm-5.3-flash") is True
    assert egress.stays_on_this_network("custom", mgr, None, "frontier") is False
    assert egress.model_place("custom", mgr, None, "frontier") == egress.PLACE_CLOUD
    assert egress.model_place("custom", mgr, None, "glm-5.3-flash") == egress.PLACE_NETWORK
    # The provider's DEFAULT model ("" = what the adapter sends): the daily
    # driver's hidden "fleet" alias, on a proxy that also uses the cloud.
    assert egress.default_model_of("custom", mgr) == "fleet"
    assert egress.stays_on_this_network("custom", mgr) is False
    assert egress.route_flags("custom", mgr, "frontier")["on_this_network"] is False
    assert egress.route_flags("custom", mgr, "glm-5.3-flash")["on_this_network"] is True


def test_the_default_model_is_judged_when_no_model_is_named(tmp_path):
    # A proxy whose saved default is one of its LOCAL names stays local for a
    # call that names no model, even though the proxy also has a cloud name.
    mgr, _ = _manager(tmp_path, custom_model="glm-5.3-flash")
    assert egress.default_model_of("custom", mgr) == "glm-5.3-flash"
    assert egress.stays_on_this_network("custom", mgr) is True
    mgr, _ = _manager(tmp_path, custom_model="frontier")
    assert egress.stays_on_this_network("custom", mgr) is False


def test_without_a_table_the_address_decides_exactly_as_before(tmp_path):
    mgr, _ = _manager(tmp_path, table=False)
    for model in ("frontier", "glm-5.3-flash", ""):
        assert egress.stays_on_this_network("custom", mgr, None, model) is True
    # A manager with no oracle at all (every older caller): the same.
    bare = ProviderManager(custom_base_url=PROXY, custom_model="fleet")
    assert egress.stays_on_this_network("custom", bare, None, "frontier") is True


def test_a_public_address_is_cloud_whatever_the_table_says(tmp_path):
    reg = _registry(tmp_path, custom_base_url="https://llm.example.com/v1")
    reg.set_routes("custom", parse_model_info(LOCAL_ONLY_INFO))
    mgr = ProviderManager(
        custom_base_url="https://llm.example.com/v1", custom_model="glm-5.3-flash",
        upstream_place=lambda n, m: reg.route_place(n, m),
    )
    assert egress.stays_on_this_network("custom", mgr, None, "glm-5.3-flash") is False


def test_an_oracle_that_raises_reads_as_cloud(tmp_path):
    def boom(name, model):
        raise RuntimeError("registry fault")

    mgr = ProviderManager(custom_base_url=PROXY, custom_model="fleet", upstream_place=boom)
    assert mgr.upstream_place("custom", "glm-5.3-flash") == PLACE_CLOUD
    assert egress.stays_on_this_network("custom", mgr, None, "glm-5.3-flash") is False


def test_the_refusal_names_the_name_that_leaves_not_the_local_box(tmp_path):
    mgr, _ = _manager(tmp_path)
    exc = egress.LocalOnlyRefused("custom", "frontier", manager=mgr)
    assert str(exc) == (
        "This chat is local only, so nothing was sent. Your own model server passes"
        " frontier on to a cloud service. Pick a local model, or allow it for"
        " this one message."
    )
    assert exc.payload()["model"] == "frontier" and exc.payload()["provider"] == "custom"
    # A real cloud provider keeps its v1.332.0 sentence word for word.
    assert str(egress.LocalOnlyRefused("claude-cli", "opus", manager=mgr)).startswith(
        "This chat is local only, so nothing was sent to Claude."
    )


# --------------------------------------------------------------------------- #
# 4. The lock: the router refuses the cloud-routed name, both lanes.
# --------------------------------------------------------------------------- #
class _Ad(LLMAdapter):
    def __init__(self, provider, model, endpoint):
        self.provider, self.model, self._endpoint = provider, model, endpoint
        self.calls = 0

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text=f"answer from {self.model}", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "text", "text": "tok"}
        yield {"type": "final", "response": LLMResponse(text="tok", tool_calls=[], usage={})}


class _Mgr:
    """A manager whose ``custom`` adapter is built per model, like the real
    one, with the registry's oracle wired."""

    def __init__(self, reg):
        self.reg = reg
        self.built: dict[str, _Ad] = {}

    def available(self, p):
        return p == "custom"

    def has_available_api_provider(self):
        return False

    def runtime_provider_names(self):
        return []

    def get(self, p, m=None):
        model = m or "fleet"
        return self.built.setdefault(model, _Ad(p, model, PROXY))

    def upstream_place(self, name, model):
        return self.reg.route_place(name, model)


def _router(tmp_path):
    reg = _registry(tmp_path)
    reg.set_routes("custom", parse_model_info(DGX_MODEL_INFO))
    mgr = _Mgr(reg)
    r = ModelRouter(mgr, default_provider="custom", event_bus=EventBus(), local_policy=lambda: "refuse")
    return r, mgr


async def _complete(router, **kw):
    return await router.complete(system="s", messages=[LLMMessage(role="user", content="hi")], tools=[], **kw)


async def _stream(router, **kw):
    return [f async for f in router.stream(
        system="s", messages=[LLMMessage(role="user", content="hi")], tools=[], **kw
    )]


@pytest.mark.parametrize("lane", [_complete, _stream])
def test_the_router_refuses_frontier_and_sends_glm_under_local_only(tmp_path, lane):
    r, mgr = _router(tmp_path)

    async def go(model):
        with egress.local_only_scope(True):
            return await lane(r, provider="custom", model=model)

    with pytest.raises(egress.LocalOnlyRefused) as caught:
        asyncio.run(go("frontier"))
    assert caught.value.model == "frontier"
    assert mgr.built["frontier"].calls == 0  # nothing was sent
    asyncio.run(go("glm-5.3-flash"))
    assert mgr.built["glm-5.3-flash"].calls == 1
    # Anti-vacuity: outside Local only the same name goes (it is the user's
    # own proxy and their pick).
    asyncio.run(lane(r, provider="custom", model="frontier"))
    assert mgr.built["frontier"].calls == 1


def test_the_allow_once_still_sends_the_cloud_routed_name(tmp_path):
    r, mgr = _router(tmp_path)

    async def go():
        with egress.local_only_scope(True, allowed_once="custom"):
            return await _complete(r, provider="custom", model="frontier")

    asyncio.run(go())
    assert mgr.built["frontier"].calls == 1


def test_a_failover_candidate_is_judged_by_its_own_model(tmp_path):
    r, _ = _router(tmp_path)
    with egress.local_only_scope(True):
        assert r._egress_ok("custom", "frontier") is False
        assert r._egress_ok("custom", "glm-5.3-flash") is True
        # No model named: the candidate's default (the hidden "fleet" alias on
        # a proxy that also uses the cloud) is not trusted.
        assert r._egress_ok("custom") is False
    # Outside Local only every candidate is allowed, as before.
    assert r._egress_ok("custom", "frontier") is True


# --------------------------------------------------------------------------- #
# 5. The model list says so, row by row, through the real route.
# --------------------------------------------------------------------------- #
def test_the_model_list_marks_only_the_cloud_routed_row(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes import connections as C
    from iron_jarvis.providers import discovery

    names = [e["model_name"] for e in DGX_MODEL_INFO["data"]]
    monkeypatch.setattr(discovery, "discover_models", lambda *a, **k: list(names))
    client = TestClient(create_app(str(tmp_path)))
    d = client.app.state.d
    d.fleet.add(FleetNode(id="dgx", label="DGX Spark", base_url=PROXY, routable=True,
                          default_model="glm-5.3-flash"))
    d.fleet.register_providers(d.platform.providers)
    C.invalidate_model_catalog()
    before = {r["model"]: r for r in client.get("/models").json()["models"] if r["provider"] == "fleet-dgx"}
    assert before["frontier"]["on_this_network"] is True  # no table yet: the address decides

    d.fleet.set_routes("dgx", parse_model_info(DGX_MODEL_INFO))
    rows = {r["model"]: r for r in client.get("/models").json()["models"] if r["provider"] == "fleet-dgx"}
    assert rows["frontier"]["on_this_network"] is False
    for name in ("glm-5.3-flash", "glm-5.3-flash-think", "glm-5.3-flash-fast", "vision"):
        assert rows[name]["on_this_network"] is True, name
    # The routing CLASS is unchanged, and reasoning_default stays LAST.
    assert rows["frontier"]["kind"] == "local"
    assert all(list(r)[-1] == "reasoning_default" for r in rows.values())


# --------------------------------------------------------------------------- #
# 6. The sampler reads tables in the background, bounded and injected.
# --------------------------------------------------------------------------- #
class _Reg:
    def __init__(self, nodes):
        self._nodes = nodes
        self.routes: list[tuple[str, object]] = []

    def nodes(self):
        return self._nodes

    def set_reachable(self, node_id, ok):
        pass

    def absorb_children(self, parent_id, children):
        pass

    def set_routes(self, node_id, result):
        self.routes.append((node_id, result))


def _probe(status="online"):
    def probe(node):
        return NodeSnapshot(node=node, status=status, evidence="direct"), []

    return probe


def test_the_sampler_reads_each_proxys_table_once_per_minute(tmp_path):
    now = [1000.0]
    proxy = FleetNode(id="dgx", base_url=PROXY)
    child = FleetNode(id="dgx-frontier", base_url="http://spark-049d:8888/v1", parent_id="dgx", routable=False)
    reg = _Reg([proxy, child])
    reads = []

    def reader(node):
        reads.append(node.id)
        return parse_model_info(DGX_MODEL_INFO)

    s = FleetSampler(reg, probe=_probe(), clock=lambda: now[0], route_reader=reader)
    s.sample_once(force=True)
    assert reads == ["dgx"]  # a proxy's own backends are never asked
    assert reg.routes and reg.routes[-1][0] == "dgx"
    s.sample_once(force=True)
    assert reads == ["dgx"]  # fresh for a minute
    now[0] += upstreams.ROUTES_TTL_S + 1
    s.sample_once(force=True)
    assert reads == ["dgx", "dgx"]


def test_a_server_that_is_not_a_proxy_is_asked_again_only_after_ten_minutes():
    now = [0.0]
    reg = _Reg([FleetNode(id="box", base_url="http://192.168.1.9:11434")])
    reads = []

    def reader(node):
        reads.append(node.id)
        return NOT_A_PROXY

    s = FleetSampler(reg, probe=_probe(), clock=lambda: now[0], route_reader=reader)
    s.sample_once(force=True)
    now[0] += upstreams.ROUTES_TTL_S + 1
    s.sample_once(force=True)
    assert reads == ["box"]
    now[0] += upstreams.NOT_A_PROXY_TTL_S
    s.sample_once(force=True)
    assert reads == ["box", "box"]


def test_an_offline_node_is_not_asked_for_its_table():
    reg = _Reg([FleetNode(id="dgx", base_url=PROXY)])
    reads = []
    s = FleetSampler(reg, probe=_probe("offline"), route_reader=lambda n: reads.append(n) or UNREAD)
    s.sample_once(force=True)
    assert reads == []


def test_a_sampler_built_without_a_reader_never_reads_a_table():
    reg = _Reg([FleetNode(id="dgx", base_url=PROXY)])
    s = FleetSampler(reg, probe=_probe())
    s.sample_once(force=True)
    assert reg.routes == []


def test_the_async_loop_reads_tables_off_the_loop_too():
    reg = _Reg([FleetNode(id="dgx", base_url=PROXY)])
    import threading

    threads = []

    def reader(node):
        threads.append(threading.current_thread() is threading.main_thread())
        return parse_model_info(DGX_MODEL_INFO)

    s = FleetSampler(reg, probe=_probe(), route_reader=reader)
    asyncio.run(s._sample_all_async())
    assert threads == [False]
    assert reg.routes and isinstance(reg.routes[-1][1], RouteTable)


def test_the_daemon_wires_the_real_reader_into_its_sampler():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(
        encoding="utf-8"
    )
    assert "route_reader=_route_reader_for(platform)" in src
    platform_src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "platform.py").read_text(
        encoding="utf-8"
    )
    assert "upstream_place=lambda name, model: fleet_registry.route_place(name, model)" in platform_src


# --------------------------------------------------------------------------- #
# 7. Every surface reads the one answer: both chat lanes, the 409, sessions.
# --------------------------------------------------------------------------- #
#: A private address nothing answers on: no lifespan runs here, so the sampler
#: never probes it; the table is set by hand.
FAKE_PROXY = "http://10.9.8.7:4000/v1"


class _Box(LLMAdapter):
    """One served name on the fleet node; records the system prompt."""

    def __init__(self, model: str) -> None:
        self.provider = "fleet-dgx"
        self.model = model
        self.node = SimpleNamespace(label="DGX Spark", base_url=FAKE_PROXY)
        self.systems: list[str] = []

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": False}

    async def complete(self, *, system, messages, tools, **_kw):
        self.systems.append(system)
        return LLMResponse(text="hello")


def _wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    d, p = app.state.d, app.state.platform
    d.fleet.add(FleetNode(id="dgx", label="DGX Spark", base_url=FAKE_PROXY, routable=True,
                          default_model="fleet"))
    d.fleet.set_routes("dgx", parse_model_info(DGX_MODEL_INFO))
    boxes: dict[str, _Box] = {}
    real_get, real_avail = p.providers.get, p.providers.available

    def get(prov, m=None):
        if prov == "fleet-dgx":
            return boxes.setdefault(m or "fleet", _Box(m or "fleet"))
        return real_get(prov, m)

    monkeypatch.setattr(p.providers, "get", get)
    monkeypatch.setattr(p.providers, "available", lambda n: True if n == "fleet-dgx" else real_avail(n))
    return TestClient(app), boxes


def _sse(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.split("\n\n"):
        ev, data = "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if ev:
            out.append((ev, json.loads(data) if data else {}))
    return out


def _turn(client, lane, body):
    if lane == "post":
        return client.post("/chat", json=body).json()
    r = client.post("/chat/stream", json=body)
    done = [d for ev, d in _sse(r.text) if ev == "done"]
    assert done, r.text[-1500:]
    return done[-1]


_ASK = {"messages": [{"role": "user", "content": "are you running locally?"}], "provider": "fleet-dgx"}


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_both_lanes_say_where_each_name_really_runs(tmp_path, monkeypatch, lane):
    client, boxes = _wired(tmp_path, monkeypatch)
    out = _turn(client, lane, {**_ASK, "model": "frontier"})
    assert out["route"]["model"] == "frontier"
    assert out["route"]["on_this_network"] is False
    assert list(out["route"])[-1] == "on_this_network"
    assert "The model answering (you) runs in the cloud" in boxes["frontier"].systems[0]

    out = _turn(client, lane, {**_ASK, "model": "glm-5.3-flash"})
    assert out["route"]["on_this_network"] is True
    assert "runs on a computer on the user's own network" in boxes["glm-5.3-flash"].systems[0]


def test_a_local_only_chat_refuses_frontier_with_the_409_and_sends_glm(tmp_path, monkeypatch):
    client, boxes = _wired(tmp_path, monkeypatch)
    r = client.post("/chat", json={**_ASK, "model": "frontier", "local_only": True})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "local_only" and body["provider"] == "fleet-dgx" and body["model"] == "frontier"
    assert "DGX Spark passes frontier on to a cloud service" in body["detail"]
    assert "frontier" not in boxes or not boxes["frontier"].systems  # nothing was sent

    r = client.post("/chat/stream", json={**_ASK, "model": "frontier", "local_only": True})
    errors = [d for ev, d in _sse(r.text) if ev == "error"]
    assert errors and errors[-1]["code"] == "local_only", r.text[-800:]
    assert "frontier" not in boxes or not boxes["frontier"].systems

    r = client.post("/chat", json={**_ASK, "model": "glm-5.3-flash", "local_only": True})
    assert r.status_code == 200, r.text
    assert boxes["glm-5.3-flash"].systems


def test_a_local_only_session_on_frontier_is_refused_before_it_starts(tmp_path, monkeypatch):
    client, _ = _wired(tmp_path, monkeypatch)
    base = {"task": "do it", "provider": "fleet-dgx", "local_only": True, "wait": False}
    r = client.post("/sessions", json={**base, "model": "frontier"})
    assert r.status_code == 409, r.text
    assert r.json()["model"] == "frontier"
    r = client.post("/sessions", json={**base, "model": "glm-5.3-flash"})
    assert r.status_code in (200, 201), r.text


def test_the_where_line_ladder_names_the_model_the_turn_will_send():
    from iron_jarvis.daemon.chat_turn import where_model

    cfg = SimpleNamespace(default_model="fleet")
    assert where_model(SimpleNamespace(provider="fleet-dgx", model="frontier"), None, cfg) == "frontier"
    proj = SimpleNamespace(default_provider="fleet-dgx", default_model="glm-5.3-flash")
    assert where_model(SimpleNamespace(provider="", model=""), proj, cfg) == "glm-5.3-flash"
    assert where_model(SimpleNamespace(provider="", model=""), SimpleNamespace(default_provider=""), cfg) == "fleet"
    assert where_model(SimpleNamespace(provider="", model=""), None, cfg) == "fleet"



def test_a_keyed_proxy_is_read_with_the_nodes_own_key():
    """v1.365.2: the user's proxy started requiring a master key; without it
    /model/info answers 401, the table is never read and a cloud-routed name
    is judged by the address again (local)."""
    seen = []

    def get(url, headers=None):
        seen.append(dict(headers or {}))
        if (headers or {}).get("Authorization") != "Bearer sk-test-key":
            return _Resp(401)
        return _Resp(200, DGX_MODEL_INFO)

    node = FleetNode(id="custom", base_url=PROXY, api_key_name="custom_api_key")
    assert read_routes(node, get=get) == UNREAD  # no key: unread, as before
    table = read_routes(node, get=get, key="sk-test-key")
    assert isinstance(table, RouteTable) and table.place("frontier") == PLACE_CLOUD
    assert seen[0] == {} and seen[1] == {"Authorization": "Bearer sk-test-key"}


def test_the_daemons_reader_takes_the_key_from_the_vault(monkeypatch):
    from iron_jarvis.daemon import app as appmod
    from iron_jarvis.fleet import upstreams as up

    calls = []
    monkeypatch.setattr(up, "read_routes", lambda node, key="": calls.append((node.id, key)) or UNREAD)
    vault = {"custom_api_key": "sk-from-vault"}
    platform = SimpleNamespace(secrets=SimpleNamespace(get=lambda n: vault.get(n)))
    reader = appmod._route_reader_for(platform)
    reader(FleetNode(id="custom", base_url=PROXY, api_key_name="custom_api_key"))
    reader(FleetNode(id="box", base_url="http://192.168.1.9:8000"))
    broken = appmod._route_reader_for(SimpleNamespace(secrets=SimpleNamespace(get=lambda n: 1 / 0)))
    broken(FleetNode(id="custom", base_url=PROXY, api_key_name="custom_api_key"))
    assert calls == [("custom", "sk-from-vault"), ("box", ""), ("custom", "")]
