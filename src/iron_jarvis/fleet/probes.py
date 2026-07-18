"""Fleet probes — WHAT a node is, and what it is actually doing right now.

Two entry points: :func:`detect_kind` (one-time classification of a base URL)
and :func:`probe_node` (one sampling pass over an already-classified node).
Every network call goes through an injected ``get`` so the whole module runs
offline in tests against real captured fixtures.

The honesty rule this module exists to enforce: **a metric we could not read is
``None``, never ``0``**. A node we could not reach NEVER gets a zeroed
``NodeMetrics`` — it gets a status, a human phrase for the transport error, and
(when we know why) an actionable hint. Zeros render as a healthy idle server;
that lie is far worse than a blank card.

"Not probeable" is a first-class state, not a failure. The user's LiteLLM proxy
fronts vLLM servers bound to localhost on OTHER machines: the proxy reaches
them, this machine cannot. That is topology, not breakage, so those nodes say
so and offer the one command that would change it.
"""

from __future__ import annotations

import ipaddress
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from . import prometheus
from .models import FleetNode, ModelEntry, NodeKind, NodeMetrics, NodeSnapshot

#: Ollama serves no Prometheus endpoint at all — this is the honest reason we
#: show instead of an empty metrics panel (loaded models + VRAM stand in).
OLLAMA_NO_METRICS = (
    "Ollama exposes no Prometheus endpoint; showing loaded models and VRAM instead"
)

#: LiteLLM's ``model`` values are provider-prefixed (``hosted_vllm/nvidia/…``).
#: Strip only these known routing prefixes when quoting a real serve command —
#: never strip a bare ``openai/…`` which is part of the HF id itself.
_LITELLM_PREFIXES = (
    "hosted_vllm",
    "openai_like",
    "text-completion-openai",
    "openrouter",
    "vertex_ai",
    "ollama",
    "ollama_chat",
)


class ProbeUnreachable(Exception):
    """A node we could not read.

    ``phrase`` is the short human summary the UI shows ("connection refused");
    ``detail`` keeps the raw transport text verbatim so nothing is lost on the
    way to a bug report.
    """

    def __init__(self, phrase: str, detail: str = "") -> None:
        super().__init__(phrase)
        self.phrase = phrase
        self.detail = detail or phrase


def _get(url: str, headers: dict[str, str] | None = None) -> Any:
    """Default transport. Short timeout — probing runs on a loop and a hung
    node must never stall the sampler."""
    import httpx

    return httpx.get(url, headers=headers or {}, timeout=3.0)


Getter = Callable[..., Any]


# --------------------------------------------------------------------------- #
# url + error normalization
# --------------------------------------------------------------------------- #
def normalize_root(base_url: str) -> str:
    """Host root for a node URL. Accepts a bare host, a ``/v1`` base, or a full
    ``/v1/chat/completions`` URL — same stripping ladder as
    ``providers/discovery.py``, except we want the ROOT (``/api/ps``,
    ``/metrics`` and ``/model/info`` all live above ``/v1``)."""
    u = (base_url or "").strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")].rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")].rstrip("/")
    return u


