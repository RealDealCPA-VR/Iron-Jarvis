"""v1.421.0 (Wave 1, D3) — A LOCAL MODEL'S CONTEXT WINDOW IS READ FROM THE
FLEET PROBE.

The context-window ladder (``daemon/chat_turn._ladder_window_source``) had an
"endpoint" rung that walked ``fleet.nodes()`` reading ``node.models`` /
``m.name``. A FleetNode is config and has no models — what a node serves is on
the sampler's NodeSnapshot (``ModelEntry.id``). So the rung never answered:
a local model planned at the 32k default unless a LiteLLM gateway stated
``max_input_tokens``, and an Ollama model loaded with a smaller window was
sent prompts it silently cut from the front. The tests that pinned the rung
handed it fakes in the dead shape (moved on purpose to the real objects:
``tests/_fakes/fleet_window``).

Now the rung reads the sampler's LAST snapshot of the node serving the
provider (``fleet/windows.probed_window``): vLLM / OpenAI-compatible
``max_model_len``, else Ollama's loaded ``context_length``; a LiteLLM proxy's
alias followed to the deployment that serves it. Every rung above keeps its
place; no snapshot, no match or no number stays unknown; a cached read only.

Driven with the REAL probes (``fleet/probes.probe_node`` answering realistic
server JSON through an injected getter), the REAL registry and the REAL
sampler, through the REAL ladder function.
"""

from __future__ import annotations

import functools
from types import SimpleNamespace

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_turn import _context_window, _context_window_source
from iron_jarvis.fleet import windows as W
from iron_jarvis.fleet.models import FleetNode, ModelEntry, NodeSnapshot
from iron_jarvis.fleet.probes import probe_node
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.fleet.sampler import FleetSampler
from iron_jarvis.fleet.upstreams import parse_model_info

OLLAMA = "http://ollama-box:11434"
SPARK_A = "http://spark-a:8000/v1"
SPARK_B = "http://spark-b:8000/v1"
GATEWAY = "http://gw:4000"

#: What each server answers (the shapes ``fleet/probes`` parses).
SERVERS = {
    # Ollama: /api/ps lists what is LOADED, with the runner's real allocation.
    f"{OLLAMA}/api/ps": {"models": [
        {"name": "qwen3:30b", "model": "qwen3:30b", "size": 20_000_000_000, "size_vram": 20_000_000_000,
         "context_length": 8192, "details": {"parameter_size": "30B", "quantization_level": "Q4_K_M"}},
        {"name": "llama3:latest", "model": "llama3:latest", "context_length": 16_384, "details": {}},
    ]},
    # /api/tags lists what is INSTALLED (no window for a model not loaded).
    f"{OLLAMA}/api/tags": {"models": [
        {"name": "qwen3:30b"}, {"name": "llama3:latest"}, {"name": "gemma4:26b", "details": {}},
    ]},
    # A vLLM box (also the custom slot): /v1/models with max_model_len + root.
    "http://spark-a:8000/v1/models": {"data": [
        {"id": "glm-5.3-flash", "root": "zai-org/GLM-5.3-Flash", "max_model_len": 131_072},
    ]},
    "http://spark-b:8000/v1/models": {"data": [
        {"id": "zai-org/GLM-5.3-FP8", "root": "zai-org/GLM-5.3-FP8", "max_model_len": 65_536},
    ]},
    # The LiteLLM proxy: aliases routed to the two boxes (and one it cannot
    # reach from here, and one alias on BOTH boxes).
    f"{GATEWAY}/model/info": {"data": [
        {"model_name": "glm-5.3-flash",
         "litellm_params": {"model": "hosted_vllm/glm-5.3-flash", "api_base": SPARK_A}},
        {"model_name": "worker",
         "litellm_params": {"model": "openai/zai-org/GLM-5.3-FP8", "api_base": SPARK_B}},
        {"model_name": "pair", "litellm_params": {"model": "hosted_vllm/glm-5.3-flash", "api_base": SPARK_A}},
        {"model_name": "pair", "litellm_params": {"model": "openai/zai-org/GLM-5.3-FP8", "api_base": SPARK_B}},
        {"model_name": "far",
         "litellm_params": {"model": "hosted_vllm/far", "api_base": "http://127.0.0.1:9000/v1"}},
    ]},
}


