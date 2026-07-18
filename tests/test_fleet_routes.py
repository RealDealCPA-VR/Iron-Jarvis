"""Fleet routes (/fleet/*) — telemetry, node CRUD, savings, code routing.

Fully offline. The ``fleet.*`` modules (models/probes/registry/sampler) are
written concurrently by sibling agents, so these tests pin the HTTP CONTRACT
against faithful stand-ins built to the same signatures: a registry backed by
the real ``config.fleet_nodes`` + the real atomic persist (so the round-trip
through config.toml is genuinely exercised), and a sampler that hands back
canned snapshots. The platform under ``d`` is a REAL one from ``create_app``,
so ``/fleet/usage`` runs against the real Observability rollup and real prices.

The invariants worth breaking a build over:

* ``GET /fleet`` never raises — the dashboard polls it, so a fleet fault
  degrades to an empty list plus the verbatim error.
* an unreachable node's tool-use capability is ``null``, never ``False``.
* the savings estimate always names the baseline it was priced against.
"""

from __future__ import annotations

import sys
import types
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config, load_config, persist_config_values
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import fleet as fleet_routes


# --- stand-ins for the concurrently-written fleet.* modules -------------------


@dataclass
class _Node:
    """Mirrors ``fleet.models.FleetNode`` (incl. pydantic v2's ``model_dump``)."""

    id: str = ""
    label: str = ""
    base_url: str = ""
    kind: str = "unknown"
    kind_detected_at: float = 0.0
    source: str = "user"
    parent_id: str = ""
    alias: str = ""
    api_key_name: str = ""
    enabled: bool = True
    routable: bool = False
    default_model: str = ""
    tool_use: bool | None = None
    vision: bool | None = None
    verified_at: float = 0.0

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Metrics:
    """Mirrors ``fleet.models.NodeMetrics``; ``None`` = we could not read it."""

    requests_running: float | None = None
    requests_waiting: float | None = None
    kv_cache_usage: float | None = None
    generation_tokens_total: float | None = None
    prompt_tokens_total: float | None = None
    prefix_cache_queries_total: float | None = None
    prefix_cache_hits_total: float | None = None

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Snapshot:
    """Mirrors ``fleet.models.NodeSnapshot`` closely enough for the routes."""

    node: _Node
    status: str = "unknown"
    evidence: str = "none"
    error: str = ""
    metrics: dict | None = None
    rates: dict | None = None
    models: list = field(default_factory=list)
    sampled_at: float = 0.0

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class _Registry:
    """A faithful ``FleetRegistry`` stand-in over the REAL config + persist.

    Only the surface the routes use. Node records live in ``config.fleet_nodes``
    and every mutation writes config.toml atomically, so the persistence
    round-trip in these tests is the real one.
    """

    _ID_OK = "abcdefghijklmnopqrstuvwxyz0123456789-_."

    def __init__(self, config) -> None:
        self.config = config
        self._nodes: dict[str, _Node] = {}
        for raw in list(getattr(config, "fleet_nodes", []) or []):
            node = _Node(**raw)
            self._nodes[node.id] = node

    def _flush(self) -> None:
        # NOTE: ``atomic_write_toml`` drops only TOP-LEVEL None values, so a node
        # carrying ``tool_use=None`` (unverified) would raise "not TOML
        # serializable" on the way to config.toml. Unknown fields are simply
        # absent on disk and default back to None on load.
        self.config.fleet_nodes = [
            {k: v for k, v in n.model_dump().items() if v is not None}
            for n in self._nodes.values()
        ]
        persist_config_values(
            self.config.home, {"fleet_nodes": self.config.fleet_nodes}
        )

    def nodes(self) -> list[_Node]:
        return list(self._nodes.values())

    def get(self, node_id: str) -> _Node | None:
        return self._nodes.get(node_id)

    def add(self, node: _Node) -> _Node:
        nid = (node.id or "").strip()
        if not nid or any(c not in self._ID_OK for c in nid):
            raise ValueError(f"invalid node id {node.id!r}")
        if nid in self._nodes:
            raise ValueError(f"node {nid!r} already exists")
        self._nodes[nid] = node
        self._flush()
        return node

    def update(self, node_id: str, **fields) -> _Node | None:
        node = self._nodes.get(node_id)
        if node is None:
            return None
        for key, value in fields.items():
            setattr(node, key, value)
        self._flush()
        return node

    def remove(self, node_id: str) -> None:
        node = self._nodes.get(node_id)
        if node is not None and node.source == "config":
            raise ValueError("config-seeded node")
        self._nodes.pop(node_id, None)
        self._flush()

    def absorb_children(self, parent_id: str, children) -> None:
        for child in children:
            child.parent_id = parent_id
            self._nodes.setdefault(child.id, child)
        self._flush()

    def routable_nodes(self) -> list[_Node]:
        return [n for n in self._nodes.values() if n.routable and n.enabled]