def _human_error(exc: BaseException) -> str:
    """One plain phrase for a transport failure. The user should never have to
    read a Python traceback to learn their server is down."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timed out" in msg or "timeout" in msg:
        return "timed out"
    if (
        "gaierror" in name
        or "getaddrinfo" in msg
        or "name or service not known" in msg
        or "nodename nor servname" in msg
        or "no address associated" in msg
    ):
        return "dns failure"
    if "refused" in msg or "connectionrefused" in name:
        return "connection refused"
    if "unreachable" in msg:
        return "network unreachable"
    if "connect" in name or "connection" in name:
        return "connection failed"
    return str(exc).strip() or type(exc).__name__


def _fetch(get: Getter, url: str, headers: dict[str, str] | None = None) -> Any:
    """One GET. Raises :class:`ProbeUnreachable` with a human phrase for any
    transport failure OR non-2xx status — the single funnel every probe goes
    through, so no probe can invent data for a request that did not land."""
    try:
        resp = get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001 — every transport failure is data
        raise ProbeUnreachable(_human_error(exc), str(exc)) from exc
    code = int(getattr(resp, "status_code", 0) or 0)
    if not 200 <= code < 300:
        raise ProbeUnreachable(f"http {code}", f"{url} returned http {code}")
    return resp


def _payload(resp: Any) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ProbeUnreachable("invalid json response", str(exc)) from exc
    return body if isinstance(body, dict) else {}


def _int(value: Any) -> int | None:
    """``None`` for anything we cannot read as an int — NOT 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in (text or "").strip().lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def detect_kind(base_url: str, *, get: Getter = _get) -> tuple[NodeKind, str]:
    """Classify a server by what it answers. Returns ``(kind, error)`` — the
    error is the last transport failure, kept verbatim, and is only meaningful
    when the kind is ``unknown``.

    ORDER MATTERS: LiteLLM is checked BEFORE the generic ``/v1/models`` probe.
    A LiteLLM proxy also answers ``/v1/models``, so probing that first would
    classify it as a plain ``openai-compat`` endpoint and the entire topology
    behind it (which servers, which are reachable) would be lost.
    """
    root = normalize_root(base_url)
    if not root:
        return "unknown", "no base_url"
    last = ""  # last real transport failure
    last_status = ""  # last "host answered, just not this API"

    def _try(path: str) -> Any | None:
        nonlocal last, last_status
        try:
            return _fetch(get, f"{root}{path}")
        except ProbeUnreachable as exc:
            if exc.phrase.startswith("http "):
                last_status = exc.detail
            else:
                last = exc.detail
            return None

    # 1. Ollama — native API, no /metrics, no OpenAI /v1 on older builds.
    resp = _try("/api/version")
    if resp is not None:
        try:
            if str(_payload(resp).get("version") or ""):
                return "ollama", ""
        except ProbeUnreachable:
            pass

    # 2. LiteLLM — the only server that reports its own upstream topology.
    resp = _try("/model/info")
    if resp is not None:
        try:
            data = _payload(resp).get("data")
        except ProbeUnreachable:
            data = None
        if isinstance(data, list) and any(
            isinstance(e, dict) and e.get("litellm_params") for e in data
        ):
            return "litellm", ""

    # 3. vLLM — the engine metrics namespace is unmistakable.
    resp = _try("/metrics")
    if resp is not None and "vllm:" in str(getattr(resp, "text", "") or ""):
        return "vllm", ""

    # 4. Anything else speaking the OpenAI API (LM Studio, llama.cpp, TGI…).
    resp = _try("/v1/models")
    if resp is not None:
        try:
            if isinstance(_payload(resp).get("data"), list):
                return "openai-compat", ""
        except ProbeUnreachable:
            pass

    return "unknown", last or last_status


# --------------------------------------------------------------------------- #
# unreachable — the honesty core
# --------------------------------------------------------------------------- #
def _is_private_host(host: str) -> bool:
    """True for a host only reachable from inside some other network: loopback,
    RFC1918/CGNAT, link-local, an mDNS/LAN suffix, or a bare hostname."""
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return False
    if h == "localhost" or h.endswith((".local", ".lan", ".internal", ".home")):
        return True
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        return "." not in h  # "spark-049d" — a LAN name, not a public one
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _serve_model(default_model: str) -> str:
    """The model id as the SERVER knows it (LiteLLM prefixes its routing
    provider onto the real id)."""
    model = (default_model or "").strip()
    if not model:
        return "<model>"
    head, _, rest = model.partition("/")
    return rest if rest and head in _LITELLM_PREFIXES else model


def _bind_hint(node: FleetNode) -> dict[str, Any] | None:
    """Why this node is dark, and the one change that would light it up.

    A topology child with NO ``api_base`` is not broken at all — it is served
    by a remote provider through the proxy, so there is nothing local to reach.
    Saying "connection refused" about it would be a lie.
    """
    base = (node.base_url or "").strip()
    if not base:
        return {
            "text": (
                "Served by a remote provider through the proxy — there is "
                "nothing local to probe."
            ),
            "action": "none",
            "commands": [],
        }
    parts = urlsplit(base if "//" in base else f"http://{base}")
    host = parts.hostname or ""
    if not _is_private_host(host):
        return None
    port = parts.port or "<port>"
    return {
        "text": (
            f"{host} listens on localhost/LAN only, so this machine cannot "
            "reach it directly — the proxy can. What is shown for it comes "
            "from the proxy, not the server."
        ),
        "action": "bind-to-tailscale",
        "commands": [
            f"# on {host}:",
            f"vllm serve {_serve_model(node.default_model)} "
            f"--host 0.0.0.0 --port {port}",
        ],
    }


