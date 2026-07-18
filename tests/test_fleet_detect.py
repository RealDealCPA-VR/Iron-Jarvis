"""Offline tests for fleet node detection, driven by REAL captured responses.

Every fixture in ``tests/fixtures/fleet/`` came off the user's actual hardware
(an Ollama tower, a LiteLLM proxy, a vLLM server), so these tests pin the
shapes we truly have to parse — not shapes we imagined.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from iron_jarvis.fleet import probes


FIXTURES = Path(__file__).parent / "fixtures" / "fleet"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str):
    return json.loads(_text(name))


# --------------------------------------------------------------------------- #
# a URL-routed fake transport
# --------------------------------------------------------------------------- #
class _Resp:
    """Minimal httpx-shaped response: ``.status_code``, ``.text``, ``.json()``."""

    def __init__(self, status_code: int = 200, text: str = "", payload=None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


def _server(routes: dict, *, missing=None):
    """Build a ``get(url, headers=None)`` that answers by PATH. Anything not in
    ``routes`` 404s (or raises ``missing`` — e.g. a refused connection)."""

    def get(url: str, headers=None):
        path = urlsplit(url).path.rstrip("/") or "/"
        hit = routes.get(path)
        if hit is None:
            if missing is not None:
                raise missing
            return _Resp(404, "not found")
        if isinstance(hit, BaseException):
            raise hit
        return hit

    return get


def _ollama_server():
    return _server(
        {
            "/api/version": _Resp(payload=_json("ollama_version.json")),
            "/api/ps": _Resp(payload=_json("ollama_ps.json")),
            "/api/tags": _Resp(payload=_json("ollama_tags.json")),
        }
    )


def _vllm_server():
    return _server(
        {
            "/metrics": _Resp(text=_text("vllm_metrics.txt")),
            "/v1/models": _Resp(payload=_json("vllm_models.json")),
        }
    )


def _litellm_server():
    """The real trap: a LiteLLM proxy ALSO answers /v1/models."""
    aliases = [e["model_name"] for e in _json("litellm_model_info.json")["data"]]
    return _server(
        {
            "/model/info": _Resp(payload=_json("litellm_model_info.json")),
            "/health": _Resp(payload=_json("litellm_health.json")),
            "/metrics": _Resp(404, "Not Found"),
            "/v1/models": _Resp(
                payload={"object": "list", "data": [{"id": a} for a in aliases]}
            ),
        }
    )


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def test_ollama_detected_from_real_version_payload():
    kind, err = probes.detect_kind("http://100.87.42.62:8003", get=_ollama_server())
    assert (kind, err) == ("ollama", "")


def test_vllm_detected_from_real_prometheus_body():
    kind, err = probes.detect_kind("http://100.66.161.52:8888", get=_vllm_server())
    assert (kind, err) == ("vllm", "")


def test_litellm_detected_from_real_model_info():
    kind, err = probes.detect_kind("http://100.66.161.52:4000", get=_litellm_server())
    assert (kind, err) == ("litellm", "")


def test_litellm_is_not_misdetected_as_openai_compat():
    """LiteLLM answers /v1/models like any OpenAI server. Probing that first
    would classify the proxy as a plain endpoint and lose the whole topology
    behind it — so /model/info MUST be checked first."""
    kind, _ = probes.detect_kind("http://proxy:4000", get=_litellm_server())
    assert kind == "litellm"


def test_generic_openai_compatible_server_detected():
    get = _server({"/v1/models": _Resp(payload={"data": [{"id": "local-model"}]})})
    assert probes.detect_kind("http://127.0.0.1:1234", get=get)[0] == "openai-compat"


def test_openai_compat_requires_a_data_list():
    """A 200 that is not a model listing is not an OpenAI-compatible server."""
    get = _server({"/v1/models": _Resp(payload={"error": "unauthorized"})})
    assert probes.detect_kind("http://127.0.0.1:1234", get=get)[0] == "unknown"


def test_all_probes_failing_yields_unknown_with_the_transport_error_verbatim():
    get = _server({}, missing=ConnectionRefusedError("[Errno 111] Connection refused"))
    kind, err = probes.detect_kind("http://spark-049d:8000", get=get)
    assert kind == "unknown"
    assert "Connection refused" in err  # raw text preserved, not swallowed


def test_http_status_error_is_reported_when_there_is_no_transport_failure():
    get = _server({}, missing=None)  # everything 404s
    kind, err = probes.detect_kind("http://host:9999", get=get)
    assert kind == "unknown"
    assert "404" in err


def test_the_three_base_url_forms_detect_identically():
    """Users paste a bare host, a /v1 base, or the full chat URL from their
    config — all three name the same server."""
    forms = [
        "http://100.66.161.52:8888",
        "http://100.66.161.52:8888/v1",
        "http://100.66.161.52:8888/v1/chat/completions",
    ]
    assert {probes.detect_kind(f, get=_vllm_server())[0] for f in forms} == {"vllm"}
    assert {probes.detect_kind(f, get=_ollama_server())[0] for f in forms} == {"ollama"}
    assert {probes.detect_kind(f, get=_litellm_server())[0] for f in forms} == {
        "litellm"
    }


def test_normalize_root_strips_v1_and_chat_completions():
    root = "http://h:8000"
    assert probes.normalize_root("http://h:8000/v1/chat/completions/") == root
    assert probes.normalize_root("http://h:8000/v1") == root
    assert probes.normalize_root("http://h:8000/") == "http://h:8000"
    assert probes.normalize_root("") == ""


def test_empty_base_url_is_not_probed_at_all():
    def _explode(url, headers=None):  # pragma: no cover - must never run
        raise AssertionError("detect_kind must not hit the network for a blank url")

    assert probes.detect_kind("", get=_explode) == ("unknown", "no base_url")


# --------------------------------------------------------------------------- #
# probing each detected kind against its real fixture
# --------------------------------------------------------------------------- #
def _node(**kw):
    from iron_jarvis.fleet.models import FleetNode

    return FleetNode(**kw)


def test_ollama_probe_reports_loaded_vram_and_says_why_there_are_no_metrics():
    node = _node(id="tower", base_url="http://100.87.42.62:8003", kind="ollama")
    snap, children = probes.probe_node(node, get=_ollama_server())
    assert (snap.status, snap.evidence, children) == ("online", "direct", [])
    # No Prometheus is a FACT about Ollama, stated plainly — not zeroed metrics.
    assert snap.metrics_supported is False
    assert snap.metrics is None and snap.rates is None
    assert "no Prometheus" in snap.metrics_reason

    by_id = {m.id: m for m in snap.models}
    gemma = by_id["gemma4:26b"]
    assert gemma.loaded is True
    assert gemma.vram_bytes == 20938688640
    assert gemma.context_length == 32768
    assert (gemma.parameter_size, gemma.quantization) == ("25.8B", "Q4_K_M")
    assert gemma.expires_at.startswith("2318-")
    # /api/tags adds the installed-but-cold models, flagged as not loaded.
    assert by_id["minicpm-v:latest"].loaded is False
    assert by_id["mxbai-embed-large:latest"].loaded is False


def test_ollama_probe_survives_a_tags_failure():
    """/api/ps answered — the node is up. A flaky /api/tags must not erase it."""
    get = _server(
        {
            "/api/ps": _Resp(payload=_json("ollama_ps.json")),
            "/api/tags": TimeoutError("timed out"),
        }
    )
    snap, _ = probes.probe_node(
        _node(id="tower", base_url="http://t:8003", kind="ollama"), get=get
    )
    assert snap.status == "online"
    assert {m.id for m in snap.models} == {"gemma4:26b", "qwen3.6:27b"}


def test_vllm_probe_reads_real_prometheus_counters():
    node = _node(id="vllm", base_url="http://100.66.161.52:8888", kind="vllm")
    snap, children = probes.probe_node(node, get=_vllm_server())
    assert (snap.status, snap.evidence, snap.metrics_supported) == (
        "online",
        "direct",
        True,
    )
    assert children == []
    m = snap.metrics
    assert m is not None
    assert m.requests_running == 0.0
    assert m.requests_waiting == 0.0
    assert m.generation_tokens_total == 1763.0
    assert m.prompt_tokens_total == 1227019.0
    assert m.prefix_cache_queries_total == 1227019.0
    assert m.prefix_cache_hits_total == 91392.0
    # rates need two samples; one probe cannot know them.
    assert snap.rates is None
    # The real payload also carries vllm:num_requests_waiting_by_reason (two
    # series). A prefix-matching lookup would silently fold those into
    # requests_waiting and triple it — names must match EXACTLY.
    assert m.requests_waiting == 0.0
    assert m.kv_cache_usage == 0.0  # genuinely reported 0, not "unreadable"
    model = snap.models[0]
    assert model.id == "deepseek-v4-flash-dspark"
    assert model.max_model_len == 1048576
    assert model.hf_id == "deepseek-ai/DeepSeek-V4-Flash-DSpark"


def test_a_metric_missing_from_a_reachable_server_stays_none_not_zero():
    """The node ANSWERED, so it is online and metrics_supported — but a counter
    its build does not export is unknown, not zero. Older vLLM releases predate
    the prefix-cache counters; showing those as 0 would report a 0% cache hit
    rate for a server that never said anything of the kind."""
    body = "\n".join(
        line
        for line in _text("vllm_metrics.txt").splitlines()
        if "prefix_cache" not in line
    )
    get = _server(
        {
            "/metrics": _Resp(text=body),
            "/v1/models": _Resp(payload=_json("vllm_models.json")),
        }
    )
    snap, _ = probes.probe_node(
        _node(id="vllm", base_url="http://h:8888", kind="vllm"), get=get
    )
    assert snap.status == "online" and snap.metrics_supported is True
    assert snap.metrics.prefix_cache_queries_total is None
    assert snap.metrics.prefix_cache_hits_total is None
    # The counters it DID export are unaffected.
    assert snap.metrics.generation_tokens_total == 1763.0


def test_litellm_probe_states_the_real_metrics_reason_it_discovered():
    node = _node(id="proxy", base_url="http://100.66.161.52:4000", kind="litellm")
    snap, children = probes.probe_node(node, get=_litellm_server())
    assert snap.status == "online"
    assert snap.metrics_supported is False
    assert snap.metrics is None
    # The reason was PROBED (a real 404), not assumed.
    assert snap.metrics_reason == "LiteLLM /metrics is not enabled on this proxy"
    assert {m.id for m in snap.models} == {"brain", "coder", "fleet", "frontier"}
    assert snap.children == [c.id for c in children]


def test_litellm_child_health_maps_the_proxy_verdict_onto_children():
    node = _node(id="proxy", base_url="http://100.66.161.52:4000", kind="litellm")
    health = probes.litellm_child_health(node, get=_litellm_server())
    # Real /health: fleet + frontier answered, brain + coder refused upstream.
    assert health == {
        "proxy-brain": "unhealthy",
        "proxy-coder": "unhealthy",
        "proxy-fleet": "healthy",
        "proxy-frontier": "healthy",
    }
