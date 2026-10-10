"""A proxy's model cannot be edited into a second row; the sampler forgets by a
public method (v1.330.0, calm chat wave 12, N3).

The wave-11 closing audit, reproduced live on the scratch daemon against the
user's LiteLLM proxy: ``PATCH /fleet/nodes/<proxy>-glm {"label": "My GLM"}``
answered 200 and ``FleetRegistry.update`` stored the model as a top-level row,
so ``GET /fleet`` listed it twice (``('px-glm', 'My GLM')`` beside
``('px-glm', 'glm')``) and, with the proxy switched off, the saved copy was the
only row left. ``POST .../detect`` on a model did the same. The page no longer
offers rename on a proxy's model and its comment said the daemon refuses it,
but the daemon only refused remove.

Now:

* ``update`` on a proxy's model raises ``ChildNodeError`` with the remove
  route's sentence and stores nothing;
* ``PATCH`` / ``detect`` / ``verify`` on one answer 409 with that sentence,
  BEFORE any probe or model call;
* a copy an older build saved is not listed beside the proxy's own report, nor
  after the proxy is switched off; one whose proxy is gone stays listed so it
  can still be removed;
* ``FleetSampler.forget(node_ids)`` drops the reading, history and backoff under
  the sampler's lock, and the delete route's ``_forget_readings`` calls it with
  no private field access left.

The route tests drive the REAL app factory; only the network calls in
``fleet.probes`` are stood in for.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import fleet as fleet_routes
from iron_jarvis.fleet import probes as fprobes
from iron_jarvis.fleet.models import FleetNode, NodeMetrics, NodeSnapshot
from iron_jarvis.fleet.registry import ChildNodeError, FleetRegistry
from iron_jarvis.fleet.sampler import FleetSampler

_ALIASES = ("fleet", "glm", "fleet-think")
_SENTENCE = "glm comes from the proxy Spark proxy. Remove the proxy to remove it."


def _config(tmp_path, **kw):
    cfg = Config(home=tmp_path / ".ironjarvis", project_root=tmp_path, **kw)
    cfg.ensure_dirs()
    return cfg


def _kids(parent: str) -> list[FleetNode]:
    return [
        FleetNode(
            id=f"{parent}-{alias}",
            parent_id=parent,
            alias=alias,
            label=alias,
            source="topology",
            base_url="http://spark-049d:8888",
        )
        for alias in _ALIASES
    ]


def _registry(tmp_path) -> tuple[FleetRegistry, Config]:
    cfg = _config(tmp_path)
    reg = FleetRegistry(cfg)
    reg.add(FleetNode(id="spark", label="Spark proxy", base_url="http://proxy:4000", kind="litellm"))
    reg.absorb_children("spark", _kids("spark"))
    return reg, cfg


def _pairs(nodes) -> list[tuple[str, str]]:
    return [(n.id, n.label) for n in nodes]


# --- the registry ------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"label": "My GLM"},
        {"kind": "vllm", "kind_detected_at": 1.0},
        {"tool_use": True, "verified_at": 1.0},
        {"vision": False, "verified_at": 1.0},
    ],
)
def test_editing_a_proxys_model_is_refused_and_stores_nothing(tmp_path, fields):
    reg, cfg = _registry(tmp_path)
    before = list(cfg.fleet_nodes)

    with pytest.raises(ChildNodeError) as err:
        reg.update("spark-glm", **fields)

    assert str(err.value) == _SENTENCE
    assert cfg.fleet_nodes == before, "a refused edit must not save a row"
    ids = [n.id for n in reg.nodes()]
    assert ids.count("spark-glm") == 1, f"the model is listed {ids.count('spark-glm')} times"
    assert reg.get("spark-glm").label == "glm"


def test_child_refusal_is_the_one_sentence_and_empty_for_everything_else(tmp_path):
    reg, _cfg = _registry(tmp_path)
    assert reg.child_refusal(reg.get("spark-glm")) == _SENTENCE
    assert reg.child_refusal(reg.get("spark")) == ""
    assert reg.child_refusal(None) == ""
    # A proxy's own edits still work.
    assert reg.update("spark", label="Sparks").label == "Sparks"
    assert reg.child_refusal(reg.get("spark-glm")) == (
        "glm comes from the proxy Sparks. Remove the proxy to remove it."
    )


def _seed_legacy_copy(cfg: Config) -> None:
    """What an older build saved when a proxy's model was renamed."""
    copy = FleetNode(
        id="spark-glm", parent_id="spark", alias="glm", label="My GLM",
        source="topology", base_url="http://spark-049d:8888",
    )
    cfg.fleet_nodes = list(cfg.fleet_nodes) + [
        {k: v for k, v in copy.model_dump().items() if v is not None}
    ]