def _unreadable(node: FleetNode, error: str, started: float) -> NodeSnapshot:
    """The ONE constructor for a node we could not read.

    ``metrics`` and ``rates`` stay ``None`` unconditionally — there is no code
    path in this module that builds a ``NodeMetrics`` for an unreached node.
    A topology child is ``not-probeable`` (we know it exists secondhand, via
    the proxy: evidence ``proxy``); a node we own directly is ``offline``.
    """
    child = bool(node.parent_id)
    return NodeSnapshot(
        node=node,
        status="not-probeable" if child else "offline",
        evidence="proxy" if child else "none",
        latency_ms=_elapsed_ms(started),
        error=error,
        # Bind advice only makes sense for a topology child: we learned it from
        # a proxy that CAN reach it, so "bind it wider" is a real fix. For a
        # node the user added themselves, "it's down" is the whole story.
        hint=_bind_hint(node) if child else None,
        metrics_supported=False,
        metrics_reason="not read — the node could not be reached",
        metrics=None,
        rates=None,
        models=[],
        sampled_at=time.time(),
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


# --------------------------------------------------------------------------- #
# per-kind probes
# --------------------------------------------------------------------------- #
def _probe_ollama(node: FleetNode, root: str, get: Getter) -> NodeSnapshot:
    """``/api/ps`` (what is resident in VRAM right now) unioned with
    ``/api/tags`` (what is installed). No Prometheus anywhere — that is stated,
    not hidden behind zeros."""
    ps = _payload(_fetch(get, f"{root}/api/ps"))
    models: dict[str, ModelEntry] = {}
    for m in ps.get("models") or []:
        name = str((m.get("name") or m.get("model") or "")).strip()
        if not name:
            continue
        details = m.get("details") or {}
        models[name] = ModelEntry(
            id=name,
            loaded=True,
            size_bytes=_int(m.get("size")),
            vram_bytes=_int(m.get("size_vram")),
            context_length=_int(m.get("context_length")),
            parameter_size=str(details.get("parameter_size") or ""),
            quantization=str(details.get("quantization_level") or ""),
            expires_at=str(m.get("expires_at") or ""),
        )
    # /api/tags is a bonus (installed-but-cold models). Its failure must not
    # sink a node whose /api/ps already answered.
    try:
        tags = _payload(_fetch(get, f"{root}/api/tags"))
    except ProbeUnreachable:
        tags = {}
    for m in tags.get("models") or []:
        name = str((m.get("name") or m.get("model") or "")).strip()
        if not name or name in models:
            continue
        details = m.get("details") or {}
        models[name] = ModelEntry(
            id=name,
            loaded=False,
            size_bytes=_int(m.get("size")),
            parameter_size=str(details.get("parameter_size") or ""),
            quantization=str(details.get("quantization_level") or ""),
        )
    return NodeSnapshot(
        node=node,
        status="online",
        evidence="direct",
        metrics_supported=False,
        metrics_reason=OLLAMA_NO_METRICS,
        metrics=None,
        rates=None,
        models=list(models.values()),
    )


def _probe_vllm(node: FleetNode, root: str, get: Getter) -> NodeSnapshot:
    """Full Prometheus. Every field is whatever ``sum_by`` found — a metric the
    server does not export stays ``None`` rather than collapsing to 0."""
    body = str(getattr(_fetch(get, f"{root}/metrics"), "text", "") or "")
    idx = prometheus.index(prometheus.parse_text(body))
    metrics = NodeMetrics(
        requests_running=prometheus.sum_by(idx, "vllm:num_requests_running"),
        requests_waiting=prometheus.sum_by(idx, "vllm:num_requests_waiting"),
        # A RATIO (0..1 per engine), so it is reduced with max, not sum: two
        # engines at 0.9 summed would render an impossible "180% KV cache".
        # Max answers what the number is for — how close to full is the
        # most-pressured engine.
        kv_cache_usage=prometheus.max_by(idx, "vllm:kv_cache_usage_perc"),
        generation_tokens_total=prometheus.sum_by(idx, "vllm:generation_tokens_total"),
        prompt_tokens_total=prometheus.sum_by(idx, "vllm:prompt_tokens_total"),
        prefix_cache_queries_total=prometheus.sum_by(
            idx, "vllm:prefix_cache_queries_total"
        ),
        prefix_cache_hits_total=prometheus.sum_by(idx, "vllm:prefix_cache_hits_total"),
    )
    models: list[ModelEntry] = []
    try:
        listing = _payload(_fetch(get, f"{root}/v1/models"))
    except ProbeUnreachable:
        listing = {}  # metrics already prove the node is up; model list is extra
    for m in listing.get("data") or []:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        models.append(
            ModelEntry(
                id=mid,
                loaded=True,  # vLLM serves exactly what it has loaded
                max_model_len=_int(m.get("max_model_len")),
                hf_id=str(m.get("root") or ""),
            )
        )
    return NodeSnapshot(
        node=node,
        status="online",
        evidence="direct",
        metrics_supported=True,
        metrics=metrics,
        rates=None,  # rates need two samples — the sampler derives them
        models=models,
    )


def _litellm_metrics_reason(root: str, get: Getter) -> str:
    """Probe ``/metrics`` ONCE to learn the real reason, instead of guessing.
    The user's proxy 404s here; other deployments enable it."""
    try:
        resp = get(f"{root}/metrics", headers=None)
    except Exception as exc:  # noqa: BLE001
        return f"LiteLLM /metrics could not be read ({_human_error(exc)})"
    code = int(getattr(resp, "status_code", 0) or 0)
    if code == 404:
        return "LiteLLM /metrics is not enabled on this proxy"
    if not 200 <= code < 300:
        return f"LiteLLM /metrics returned http {code}"
    return (
        "LiteLLM /metrics is enabled, but proxy counters are not engine "
        "metrics — per-node numbers come from the servers behind it"
    )


def _health_sets(
    root: str, get: Getter
) -> tuple[set[str], set[str], set[str], set[str]]:
    """``(healthy_ids, healthy_models, unhealthy_ids, unhealthy_models)`` from
    ``/health``. Empty sets mean "the proxy did not tell us" — which maps to
    ``unknown``, never to ``unhealthy``."""
    try:
        health = _payload(_fetch(get, f"{root}/health"))
    except ProbeUnreachable:
        return set(), set(), set(), set()

    def _split(key: str) -> tuple[set[str], set[str]]:
        ids: set[str] = set()
        names: set[str] = set()
        for e in health.get(key) or []:
            if not isinstance(e, dict):
                continue
            if e.get("model_id"):
                ids.add(str(e["model_id"]))
            if e.get("model"):
                names.add(str(e["model"]))
        return ids, names

    healthy_ids, healthy_models = _split("healthy_endpoints")
    unhealthy_ids, unhealthy_models = _split("unhealthy_endpoints")
    return healthy_ids, healthy_models, unhealthy_ids, unhealthy_models


def _probe_litellm(
    node: FleetNode, root: str, get: Getter
) -> tuple[NodeSnapshot, list[FleetNode]]:
    """The proxy is the only node that knows the SHAPE of the fleet: every
    alias, and the upstream URL it routes to. Those upstreams become topology
    children — never routable themselves, because the proxy is the routable
    surface and the children usually are not reachable from here at all.

    Per-child ``proxy_health`` comes from :func:`litellm_child_health` (it
    belongs on the CHILD's snapshot, which this call does not build)."""
    info = _payload(_fetch(get, f"{root}/model/info"))
    children: list[FleetNode] = []
    models: list[ModelEntry] = []
    for entry in info.get("data") or []:
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("model_name") or "").strip()
        if not alias:
            continue
        params = entry.get("litellm_params") or {}
        model = str(params.get("model") or "")
        api_base = str(params.get("api_base") or "")
        child = FleetNode(
            id=f"{node.id}-{_slug(alias)}",
            label=alias,
            base_url=api_base,
            kind="unknown",  # detection runs against the child's own URL
            source="topology",
            parent_id=node.id,
            alias=alias,
            default_model=model,
            routable=False,  # ALWAYS — route through the proxy, not around it
        )
        children.append(child)
        models.append(ModelEntry(id=alias, hf_id=model))
    snap = NodeSnapshot(
        node=node,
        status="online",
        evidence="direct",
        metrics_supported=False,
        metrics_reason=_litellm_metrics_reason(root, get),
        metrics=None,
        rates=None,
        models=models,
        children=[c.id for c in children],
        proxy_health="healthy",  # it answered us
    )
    return snap, children


