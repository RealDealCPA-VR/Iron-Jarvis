"""Fleet data shapes — the vocabulary every fleet module speaks.

A "fleet" is the user's own inference hardware: Ollama boxes, vLLM servers, and
LiteLLM proxies that front OTHER servers. These models exist to keep one promise
that the rest of the feature is built around:

    **A metric we could not read is ``None``, never ``0``.**

That is why almost every numeric field here is ``| None`` with a ``None``
default. A vLLM server reporting ``vllm:num_requests_running 0.0`` and a server
we could not reach at all are completely different facts, and the dashboard must
be able to tell them apart. Collapsing the second into a confident zero would
show the user an idle-looking green node that is in fact dead — exactly the
fabricated-output failure this project refuses to ship.

The same honesty drives :class:`NodeStatus`: a node behind a LiteLLM proxy whose
``api_base`` we cannot open (bound to localhost on the far host) is
``"not-probeable"`` — a THIRD state, distinct from both ``"online"`` and
``"offline"``. We know it exists, we know its address, we simply cannot ask it
anything from here, and its ``metrics`` stay ``None``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: What kind of server lives at a base URL. ``"openai-compat"`` is the honest
#: fallback for something that answers ``/v1/models`` but identifies as nothing
#: more specific; ``"unknown"`` means detection has not run or failed.
NodeKind = Literal["ollama", "vllm", "litellm", "openai-compat", "unknown"]

#: Reachability, as four distinct facts. ``"not-probeable"`` is the load-bearing
#: one: the node is KNOWN (a proxy told us about it) but unreachable from this
#: machine, so it gets a bind hint instead of invented numbers.
NodeStatus = Literal["online", "offline", "not-probeable", "unknown"]


class FleetNode(BaseModel):
    """One inference endpoint the user owns — config, not observation.

    Nothing here is measured; it is the durable description of a node. Anything
    we learned by asking the node lives on :class:`NodeSnapshot` instead, so a
    node's identity survives every failed probe unchanged.
    """

    id: str
    label: str = ""
    base_url: str = ""
    kind: NodeKind = "unknown"
    #: When ``kind`` was last confirmed by a probe (monotonic-free wall clock).
    #: ``0.0`` means "never detected" — the kind is a guess from config.
    kind_detected_at: float = 0.0
    #: How this node entered the registry. ``"topology"`` = discovered as a
    #: child of a proxy, so re-absorbing a proxy may legitimately replace it.
    source: Literal["config", "user", "topology"] = "user"
    #: The proxy that fronts this node (empty for a top-level node).
    parent_id: str = ""
    #: The name the parent proxy routes by (e.g. "coder"), not the model id.
    alias: str = ""
    #: Secret-vault key name for this node's API key — never the key itself.
    api_key_name: str = ""
    enabled: bool = True
    #: Whether this node is registered as a provider the router may send work
    #: to. Off by default: discovering a server is not consent to use it.
    routable: bool = False
    default_model: str = ""
    #: Capability flags. ``None`` = unverified, which is NOT the same as
    #: ``False`` ("we checked, it cannot").
    tool_use: bool | None = None
    vision: bool | None = None
    verified_at: float = 0.0


class ModelEntry(BaseModel):
    """A model as reported by a node — every field optional by design.

    Different servers volunteer wildly different facts: Ollama's ``/api/ps``
    knows ``size_vram``/``expires_at``/quantization, vLLM's ``/v1/models`` knows
    ``max_model_len``/``root``, a LiteLLM alias knows only its upstream id.
    Whatever a given server does not say stays ``None``/``""`` rather than being
    back-filled with a plausible-looking default.
    """

    id: str
    #: Ollama distinguishes "installed" from "loaded in VRAM right now".
    #: ``None`` = the node does not report loadedness at all.
    loaded: bool | None = None
    size_bytes: int | None = None
    vram_bytes: int | None = None
    context_length: int | None = None
    parameter_size: str = ""
    quantization: str = ""
    #: Ollama's model-unload deadline, kept as the server's own ISO string.
    expires_at: str = ""
    max_model_len: int | None = None
    #: The upstream/HuggingFace id behind an alias (vLLM ``root``, LiteLLM
    #: ``litellm_params.model``).
    hf_id: str = ""


class NodeMetrics(BaseModel):
    """A single Prometheus scrape, as raw counters/gauges.

    Every field is ``None`` when the metric was absent from the payload and a
    real number (including ``0.0``) when the server actually reported it. The
    parser preserves that distinction; do not "helpfully" default these to 0.
    """

    requests_running: float | None = None
    requests_waiting: float | None = None
    #: Fraction, not percent: 1.0 means the KV cache is full.
    kv_cache_usage: float | None = None
    generation_tokens_total: float | None = None
    prompt_tokens_total: float | None = None
    prefix_cache_queries_total: float | None = None
    prefix_cache_hits_total: float | None = None


class NodeRates(BaseModel):
    """Deltas derived from two consecutive scrapes — never from one.

    Rates need a previous sample, so the first scrape of a node legitimately has
    all-``None`` rates. ``counter_reset`` records that a counter went BACKWARDS
    (the server restarted): the window is unusable, so the rates are ``None``
    and this flag explains why instead of reporting a bogus negative or zero.
    """

    window_seconds: float | None = None
    generation_tps: float | None = None
    prompt_tps: float | None = None
    prefix_cache_hit_rate: float | None = None
    counter_reset: bool = False


class NodeSnapshot(BaseModel):
    """Everything we observed about one node at one moment.

    ``evidence`` is how we know: ``"direct"`` = we talked to the node itself,
    ``"proxy"`` = only its proxy vouched for it, ``"none"`` = we know nothing.
    A ``"proxy"`` snapshot may say ``status="online"`` while ``metrics`` stays
    ``None`` — the health is second-hand and the numbers were never readable.
    """

    node: FleetNode
    status: NodeStatus = "unknown"
    evidence: Literal["direct", "proxy", "none"] = "none"
    latency_ms: float | None = None
    #: Human-readable failure text. Empty on success — never a fake success.
    error: str = ""
    #: Actionable next step for a failure (e.g. the ``--host 0.0.0.0`` bind hint
    #: for a node bound to localhost on another machine).
    hint: dict | None = None
    #: False for servers that simply have no ``/metrics`` (Ollama, LiteLLM).
    #: ``metrics_reason`` says WHY so the UI shows "no metrics endpoint" rather
    #: than an empty gauge the user reads as zero load.
    metrics_supported: bool = False
    metrics_reason: str = ""
    metrics: NodeMetrics | None = None
    rates: NodeRates | None = None
    models: list[ModelEntry] = []
    #: Node ids this node fronts (LiteLLM proxies only).
    children: list[str] = []
    proxy_health: Literal["healthy", "unhealthy", "unknown"] = "unknown"
    sampled_at: float = 0.0