def test_an_older_saved_copy_is_not_listed_beside_the_proxys_report(tmp_path):
    reg, cfg = _registry(tmp_path)
    _seed_legacy_copy(cfg)

    pairs = _pairs(reg.nodes())
    assert [p for p in pairs if p[0] == "spark-glm"] == [("spark-glm", "glm")], pairs

    reg.update("spark", enabled=False)
    assert [n.id for n in reg.nodes()] == ["spark"], "a switched-off proxy's models are not shown"

    # The proxy's own removal takes the saved copy off disk too.
    reg.update("spark", enabled=True)
    reg.remove("spark")
    assert reg.nodes() == []
    assert cfg.fleet_nodes == []


def test_an_older_saved_copy_whose_proxy_is_gone_stays_listed_and_removable(tmp_path):
    cfg = _config(tmp_path)
    reg = FleetRegistry(cfg)
    _seed_legacy_copy(cfg)  # its proxy "spark" is not a node

    assert _pairs(reg.nodes()) == [("spark-glm", "My GLM")]
    assert reg.child_refusal(reg.get("spark-glm")) == ""
    assert reg.remove("spark-glm") == []
    assert reg.nodes() == [] and cfg.fleet_nodes == []


# --- the sampler -------------------------------------------------------------


class _Reg:
    def __init__(self, *nodes):
        self._nodes = list(nodes)

    def nodes(self):
        return list(self._nodes)

    def set_reachable(self, *_a):
        pass

    def absorb_children(self, *_a):
        pass


class _CountingLock:
    """An RLock that counts how often it is entered."""

    def __init__(self):
        self._lock = threading.RLock()
        self.entered = 0

    def __enter__(self):
        self._lock.acquire()
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


def test_forget_drops_reading_history_and_backoff_under_the_lock():
    a = FleetNode(id="a", base_url="http://a:1")
    b = FleetNode(id="b", base_url="http://b:1")
    t = [100.0]
    sampler = FleetSampler(_Reg(a, b), clock=lambda: t[0])
    for n in (a, b):
        for _ in range(2):
            t[0] += 2.0
            sampler.record(n, NodeSnapshot(node=n, status="online", metrics=NodeMetrics(requests_running=1)))
    for _ in range(4):  # past the failure threshold: a backoff is armed for "a"
        sampler._record_failure(a, "refused")  # noqa: SLF001 — arming the backoff
    assert sampler._state["a"].failure.next_at > t[0]  # noqa: SLF001
    assert sampler.series("a") and sampler.latest("a") is not None

    lock = _CountingLock()
    sampler._lock = lock  # noqa: SLF001 — counting the lock IS the test
    assert sampler.forget(["a", "missing"]) == 1

    assert lock.entered >= 1, "forget must hold the sampler's lock"
    assert sampler.latest("a") is None
    assert sampler.series("a") == []
    assert "a" not in sampler._state, "the backoff must go with the reading"  # noqa: SLF001
    # Only what was asked for is forgotten.
    assert sampler.latest("b") is not None and len(sampler.series("b")) == 2
    # Listed again as "not checked yet", never as the old reading.
    listing = {snap.node.id: checked for snap, checked in sampler.listing()}
    assert listing == {"a": False, "b": True}


def test_the_route_helper_reads_no_private_sampler_field():
    src = Path(fleet_routes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_forget_readings"
    )
    private = sorted(
        {
            (n.value if isinstance(n, ast.Constant) else n.attr)
            for n in ast.walk(fn)
            if (isinstance(n, ast.Attribute) and n.attr.startswith("_"))
            or (isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("_"))
        }
    )
    assert private == [], f"_forget_readings reaches into the sampler: {private}"

    calls: list[list[str]] = []

    class _S:
        def forget(self, ids):
            calls.append(list(ids))
            return len(ids)

    fleet_routes._forget_readings(_S(), ["x", "y"])
    assert calls == [["x", "y"]]