@dataclass
class _Rates:
    """Mirrors ``fleet.models.NodeRates``."""

    window_seconds: float | None = None
    generation_tps: float | None = None
    prompt_tps: float | None = None
    prefix_cache_hit_rate: float | None = None
    counter_reset: bool = False

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class _Sampler:
    """A ``FleetSampler`` stand-in. Counts calls so "no network in GET /fleet"
    is asserted, not assumed."""

    def __init__(self, snaps=(), series=None) -> None:
        self._snaps = list(snaps)
        self._series = dict(series or {})
        self.touched = 0
        self.sampled = 0

    def touch(self) -> None:
        self.touched += 1

    def snapshots(self):
        return list(self._snaps)

    def latest(self, node_id):
        return next((s for s in self._snaps if s.node.id == node_id), None)

    def series(self, node_id, limit=None):
        rows = list(self._series.get(node_id, []))
        return rows[-limit:] if limit else rows

    def sample_once(self) -> None:
        self.sampled += 1

    def status(self) -> dict:
        return {"active": False, "interval": 30.0, "lease_expires_in": 0.0}


class _BoomSampler:
    """Every method raises — the fleet fault a dashboard poll must survive."""

    def __getattr__(self, name):
        def _raise(*_a, **_kw):
            raise RuntimeError("sampler exploded")

        return _raise


class _FakeAdapter:
    provider = "fleet-tower"
    model = "qwen3-coder"

    def __init__(self, tool_calls=()) -> None:
        self._tool_calls = list(tool_calls)
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        return types.SimpleNamespace(text="ok", tool_calls=list(self._tool_calls))


# --- wiring -------------------------------------------------------------------


def _stub_probes(monkeypatch, *, kind="vllm", snapshot=None, children=()):
    """Offline ``detect_kind``/``probe_node``, whether or not ``fleet.*`` exists.

    When the sibling modules aren't on disk yet we insert a minimal stand-in
    package; when they are, we replace ONLY the two network calls. Returns a
    counter dict so tests can assert nothing probed.
    """
    counts = {"detect": 0, "probe": 0}
    try:  # the real modules, once the sibling agents land them
        from iron_jarvis.fleet import models as fmodels, probes as fprobes
    except Exception:  # noqa: BLE001 - absent (or mid-write) => use the stand-in
        import iron_jarvis

        pkg = types.ModuleType("iron_jarvis.fleet")
        pkg.__path__ = []  # namespace-ish package, enough for `from ... import`
        fmodels = types.ModuleType("iron_jarvis.fleet.models")
        fmodels.FleetNode = _Node
        fprobes = types.ModuleType("iron_jarvis.fleet.probes")
        pkg.models, pkg.probes = fmodels, fprobes
        monkeypatch.setattr(iron_jarvis, "fleet", pkg, raising=False)
        monkeypatch.setitem(sys.modules, "iron_jarvis.fleet", pkg)
        monkeypatch.setitem(sys.modules, "iron_jarvis.fleet.models", fmodels)
        monkeypatch.setitem(sys.modules, "iron_jarvis.fleet.probes", fprobes)

    def _detect(base_url, **_kw):
        counts["detect"] += 1
        return (kind, "stubbed detection")

    def _probe(node, **_kw):
        counts["probe"] += 1
        snap = snapshot or _Snapshot(node=node, status="online", evidence="direct")
        return (snap, list(children))

    monkeypatch.setattr(fprobes, "detect_kind", _detect, raising=False)
    monkeypatch.setattr(fprobes, "probe_node", _probe, raising=False)
    return counts