class Net:
    """The fake network the probes go through: counts every request."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def get(self, url: str, headers=None):
        self.requests.append(url)
        if url in SERVERS:
            body = SERVERS[url]
            return SimpleNamespace(status_code=200, json=lambda: body, text="")
        if url.startswith("http://127.0.0.1:9000"):
            raise ConnectionError("connection refused")
        return SimpleNamespace(status_code=404, json=lambda: {}, text="")


def _fleet(*, gateway=True, sample=True):
    cfg = SimpleNamespace(
        home=None,
        fleet_nodes=[
            FleetNode(id="dgx", label="DGX", base_url=GATEWAY, kind="litellm", routable=True).model_dump()
        ] if gateway else [],
        ollama_base_url=OLLAMA, ollama_model="", custom_base_url=SPARK_A, custom_model="glm-5.3-flash",
    )
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    net = Net()
    sampler = FleetSampler(reg, probe=functools.partial(probe_node, get=net.get))
    if sample:
        sampler.sample_once(force=True)  # the top-level nodes (and the proxy's children)
        sampler.sample_once(force=True)  # ...then the children it absorbed
    return reg, sampler, net, cfg


def _d(reg, sampler, **pins):
    return SimpleNamespace(platform=SimpleNamespace(
        config=SimpleNamespace(default_provider="", default_model="", model_context_windows=dict(pins)),
        providers=None, fleet=reg, fleet_sampler=sampler,
    ))


# --------------------------------------------------------------------------- #
# the rung answers from the model's own server
# --------------------------------------------------------------------------- #


def test_an_ollama_model_plans_at_the_window_its_loaded_runner_has():
    reg, sampler, _, _ = _fleet()
    d = _d(reg, sampler)
    assert _context_window_source(d, "ollama", "qwen3:30b") == (8192, "endpoint")
    # the slot's seed twin is the same node
    assert _context_window_source(d, "fleet-ollama", "qwen3:30b") == (8192, "endpoint")
    # Ollama's own rule: "llama3" IS "llama3:latest"
    assert _context_window_source(d, "ollama", "llama3") == (16_384, "endpoint")
    # installed but not loaded: no runner, no window — unknown, never a guess
    assert _context_window_source(d, "ollama", "gemma4:26b") == (None, "default")
    assert _context_window_source(d, "ollama", "not-installed") == (None, "default")


def test_a_vllm_model_plans_at_its_servers_max_model_len():
    reg, sampler, _, _ = _fleet()
    d = _d(reg, sampler)
    assert _context_window_source(d, "custom", "glm-5.3-flash") == (131_072, "endpoint")
    assert _context_window_source(d, "fleet-custom", "glm-5.3-flash") == (131_072, "endpoint")
    # vLLM's ``root`` (ModelEntry.hf_id) names the same model
    assert _context_window_source(d, "custom", "zai-org/GLM-5.3-Flash") == (131_072, "endpoint")


def test_a_proxys_alias_is_followed_to_the_server_that_serves_it():
    reg, sampler, _, _ = _fleet()
    d = _d(reg, sampler)
    # hosted_vllm/glm-5.3-flash at spark-a -> vLLM's id "glm-5.3-flash"
    assert _context_window_source(d, "fleet-dgx", "glm-5.3-flash") == (131_072, "endpoint")
    # openai/zai-org/GLM-5.3-FP8 -> LiteLLM sends "zai-org/GLM-5.3-FP8" (the
    # provider prefix off, never only the last segment)
    assert _context_window_source(d, "fleet-dgx", "worker") == (65_536, "endpoint")
    # a deployment this PC cannot reach: unknown
    assert _context_window_source(d, "fleet-dgx", "far") == (None, "default")
    # an alias on TWO servers: a request may land on either — never the larger
    assert _context_window_source(d, "fleet-dgx", "pair") == (None, "default")


def test_several_deployments_answer_the_smallest_and_only_when_all_are_known():
    """The rule ``probed_window`` applies when a proxy's children are
    distinct nodes (real objects, real snapshots)."""
    reg, sampler, _, _ = _fleet(sample=False)
    sampler.record(reg.get("dgx"), NodeSnapshot(
        node=reg.get("dgx"), status="online", models=[ModelEntry(id="pair", hf_id="x")],
    ))
    kids = [
        FleetNode(id="dgx-a", base_url=SPARK_A, source="topology", parent_id="dgx", alias="pair",
                  default_model="hosted_vllm/glm-5.3-flash"),
        FleetNode(id="dgx-b", base_url=SPARK_B, source="topology", parent_id="dgx", alias="pair",
                  default_model="openai/zai-org/GLM-5.3-FP8"),
    ]
    reg.absorb_children("dgx", kids)
    sampler.record(kids[0], NodeSnapshot(node=kids[0], status="online",
                                         models=[ModelEntry(id="glm-5.3-flash", max_model_len=131_072)]))
    assert W.probed_window(reg, sampler, "fleet-dgx", "pair") is None  # one not read yet
    # read, but its server states no number for the model: still unknown
    sampler.record(kids[1], NodeSnapshot(node=kids[1], status="online",
                                         models=[ModelEntry(id="zai-org/GLM-5.3-FP8")]))
    assert W.probed_window(reg, sampler, "fleet-dgx", "pair") is None
    sampler.record(kids[1], NodeSnapshot(node=kids[1], status="online",
                                         models=[ModelEntry(id="zai-org/GLM-5.3-FP8", max_model_len=65_536)]))
    assert W.probed_window(reg, sampler, "fleet-dgx", "pair") == 65_536


def test_max_model_len_is_the_number_before_context_length():
    reg, sampler, _, _ = _fleet(sample=False, gateway=False)
    node = reg.get("custom")
    sampler.record(node, NodeSnapshot(node=node, status="online", models=[
        ModelEntry(id="m", max_model_len=40_000, context_length=4096),
    ]))
    assert W.probed_window(reg, sampler, "custom", "m") == 40_000


# --------------------------------------------------------------------------- #
# the ladder keeps its order; unknown stays unknown
# --------------------------------------------------------------------------- #


def test_every_rung_above_keeps_its_place():
    reg, sampler, _, _ = _fleet()
    # a pin wins
    assert _context_window_source(_d(reg, sampler, **{"ollama::qwen3:30b": 20_000}), "ollama", "qwen3:30b") == (
        20_000, "pin")
    # a window the proxy STATES (its /model/info max_input_tokens) wins over
    # the probe — the gateway rung sits above this one
    reg.set_routes("dgx", parse_model_info({"data": [
        {"model_name": "glm-5.3-flash",
         "litellm_params": {"model": "hosted_vllm/glm-5.3-flash", "api_base": SPARK_A},
         "model_info": {"max_input_tokens": 200_000}},
        {"model_name": "worker",
         "litellm_params": {"model": "openai/zai-org/GLM-5.3-FP8", "api_base": SPARK_B}},
    ]}))
    d = _d(reg, sampler)
    assert _context_window_source(d, "fleet-dgx", "glm-5.3-flash") == (200_000, "endpoint")
    assert _context_window_source(d, "fleet-dgx", "worker") == (65_536, "endpoint")  # not stated: the probe


def test_a_window_the_server_said_in_a_refusal_still_wins_when_smaller(monkeypatch):
    reg, sampler, _, _ = _fleet()
    monkeypatch.setitem(chat_turn._LEARNED_WINDOWS, "ollama::qwen3:30b", 6000)
    assert _context_window_source(_d(reg, sampler), "ollama", "qwen3:30b") == (6000, "learned")


def test_unknown_stays_unknown():
    reg, sampler, _, cfg = _fleet()
    # no sampler at all (a bare platform, a CLI one-shot)
    assert _context_window_source(_d(reg, None), "ollama", "qwen3:30b") == (None, "default")
    # a sampler that has not probed yet
    fresh = FleetSampler(reg, probe=lambda node, **kw: 1 / 0)
    assert _context_window_source(_d(reg, fresh), "ollama", "qwen3:30b") == (None, "default")
    # a provider that is no fleet node
    assert _context_window_source(_d(reg, sampler), "anthropic", "qwen3:30b") == (None, "default")
    # a proxy's CHILD is never a provider
    assert W.probed_window(reg, sampler, "fleet-dgx-glm-5-3-flash", "glm-5.3-flash") is None
    # the slot re-pointed at another box: the old reading describes the old one
    cfg.ollama_base_url = "http://another-box:11434"
    assert _context_window_source(_d(reg, sampler), "ollama", "qwen3:30b") == (None, "default")


def test_a_switched_off_node_answers_nothing():
    reg, sampler, _, cfg = _fleet()
    cfg.fleet_nodes = [{**cfg.fleet_nodes[0], "enabled": False}]
    assert _context_window_source(_d(reg, sampler), "fleet-dgx", "glm-5.3-flash") == (None, "default")


def test_a_node_that_went_offline_keeps_what_it_last_said_and_an_unread_one_says_nothing():
    """The sampler keeps a failed node's MODELS (what is still true) and drops
    its numbers; a node whose first probe failed has no models at all."""
    reg, sampler, _, _ = _fleet()
    node = reg.get("ollama")
    sampler._record_failure(node, "timed out")
    assert W.probed_window(reg, sampler, "ollama", "qwen3:30b") == 8192
    reg2, sampler2, _, _ = _fleet(sample=False)
    sampler2.record(reg2.get("ollama"), probe_node(reg2.get("ollama"), get=lambda *a, **k: 1 / 0)[0])
    assert W.probed_window(reg2, sampler2, "ollama", "qwen3:30b") is None


def test_the_ladder_makes_no_request():
    reg, sampler, net, _ = _fleet()
    before = len(net.requests)
    d = _d(reg, sampler)
    for provider, model in (("ollama", "qwen3:30b"), ("custom", "glm-5.3-flash"),
                            ("fleet-dgx", "worker"), ("fleet-dgx", "far"), ("ollama", "gemma4:26b")):
        _context_window_source(d, provider, model)
    assert len(net.requests) == before


def test_probed_window_never_raises():
    class Broken:
        def latest(self, node_id):
            raise RuntimeError("boom")

    reg, _, _, _ = _fleet(sample=False)
    assert W.probed_window(reg, Broken(), "ollama", "qwen3:30b") is None
    assert W.probed_window(None, Broken(), "ollama", "qwen3:30b") is None
    assert W.probed_window(reg, Broken(), "ollama", "") is None


# --------------------------------------------------------------------------- #
# the real platform: the planners and the reply budget read it
# --------------------------------------------------------------------------- #


def test_the_platforms_planners_and_reply_budget_read_the_probed_window(tmp_path):
    """``platform.fleet_sampler`` (set by the app) is what the agent runtime's
    planner (``_context_window(SimpleNamespace(platform=p), …)``) and the
    local endpoint's reply budget (``providers.reply_facts``) read."""
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path / "home"))
    p.config.ollama_base_url = OLLAMA
    net = Net()
    p.fleet_sampler = FleetSampler(p.fleet, probe=functools.partial(probe_node, get=net.get))
    d = SimpleNamespace(platform=p)
    assert _context_window(d, "ollama", "qwen3:30b") is None  # nothing probed yet
    p.fleet_sampler.sample_once(force=True)
    assert _context_window(d, "ollama", "qwen3:30b") == 8192
    assert p.providers.reply_facts_of("ollama", "qwen3:30b")["window"] == 8192
