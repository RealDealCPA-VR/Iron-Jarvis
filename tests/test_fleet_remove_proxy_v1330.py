"""Removing a proxy from Fleet removes its models too; Remove on a model the
proxy reports tells the truth (v1.330.0, calm chat wave 11, M1).

The wave-10 closing audit, reproduced live on the scratch daemon against a real
LiteLLM proxy: after ``DELETE /fleet/nodes/<proxy>`` every model the proxy had
reported (``<id>-glm``, ``<id>-fleet-think`` ...) stayed on ``GET /fleet`` and on
the Fleet page, Online, each with a Remove button, until the daemon restarted.
``FleetRegistry.remove`` never dropped ``_children[<proxy>]``. And Remove on one
of those rows answered ``{"ok": true}`` while nothing changed: a child is never
a stored row, and the proxy names it again on the next sampling pass anyway.

Now:

* removing a proxy drops its children from the registry (the list and the
  reachability cache), drops any child row an edit once saved, and the delete
  route clears the sampler's readings for all of them;
* a sampling pass that probed the proxy just before the removal and lands after
  it does not bring the children back (``absorb_children`` ignores a parent
  that is no longer a node);
* a proxy's children are listed only while the proxy is listed and switched on;
* removing a child is REFUSED (409) in one plain sentence that says what works;
* the Fleet copy the probes write has no dash asides.

The route tests drive the REAL app factory; only the two network calls in
``fleet.probes`` are stood in for.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet import probes as fprobes
from iron_jarvis.fleet.models import FleetNode, NodeSnapshot
from iron_jarvis.fleet.registry import ChildNodeError, FleetRegistry

_DASH = chr(0x2014)
_ALIASES = ("fleet", "glm", "fleet-think")


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
            source="topology",
            base_url="http://spark-049d:8888",
        )
        for alias in _ALIASES
    ]


def _registry_with_proxy(tmp_path) -> tuple[FleetRegistry, Config]:
    cfg = _config(tmp_path)
    reg = FleetRegistry(cfg)
    reg.add(FleetNode(id="spark", label="Spark proxy", base_url="http://proxy:4000", kind="litellm"))
    reg.absorb_children("spark", _kids("spark"))
    for kid in reg.children_of("spark"):
        reg.set_reachable(kid.id, True)
    reg.set_reachable("spark", True)
    return reg, cfg


# --- the registry ---------------------------------------------------------------


def test_removing_a_proxy_removes_its_children_everywhere_the_registry_holds_them(tmp_path):
    reg, _cfg = _registry_with_proxy(tmp_path)
    assert {n.id for n in reg.nodes()} == {"spark", "spark-fleet", "spark-glm", "spark-fleet-think"}

    assert reg.remove("spark") == []

    assert reg.nodes() == [], "the proxy's models must leave with it"
    assert reg.children_of("spark") == []
    assert reg.get("spark-glm") is None
    # The reachability cache is cleared too, not just hidden behind get().
    assert "spark-glm" not in reg._reachable and "spark" not in reg._reachable


def test_a_late_sampling_result_for_a_removed_proxy_does_not_bring_its_children_back(tmp_path):
    reg, _cfg = _registry_with_proxy(tmp_path)
    reg.remove("spark")
    # The sampler probed the proxy just before the removal; its result lands now.
    reg.absorb_children("spark", _kids("spark"))
    assert reg.nodes() == []
    assert reg.children_of("spark") == []


def test_removing_a_child_is_refused_in_plain_words_and_changes_nothing(tmp_path):
    reg, cfg = _registry_with_proxy(tmp_path)
    before = list(cfg.fleet_nodes)

    with pytest.raises(ChildNodeError) as err:
        reg.remove("spark-glm")

    msg = str(err.value)
    assert msg == "glm comes from the proxy Spark proxy. Remove the proxy to remove it."
    assert _DASH not in msg
    assert reg.get("spark-glm") is not None, "a refused removal removes nothing"
    assert cfg.fleet_nodes == before


def test_a_child_row_an_edit_saved_goes_with_its_proxy(tmp_path):
    reg, cfg = _registry_with_proxy(tmp_path)
    # Any PATCH on a child (rename, detect) promotes it to a stored row.
    reg.update("spark-glm", label="GLM on the Sparks")
    assert "spark-glm" in [row["id"] for row in cfg.fleet_nodes]
    assert set(reg.family("spark")) == {"spark", "spark-fleet", "spark-glm", "spark-fleet-think"}

    reg.remove("spark")

    assert reg.nodes() == []
    assert cfg.fleet_nodes == [], "a saved child row must not outlive its proxy on disk"


def test_children_are_listed_only_while_their_proxy_is_listed_and_on(tmp_path):
    reg, _cfg = _registry_with_proxy(tmp_path)
    reg.update("spark", enabled=False)
    assert [n.id for n in reg.nodes()] == ["spark"], "a switched-off proxy's models are not shown"
    reg.update("spark", enabled=True)
    assert len(reg.nodes()) == 4


def test_children_of_a_config_seeded_proxy_go_when_the_seed_is_removed(tmp_path):
    cfg = _config(tmp_path, custom_base_url="http://proxy:4000")
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    reg.absorb_children("custom", _kids("custom"))
    assert len(reg.nodes()) == 4

    assert reg.remove("custom") == ["custom_base_url"]
    assert reg.nodes() == []


# --- the delete route, through the real app ---------------------------------------


def _stub_network(monkeypatch):
    def _detect(base_url, **_kw):
        return ("litellm", "stubbed")

    def _probe(node, **_kw):
        kids = _kids(node.id) if not node.parent_id else []
        return (
            NodeSnapshot(node=node, status="online", evidence="direct", latency_ms=9.0),
            kids,
        )

    monkeypatch.setattr(fprobes, "detect_kind", _detect)
    monkeypatch.setattr(fprobes, "probe_node", _probe)
    return _probe


def _ids(client) -> set[str]:
    return {row["node"]["id"] for row in client.get("/fleet").json()["nodes"]}


def test_delete_route_removes_the_proxy_and_its_models_and_their_readings(tmp_path, monkeypatch):
    probe = _stub_network(monkeypatch)
    app = create_app(str(tmp_path))
    client = TestClient(app)
    d = app.state.d

    r = client.post(
        "/fleet/nodes",
        json={"id": "spark", "label": "Spark proxy", "base_url": "http://proxy:4000",
              "protocol": "anthropic", "default_model": "glm"},
    )
    assert r.status_code == 200, r.text
    assert _ids(client) == {"spark", "spark-fleet", "spark-glm", "spark-fleet-think"}
    # A reading for a child too, as a sampling pass leaves.
    kid = d.fleet.get("spark-glm")
    d.fleet_sampler.record(kid, NodeSnapshot(node=kid, status="online", evidence="direct"), [])
    assert d.fleet_sampler.latest("spark-glm") is not None

    r = client.delete("/fleet/nodes/spark")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "cleared_settings": []}

    assert _ids(client) == set(), "GET /fleet must be clean after removing the proxy"
    assert d.fleet_sampler.latest("spark") is None
    assert d.fleet_sampler.latest("spark-glm") is None

    # The sampler's in-flight pass for the proxy lands after the removal.
    parent = FleetNode(id="spark", base_url="http://proxy:4000", kind="litellm")
    snap, kids = probe(parent)
    d.fleet_sampler.record(parent, snap, kids)
    assert _ids(client) == set(), "a late reading must not put the removed proxy's models back"


def test_delete_route_refuses_a_child_with_a_409_and_keeps_it(tmp_path, monkeypatch):
    _stub_network(monkeypatch)
    app = create_app(str(tmp_path))
    client = TestClient(app)
    client.post("/fleet/nodes", json={"id": "spark", "label": "Spark proxy", "base_url": "http://proxy:4000"})

    r = client.delete("/fleet/nodes/spark-glm")

    assert r.status_code == 409, "never answer ok for a removal that did not happen"
    assert r.json()["detail"] == "glm comes from the proxy Spark proxy. Remove the proxy to remove it."
    assert "spark-glm" in _ids(client)
    assert app.state.d.fleet_sampler.latest("spark") is not None, "a refusal clears nothing"

    client.delete("/fleet/nodes/spark")
    assert _ids(client) == set()


# --- the copy the probes write ----------------------------------------------------


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def test_no_string_the_probes_write_has_a_dash_aside():
    src = Path(fprobes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    skip = _docstring_nodes(tree)
    found = [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in skip
        and _DASH in n.value
    ]
    assert found == [], f"user-visible probe copy with a dash aside: {found}"


def test_the_three_fleet_row_notes_read_as_plain_sentences():
    remote = FleetNode(id="p-frontier", parent_id="p", alias="frontier", source="topology")
    snap, _ = fprobes.probe_node(remote, get=lambda *a, **k: None)
    assert snap.metrics_reason == "It runs at a remote provider, so there is nothing local to measure."
    assert snap.hint["text"] == (
        "This model runs at a remote provider and is reached through the proxy. "
        "There is nothing local to probe."
    )

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "glm"}]}

    fresh = FleetNode(id="p-glm", parent_id="p", alias="glm", source="topology",
                      base_url="http://spark-049d:8888")
    snap, _ = fprobes.probe_node(fresh, get=lambda *a, **k: _Resp())
    assert snap.metrics_reason == "The server type is not known yet, so only a basic check runs."

    def _down(*_a, **_k):
        raise ConnectionError("connection refused")

    snap, _ = fprobes.probe_node(fresh, get=_down)
    assert snap.metrics_reason == "Not read, because the node could not be reached."
    assert _DASH not in snap.hint["text"]