def _stub_derive(monkeypatch, *, boom=False):
    """Stand in for ``sampler.derive`` and record the pairs it was handed.

    ``derive``'s SEMANTICS (counter resets, unknown windows) are the sampler
    module's contract and are covered by its own tests; what this module owns is
    that the history route feeds it CONSECUTIVE points and passes the result
    through untouched. Call after :func:`_stub_probes` — it reuses the stand-in
    ``iron_jarvis.fleet`` package that helper installs when the real one isn't
    importable yet.
    """
    calls: list[tuple] = []

    def _derive(prev, cur):
        calls.append((prev, cur))
        if boom:
            raise RuntimeError("derive exploded")
        return _Rates(window_seconds=cur[0] - prev[0])

    try:
        from iron_jarvis.fleet import sampler as fsampler
    except Exception:  # noqa: BLE001 - absent (or mid-write) => use the stand-in
        fsampler = types.ModuleType("iron_jarvis.fleet.sampler")
        monkeypatch.setitem(sys.modules, "iron_jarvis.fleet.sampler", fsampler)
        pkg = sys.modules.get("iron_jarvis.fleet")
        if pkg is not None:
            monkeypatch.setattr(pkg, "sampler", fsampler, raising=False)
    monkeypatch.setattr(fsampler, "derive", _derive, raising=False)
    return calls


def _wire(tmp_path, *, sampler=None, seed=()):
    """A bare app carrying ONLY the fleet routes, over a real platform.

    Deliberately not ``create_app``'s own app: create_app will register these
    same routes once the coordinator wires them, and a duplicate registration
    would silently shadow the stub deps this module needs.
    """
    platform = create_app(str(tmp_path)).state.platform
    for node in seed:
        platform.config.fleet_nodes.append(node.model_dump())
    registry = _Registry(platform.config)

    def _persist(keys: list[str]) -> None:
        persist_config_values(
            platform.config.home,
            {k: getattr(platform.config, k, None) for k in keys},
        )

    d = types.SimpleNamespace(
        platform=platform,
        fleet=registry,
        fleet_sampler=sampler if sampler is not None else _Sampler(),
        _persist_config=_persist,
    )
    app = FastAPI()
    fleet_routes.register(app, d)
    return TestClient(app), d


def _register_provider(d, name: str) -> None:
    """Pretend ``registry.register_providers()`` ran for this node, so the
    manager reports its ``fleet-<id>`` provider as available."""
    mgr = d.platform.providers
    real = mgr.available
    mgr.available = lambda p: True if p == name else real(p)


def _reload(d) -> Config:
    """The config as a FRESH process would read it back off disk."""
    return load_config(d.platform.config.project_root)


# --- GET /fleet ---------------------------------------------------------------


def test_fleet_overview_empty_and_touches_without_probing(tmp_path, monkeypatch):
    counts = _stub_probes(monkeypatch)
    client, d = _wire(tmp_path)
    r = client.get("/fleet")
    assert r.status_code == 200
    out = r.json()
    assert out["nodes"] == []
    assert out["error"] == ""
    # Served from the sampler: touched (a human is watching), never probed.
    assert d.fleet_sampler.touched == 1
    assert d.fleet_sampler.sampled == 0
    assert counts == {"detect": 0, "probe": 0}
    assert set(out["sampling"]) >= {"active", "interval", "lease_expires_in"}
    assert out["code_route"]["enabled"] is False