def _probe_openai_compat(node: FleetNode, root: str, get: Getter) -> NodeSnapshot:
    """``/v1/models`` is all a generic OpenAI-compatible server owes us."""
    listing = _payload(_fetch(get, f"{root}/v1/models"))
    models = [
        ModelEntry(
            id=str(m.get("id")),
            max_model_len=_int(m.get("max_model_len")),
            hf_id=str(m.get("root") or ""),
        )
        for m in (listing.get("data") or [])
        if isinstance(m, dict) and m.get("id")
    ]
    return NodeSnapshot(
        node=node,
        status="online",
        evidence="direct",
        metrics_supported=False,
        metrics_reason=(
            "This server exposes an OpenAI-compatible API but no Prometheus "
            "/metrics endpoint"
        ),
        metrics=None,
        rates=None,
        models=models,
    )


# --------------------------------------------------------------------------- #
# public probe
# --------------------------------------------------------------------------- #
def probe_node(
    node: FleetNode, *, get: Getter = _get
) -> tuple[NodeSnapshot, list[FleetNode]]:
    """One sampling pass. Returns ``(snapshot, discovered_children)`` — children
    are only ever produced by a LiteLLM proxy, every other kind returns ``[]``.

    Any failure lands in :func:`_unreadable`, so an unreachable node can only
    come back with ``metrics=None`` / ``rates=None``.
    """
    started = time.perf_counter()
    root = normalize_root(node.base_url)
    if not root:
        # A topology child with no upstream URL (an alias the proxy serves from
        # a remote provider). Not a failure: no error text, just the honest
        # explanation in the hint.
        if node.parent_id:
            return (
                NodeSnapshot(
                    node=node,
                    status="not-probeable",
                    evidence="proxy",
                    error="",
                    hint=_bind_hint(node),
                    metrics_supported=False,
                    metrics_reason="remote provider — nothing local to measure",
                    metrics=None,
                    rates=None,
                    latency_ms=None,
                    sampled_at=time.time(),
                ),
                [],
            )
        return _unreadable(node, "no base_url configured", started), []

    children: list[FleetNode] = []
    try:
        if node.kind == "ollama":
            snap = _probe_ollama(node, root, get)
        elif node.kind == "vllm":
            snap = _probe_vllm(node, root, get)
        elif node.kind == "litellm":
            snap, children = _probe_litellm(node, root, get)
        elif node.kind == "openai-compat":
            snap = _probe_openai_compat(node, root, get)
        else:
            # Kind not established yet (a fresh topology child). We still have
            # to answer "can this machine reach it at all?" — that reachability
            # question is what produces the not-probeable state and its hint.
            snap = _probe_openai_compat(node, root, get)
            snap.metrics_reason = (
                "Node kind not detected yet — run detection for full metrics"
            )
    except ProbeUnreachable as exc:
        return _unreadable(node, exc.phrase, started), []
    snap.latency_ms = _elapsed_ms(started)
    snap.sampled_at = time.time()
    return snap, children