# --- the routes, through the real app --------------------------------------------


def _stub_network(monkeypatch):
    calls = {"detect": 0, "probe": 0}

    def _detect(base_url, **_kw):
        calls["detect"] += 1
        return ("litellm", "stubbed")

    def _probe(node, **_kw):
        calls["probe"] += 1
        kids = _kids(node.id) if not node.parent_id else []
        return (NodeSnapshot(node=node, status="online", evidence="direct", latency_ms=9.0), kids)

    monkeypatch.setattr(fprobes, "detect_kind", _detect)
    monkeypatch.setattr(fprobes, "probe_node", _probe)
    return calls


def _pairs_from(client) -> list[tuple[str, str]]:
    return [(r["node"]["id"], r["node"]["label"]) for r in client.get("/fleet").json()["nodes"]]


@pytest.fixture
def live(tmp_path, monkeypatch):
    calls = _stub_network(monkeypatch)
    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.post("/fleet/nodes", json={"id": "spark", "label": "Spark proxy", "base_url": "http://proxy:4000"})
    assert r.status_code == 200, r.text
    calls["detect"] = 0
    return app, client, calls


def test_patch_on_a_proxys_model_is_a_409_and_lists_it_once(live):
    app, client, _calls = live
    cfg = app.state.d.platform.config
    before = list(cfg.fleet_nodes)

    r = client.patch("/fleet/nodes/spark-glm", json={"label": "My GLM"})

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == _SENTENCE
    pairs = _pairs_from(client)
    assert [p for p in pairs if p[0] == "spark-glm"] == [("spark-glm", "glm")], pairs
    assert cfg.fleet_nodes == before, "a refused edit must not be saved"

    # Switching the proxy off leaves no copy of its model behind.
    assert client.patch("/fleet/nodes/spark", json={"enabled": False}).status_code == 200
    assert "spark-glm" not in [p[0] for p in _pairs_from(client)]


def test_detect_on_a_proxys_model_is_a_409_before_anything_is_probed(live):
    app, client, calls = live
    before = list(app.state.d.platform.config.fleet_nodes)

    r = client.post("/fleet/nodes/spark-fleet/detect")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "fleet comes from the proxy Spark proxy. Remove the proxy to remove it."
    assert calls["detect"] == 0, "nothing may be probed for a refused node"
    assert app.state.d.platform.config.fleet_nodes == before
    assert [p[0] for p in _pairs_from(client)].count("spark-fleet") == 1


def test_verify_on_a_proxys_model_is_a_409_before_any_model_is_asked(live, monkeypatch):
    app, client, _calls = live
    asked: list[str] = []
    providers = app.state.d.platform.providers
    monkeypatch.setattr(providers, "get", lambda name, *a, **k: asked.append(name))
    before = list(app.state.d.platform.config.fleet_nodes)

    r = client.post("/fleet/nodes/spark-fleet-think/verify", json={})

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "fleet-think comes from the proxy Spark proxy. Remove the proxy to remove it."
    )
    assert asked == [], "no adapter may be built for a refused node"
    assert app.state.d.platform.config.fleet_nodes == before


def test_the_proxy_itself_is_still_edited_detected_and_verified(live):
    _app, client, calls = live
    r = client.patch("/fleet/nodes/spark", json={"label": "Sparks"})
    assert r.status_code == 200 and r.json()["node"]["label"] == "Sparks"
    r = client.post("/fleet/nodes/spark/detect")
    assert r.status_code == 200 and calls["detect"] == 1
    r = client.post("/fleet/nodes/spark/verify", json={})
    assert r.status_code == 200, r.text  # an honest unknown, never a refusal
    assert ("spark", "Sparks") in _pairs_from(client)


def test_the_delete_route_forgets_through_the_samplers_public_method(live, monkeypatch):
    app, client, _calls = live
    sampler = app.state.d.fleet_sampler
    seen: list[list[str]] = []
    real = sampler.forget

    def _spy(ids):
        seen.append(sorted(ids))
        return real(ids)

    monkeypatch.setattr(sampler, "forget", _spy)
    assert sampler.latest("spark") is not None

    assert client.delete("/fleet/nodes/spark").status_code == 200

    assert seen == [sorted(["spark", "spark-fleet", "spark-glm", "spark-fleet-think"])]
    assert sampler.latest("spark") is None
    assert _pairs_from(client) == []