def test_fleet_overview_survives_a_broken_sampler(tmp_path, monkeypatch):
    """A fleet fault must not break dashboard polling — 200 with the verbatim
    error beats a 500 that blanks the page."""
    _stub_probes(monkeypatch)
    client, _d = _wire(tmp_path, sampler=_BoomSampler())
    r = client.get("/fleet")
    assert r.status_code == 200
    out = r.json()
    assert out["nodes"] == []
    assert "sampler exploded" in out["error"]  # verbatim, not "something failed"
    # And the refreshing variant degrades the same way.
    r2 = client.get("/fleet/snapshot?refresh=1")
    assert r2.status_code == 200
    assert "sampler exploded" in r2.json()["error"]


def test_unreachable_node_reports_null_metrics_not_zeros(tmp_path, monkeypatch):
    """The whole point of the feature: a node we cannot read has NO metrics.
    Rendering 0 requests/s for spark-76a1 would be a fabricated fact."""
    _stub_probes(monkeypatch)
    node = _Node(id="spark-76a1", base_url="http://spark-76a1:8001/v1", kind="vllm")
    snap = _Snapshot(
        node=node,
        status="not-probeable",
        evidence="proxy",
        error="ConnectError: [Errno 111] Connection refused",
        metrics=None,
        rates=None,
    )
    client, _d = _wire(tmp_path, sampler=_Sampler(snaps=[snap]))
    row = client.get("/fleet").json()["nodes"][0]
    assert row["status"] == "not-probeable"
    assert row["metrics"] is None and row["rates"] is None
    assert "Connection refused" in row["error"]