def litellm_child_health(node: FleetNode, *, get: Getter = _get) -> dict[str, str]:
    """``{child_id: healthy|unhealthy|unknown}`` for one LiteLLM proxy.

    ``NodeSnapshot.proxy_health`` lives on the CHILD's snapshot, but only the
    parent can learn it (``/health`` is a proxy endpoint). The sampler applies
    this map onto child snapshots after probing the parent.
    """
    root = normalize_root(node.base_url)
    if not root:
        return {}
    try:
        info = _payload(_fetch(get, f"{root}/model/info"))
    except ProbeUnreachable:
        return {}
    healthy_ids, healthy_models, unhealthy_ids, unhealthy_models = _health_sets(
        root, get
    )
    out: dict[str, str] = {}
    for entry in info.get("data") or []:
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("model_name") or "").strip()
        if not alias:
            continue
        model = str((entry.get("litellm_params") or {}).get("model") or "")
        model_id = str((entry.get("model_info") or {}).get("id") or "")
        child_id = f"{node.id}-{_slug(alias)}"
        if model_id in healthy_ids or model in healthy_models:
            out[child_id] = "healthy"
        elif model_id in unhealthy_ids or model in unhealthy_models:
            out[child_id] = "unhealthy"
        else:
            out[child_id] = "unknown"
    return out