def test_snapshot_refresh_forces_one_sample(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, d = _wire(tmp_path)
    client.get("/fleet/snapshot")
    assert d.fleet_sampler.sampled == 0  # no refresh => no network
    client.get("/fleet/snapshot?refresh=1")
    assert d.fleet_sampler.sampled == 1


# --- history ------------------------------------------------------------------


def _points(n=10):
    """A ``sampler.series`` history: ``(t, NodeMetrics)`` points, oldest first,
    ending on a scrape that read nothing at all."""
    rows = [
        (
            float(i),
            _Metrics(requests_running=1.0, generation_tokens_total=float(i * 10)),
        )
        for i in range(n)
    ]
    rows.append((float(n), _Metrics()))
    return rows


def test_history_derives_rates_and_truncates(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    derived = _stub_derive(monkeypatch)
    node = _Node(id="vllm", base_url="http://100.66.161.52:8888")
    client, _d = _wire(
        tmp_path, sampler=_Sampler(series={"vllm": _points()}), seed=[node]
    )

    out = client.get("/fleet/nodes/vllm/history?limit=3").json()
    assert out["node_id"] == "vllm"
    assert out["truncated"] is True
    assert len(out["samples"]) == 3
    # Metrics pass through verbatim (the sampler retains raw counters)...
    assert out["samples"][0]["metrics"]["generation_tokens_total"] == 80.0
    # ...and the CLIPPED window's oldest row still carries rates derived from
    # the point before it, so the chart doesn't open with a hole.
    assert out["samples"][0]["rates"]["window_seconds"] == 1.0
    # A scrape that read nothing is all-null metrics — never zeros.
    last = out["samples"][-1]
    assert last["t"] == 10.0
    assert last["metrics"]["generation_tokens_total"] is None
    assert last["metrics"]["requests_running"] is None

    full = client.get("/fleet/nodes/vllm/history").json()
    assert full["truncated"] is False
    # The first sample ever has no window, so no rates — not zero rates.
    assert full["samples"][0]["rates"] is None
    # And derive really saw CONSECUTIVE points, oldest first.
    assert [(prev[0], cur[0]) for prev, cur in derived[:3]] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
    ]


def test_history_rates_are_null_when_derivation_fails(tmp_path, monkeypatch):
    """An underivable rate is UNKNOWN. Falling back to 0 tok/s would invent a
    fact about the node, and a broken overlay must not 500 the chart."""
    _stub_probes(monkeypatch)
    _stub_derive(monkeypatch, boom=True)
    node = _Node(id="vllm", base_url="http://x")
    client, _d = _wire(tmp_path, sampler=_Sampler(series={"vllm": _points(3)}), seed=[node])
    out = client.get("/fleet/nodes/vllm/history").json()
    assert len(out["samples"]) == 4
    assert all(row["rates"] is None for row in out["samples"])
    assert out["samples"][1]["metrics"]["generation_tokens_total"] == 10.0


def test_unknown_node_404s_everywhere(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, _d = _wire(tmp_path)
    assert client.get("/fleet/nodes/nope/history").status_code == 404
    assert client.post("/fleet/nodes/nope/detect").status_code == 404
    assert client.post("/fleet/nodes/nope/verify", json={}).status_code == 404
    assert client.patch("/fleet/nodes/nope", json={"label": "x"}).status_code == 404
    assert client.delete("/fleet/nodes/nope").status_code == 404


# --- node CRUD ----------------------------------------------------------------


def test_node_crud_round_trip_persists_and_survives_reload(tmp_path, monkeypatch):
    counts = _stub_probes(monkeypatch, kind="ollama")
    client, d = _wire(tmp_path)

    r = client.post(
        "/fleet/nodes",
        json={"base_url": "http://100.87.42.62:8003", "label": "Tower"},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["node"]["kind"] == "ollama"
    assert out["node"]["source"] == "user"
    assert out["node"]["id"] == "tower"  # slugged from the label
    assert out["snapshot"]["status"] == "online"
    assert counts == {"detect": 1, "probe": 1}

    r = client.patch("/fleet/nodes/tower", json={"routable": True, "label": "Tower A"})
    assert r.status_code == 200
    assert r.json()["node"]["routable"] is True

    # A fresh process reads the node back off config.toml.
    saved = {n["id"]: n for n in _reload(d).fleet_nodes}
    assert saved["tower"]["routable"] is True
    assert saved["tower"]["label"] == "Tower A"
    assert saved["tower"]["base_url"] == "http://100.87.42.62:8003"

    assert client.delete("/fleet/nodes/tower").json() == {"ok": True}
    assert _reload(d).fleet_nodes == []


def test_patch_omits_untouched_fields(tmp_path, monkeypatch):
    """``None`` means "leave alone" — a UI that toggles one switch must not
    blank the rest of the record."""
    _stub_probes(monkeypatch)
    node = _Node(id="tower", label="Tower", base_url="http://x", default_model="qwen")
    client, _d = _wire(tmp_path, seed=[node])
    out = client.patch("/fleet/nodes/tower", json={"enabled": False}).json()["node"]
    assert out["enabled"] is False
    assert out["label"] == "Tower" and out["default_model"] == "qwen"


def test_add_node_validation(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, _d = _wire(tmp_path)
    assert client.post("/fleet/nodes", json={"base_url": "   "}).status_code == 400
    bad = client.post(
        "/fleet/nodes", json={"base_url": "http://x", "id": "not a valid id!"}
    )
    assert bad.status_code == 400
    assert "invalid node id" in bad.json()["detail"]  # the registry's own reason


def test_config_seeded_node_is_visible_but_not_deletable(tmp_path, monkeypatch):
    """The two endpoint slots are auto-seeded; deleting one here would just
    reappear on the next boot, so we point at where it actually lives."""
    _stub_probes(monkeypatch)
    seed = _Node(
        id="ollama", label="Ollama", base_url="http://127.0.0.1:11434", source="config"
    )
    client, _d = _wire(tmp_path, sampler=_Sampler(snaps=[_Snapshot(node=seed)]), seed=[seed])
    assert client.get("/fleet").json()["nodes"][0]["node"]["source"] == "config"
    r = client.delete("/fleet/nodes/ollama")
    assert r.status_code == 404
    assert "managed in Settings" in r.json()["detail"]
    assert "ollama_base_url" in r.json()["detail"]


def test_litellm_children_are_absorbed(tmp_path, monkeypatch):
    """A proxy names its own backends; the fleet shows the real topology."""
    child = _Node(id="brain", base_url="http://spark-049d:8000/v1", alias="brain")
    _stub_probes(monkeypatch, kind="litellm", children=[child])
    client, d = _wire(tmp_path)
    out = client.post("/fleet/nodes", json={"base_url": "http://100.66.161.52:4000"}).json()
    assert [c["id"] for c in out["children"]] == ["brain"]
    assert d.fleet.get("brain").parent_id == out["node"]["id"]


def test_detect_reruns_kind(tmp_path, monkeypatch):
    _stub_probes(monkeypatch, kind="vllm")
    client, _d = _wire(tmp_path, seed=[_Node(id="tower", base_url="http://x", kind="unknown")])
    out = client.post("/fleet/nodes/tower/detect").json()
    assert out["kind"] == "vllm"
    assert out["node"]["kind"] == "vllm"
    assert out["node"]["kind_detected_at"] > 0


# --- ad-hoc probe -------------------------------------------------------------


def test_probe_is_always_200_with_an_honest_error(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    from iron_jarvis.fleet import probes as fprobes

    def _boom(node, **_kw):
        raise OSError("[Errno 111] Connection refused")

    monkeypatch.setattr(fprobes, "probe_node", _boom, raising=False)
    client, d = _wire(tmp_path)
    r = client.post("/fleet/probe", json={"base_url": "http://spark-76a1:8001/v1"})
    assert r.status_code == 200
    out = r.json()
    assert out["snapshot"] is None
    assert "Connection refused" in out["error"]
    assert d.fleet.nodes() == []  # probe-only: nothing was saved
    assert client.post("/fleet/probe", json={"base_url": " "}).status_code == 400


def test_probe_returns_kind_and_snapshot(tmp_path, monkeypatch):
    _stub_probes(monkeypatch, kind="vllm")
    client, d = _wire(tmp_path)
    out = client.post("/fleet/probe", json={"base_url": "http://100.66.161.52:8888"}).json()
    assert out["kind"] == "vllm" and out["error"] == ""
    assert out["snapshot"]["status"] == "online"
    assert d.fleet.nodes() == []


# --- verify (live tool-capability check) --------------------------------------


def test_verify_records_tool_use_true(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, d = _wire(tmp_path, seed=[_Node(id="tower", base_url="http://x", routable=True)])
    adapter = _FakeAdapter(tool_calls=[{"name": "ping"}])
    d.platform.providers.get = lambda name, model=None: adapter

    out = client.post("/fleet/nodes/tower/verify", json={"model": "qwen3-coder"}).json()
    assert out["tool_use"] is True and out["error"] == ""
    assert out["node"]["tool_use"] is True and out["node"]["verified_at"] > 0
    # It really asked the server to call a tool.
    assert adapter.calls[0]["tools"][0]["name"] == "ping"


def test_verify_records_tool_use_false_when_the_server_ignores_tools(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, d = _wire(tmp_path, seed=[_Node(id="tower", base_url="http://x", routable=True)])
    d.platform.providers.get = lambda name, model=None: _FakeAdapter(tool_calls=[])
    out = client.post("/fleet/nodes/tower/verify", json={}).json()
    assert out["tool_use"] is False
    assert d.fleet.get("tower").tool_use is False


def test_verify_never_certifies_a_node_from_the_mock_adapter(tmp_path, monkeypatch):
    """The mock calls any tool it's offered. If a node's provider resolves to
    it, the honest answer is "we asked nothing" — not "tool use works"."""
    _stub_probes(monkeypatch)
    client, d = _wire(tmp_path, seed=[_Node(id="tower", base_url="http://x")])
    mock = _FakeAdapter(tool_calls=[{"name": "ping"}])
    mock.provider = "mock"
    d.platform.providers.get = lambda name, model=None: mock

    out = client.post("/fleet/nodes/tower/verify", json={}).json()
    assert out["tool_use"] is None
    assert "mock" in out["error"]
    assert mock.calls == []  # it never even asked
    assert d.fleet.get("tower").verified_at == 0.0


def test_verify_unreachable_node_is_null_not_false(tmp_path, monkeypatch):
    """An unreachable node is an UNKNOWN capability. Recording False here would
    permanently demote a box that was merely asleep."""
    _stub_probes(monkeypatch)
    client, d = _wire(tmp_path, seed=[_Node(id="spark", base_url="http://spark:8001")])

    def _boom(name, model=None):
        raise KeyError(f"unknown provider '{name}'")

    d.platform.providers.get = _boom
    r = client.post("/fleet/nodes/spark/verify", json={})
    assert r.status_code == 200  # not a 500 — the node just can't answer
    out = r.json()
    assert out["tool_use"] is None
    assert "unknown provider" in out["error"]
    # Nothing was written, and the hint is actionable (the node isn't routable).
    assert d.fleet.get("spark").tool_use is None
    assert d.fleet.get("spark").verified_at == 0.0
    assert "Routable" in out["hint"]


# --- usage / savings ----------------------------------------------------------


def _seed_runs(platform, rows) -> None:
    with session_scope(platform.engine) as db:
        for provider, model, itok, otok in rows:
            run = AgentRun(
                session_id="s1",
                provider=provider,
                model=model,
                input_tokens=itok,
                output_tokens=otok,
            )
            run.created_at = utcnow() - timedelta(hours=1)
            db.add(run)
        db.commit()


def test_usage_splits_local_from_cloud_and_names_its_baseline(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    node = _Node(id="tower", label="Tower", base_url="http://x")
    client, d = _wire(tmp_path, seed=[node])
    _seed_runs(
        d.platform,
        [
            ("fleet-tower", "qwen3-coder", 1_000_000, 1_000_000),  # local
            ("ollama", "llama3.1", 1_000_000, 0),                  # local (legacy slot)
            ("anthropic", "claude-opus-4-8", 1_000_000, 0),        # cloud, $5
            ("openai", "gpt-4o", 1_000_000, 0),                    # cloud, $2.5
        ],
    )
    out = client.get("/fleet/usage?days=30").json()

    assert out["local_tokens"] == 3_000_000
    assert out["cloud_tokens"] == 2_000_000
    assert out["cloud_cost_usd"] == 7.5
    # Local tokens re-priced on the baseline: (2M in * $5 + 1M out * $25) / 1M.
    assert out["comparison_provider"] == "anthropic"
    assert out["comparison_model"] == "claude-opus-4-8"
    assert out["est_avoided_usd"] == 35.0
    # The estimate always ships its basis — no bare savings claim is possible.
    assert "claude-opus-4-8" in out["basis"] and "estimate" in out["basis"]

    by_node = {e["node_id"]: e for e in out["by_node"]}
    assert by_node["tower"]["label"] == "Tower"
    assert by_node["tower"]["models"] == ["qwen3-coder"]
    assert by_node["tower"]["est_avoided_usd"] == 30.0
    assert by_node["ollama"]["est_avoided_usd"] == 5.0


def test_usage_baseline_is_configurable(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, d = _wire(tmp_path)
    d.platform.config.fleet_savings_baseline = "openai:gpt-4o"
    _seed_runs(d.platform, [("fleet-tower", "qwen3-coder", 1_000_000, 0)])
    out = client.get("/fleet/usage").json()
    assert out["comparison_provider"] == "openai"
    assert out["comparison_model"] == "gpt-4o"
    assert out["est_avoided_usd"] == 2.5

    # A baseline naming no model would price everything at $0 — that reads as
    # "you saved nothing" rather than "your baseline is misconfigured", so it
    # falls back to the built-in default instead.
    d.platform.config.fleet_savings_baseline = "anthropic"
    out = client.get("/fleet/usage").json()
    assert out["comparison_model"] == "claude-opus-4-8"
    assert out["est_avoided_usd"] == 5.0


def test_usage_with_no_runs_is_zero_but_still_names_the_baseline(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, _d = _wire(tmp_path)
    out = client.get("/fleet/usage").json()
    assert out["by_node"] == []
    assert out["local_tokens"] == 0 and out["est_avoided_usd"] == 0.0
    assert out["comparison_model"] == "claude-opus-4-8"


# --- code routing (Wave 1: reports honestly, wired in Wave 2) -----------------


def test_code_route_off_by_default(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, _d = _wire(tmp_path)
    out = client.get("/fleet/code-route").json()
    assert out["enabled"] is False and out["target"] == ""
    assert out["task_classes"]  # the built-in set
    assert out["effective"]["why"] == "code routing is off"
    assert out["effective"]["tool_use"] is None


def test_put_code_route_validates_target(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    client, _d = _wire(tmp_path, seed=[_Node(id="tower", base_url="http://x")])
    bad = client.put("/fleet/code-route", json={"target": "just-a-model"})
    assert bad.status_code == 400 and "provider:model" in bad.json()["detail"]
    # Parses, but the node isn't routable — rejected rather than silently saved.
    r = client.put("/fleet/code-route", json={"target": "fleet-tower:qwen3-coder"})
    assert r.status_code == 400 and "Routable" in r.json()["detail"]
    # And you can't switch it on with no target at all.
    assert client.put("/fleet/code-route", json={"enabled": True}).status_code == 400


def test_put_code_route_persists_and_explains_itself(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    node = _Node(id="tower", base_url="http://x", routable=True, tool_use=True)
    client, d = _wire(tmp_path, seed=[node])
    _register_provider(d, "fleet-tower")
    r = client.put(
        "/fleet/code-route",
        json={
            "enabled": True,
            "target": "fleet-tower:qwen3-coder",
            "task_classes": "builder, maintainer",
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out["enabled"] is True
    assert out["task_classes"] == ["builder", "maintainer"]
    assert out["effective"]["provider"] == "fleet-tower"
    assert out["effective"]["model"] == "qwen3-coder"
    assert "fleet-tower:qwen3-coder" in out["effective"]["why"]

    cfg = _reload(d)  # survives a restart
    assert cfg.fleet_code_route_enabled is True
    assert cfg.fleet_code_target == "fleet-tower:qwen3-coder"
    assert cfg.fleet_code_task_classes == "builder, maintainer"


def test_code_route_is_honest_about_an_unverified_node(tmp_path, monkeypatch):
    """tool_use unknown must not read as "ready" — the why says so out loud."""
    _stub_probes(monkeypatch)
    node = _Node(id="tower", base_url="http://x", routable=True, tool_use=None)
    client, d = _wire(tmp_path, seed=[node])
    client.put(
        "/fleet/code-route", json={"enabled": True, "target": "fleet-tower:qwen3"}
    )
    # Before the node is registered as a provider, the honest answer is that the
    # target isn't reachable — NOT that everything is fine.
    assert "not currently available" in (
        client.get("/fleet/code-route").json()["effective"]["why"]
    )

    _register_provider(d, "fleet-tower")
    why = client.get("/fleet/code-route").json()["effective"]["why"]
    assert "unverified" in why

    d.fleet.update("tower", tool_use=False)
    why = client.get("/fleet/code-route").json()["effective"]["why"]
    assert "can't call tools" in why
