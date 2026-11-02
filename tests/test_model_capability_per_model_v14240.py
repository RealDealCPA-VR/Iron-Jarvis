"""v1.424.0 (D8): tools and vision are judged PER MODEL, never per gateway.

A gateway (a LiteLLM proxy, an Ollama box) serves several models, and they do
not all call tools or see pictures. Before v1.424.0 a node had ONE answer: a
check of its default model (``POST /fleet/nodes/{id}/verify`` wrote the node's
``tool_use`` / ``vision``) and every model behind it wore it — the Connections
row said "tools ✓" for a model nobody had asked, a tool turn ran natively on a
model that cannot call tools and stalled, a picture went to a model that cannot
see. Pinned here:

* ``fleet/adapter.model_capability``'s order: a live check of THIS model, the
  server's "yes", the node's record for its DEFAULT model only, the server's
  "no", else not known — and ``node_capabilities`` (what the router reads)
  reads not known as False;
* the check saves per model (``FleetRegistry.record_check``) and touches the
  node's own flags only for its default model; an empty map is never written
  and an older stored row loads unchanged;
* ``POST /fleet/nodes/{id}/verify`` records the model it asked;
* ``GET /fleet`` rows say each model's own answer (``model_capabilities``, and
  every ``models[]`` entry), a proxy's model by its proxy;
* ``GET /models`` endpoint rows carry ``tool_use`` / ``vision`` per model
  (before ``reasoning_default``, which stays LAST);
* the coding-route view reads the TARGET model's answer.
"""

from __future__ import annotations

import time
import types

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet.adapter import (
    FleetAdapter,
    is_default_model,
    model_capability,
    node_capabilities,
)
from iron_jarvis.fleet.models import FleetNode, ModelEntry, NodeSnapshot
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.fleet.upstreams import parse_model_info


def _table(stated: dict[str, dict]):
    """A LiteLLM ``/model/info`` table stating *stated* per served name."""
    body = {
        "data": [
            {
                "model_name": name,
                "litellm_params": {"model": f"hosted_vllm/{name}", "api_base": "http://10.0.0.5:8000/v1"},
                "model_info": dict(info),
            }
            for name, info in stated.items()
        ]
    }
    return parse_model_info(body)


STATED = {
    "seer": {"supports_vision": True, "supports_function_calling": True},
    "blind": {"supports_vision": False, "supports_function_calling": False},
    "quiet": {},
}


def _registry(tmp_path, nodes=(), saved=None):
    cfg = types.SimpleNamespace(
        home=str(tmp_path), fleet_nodes=[n.model_dump() for n in nodes],
        custom_base_url="", custom_model="", ollama_base_url="",
    )
    reg = FleetRegistry(cfg, persist=lambda home, doc: (saved.append(doc) if saved is not None else None))
    return reg, cfg


# --------------------------------------------------------------------------- #
# 1. the order
# --------------------------------------------------------------------------- #


def test_each_model_says_its_own_answer_in_the_documented_order():
    caps = _table(STATED).caps
    gw = FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="quiet",
                   tool_use=True, vision=True)
    # 3. the node's record speaks for its DEFAULT model only…
    assert model_capability(gw, "quiet", caps) == {
        "tool_use": True, "tool_use_from": "checked", "vision": True, "vision_from": "checked",
    }
    # …never for another model behind the same gateway (the D8 bug).
    other = FleetNode(**{**gw.model_dump(), "default_model": "something-else"})
    assert model_capability(other, "quiet", caps) == {
        "tool_use": None, "tool_use_from": "", "vision": None, "vision_from": "",
    }
    # 4. the server's "no" counts for a model nothing else speaks for.
    assert model_capability(other, "blind", caps)["vision"] is False
    assert model_capability(other, "blind", caps)["vision_from"] == "stated"
    # 2. the server's "yes".
    assert model_capability(other, "seer", caps)["tool_use"] is True
    assert model_capability(other, "seer", caps)["tool_use_from"] == "stated"
    # 1. a live check of THIS model outranks what the server says.
    checked = FleetNode(**{**other.model_dump(), "model_caps": {"seer": {"vision": False, "verified_at": 1.0}}})
    assert model_capability(checked, "seer", caps)["vision"] is False
    assert model_capability(checked, "seer", caps)["vision_from"] == "checked"
    assert model_capability(checked, "seer", caps)["tool_use"] is True  # unchecked key: the server's yes
    # The default model's own record outranks the server's "no".
    blind_default = FleetNode(**{**gw.model_dump(), "default_model": "blind"})
    assert model_capability(blind_default, "blind", caps)["vision"] is True
    # A table that faults states nothing.
    assert model_capability(other, "seer", lambda m: 1 / 0)["tool_use"] is None


def test_the_router_reads_not_known_as_not_able():
    caps = _table(STATED).caps
    gw = FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="quiet",
                   tool_use=True, vision=True)
    assert node_capabilities(gw, "unlisted", caps) == {"tool_use": False, "vision": False}
    assert node_capabilities(gw, "quiet", caps) == {"tool_use": True, "vision": True}
    assert FleetAdapter(node=gw, model="unlisted", caps=caps).capabilities()["tool_use"] is False
    assert FleetAdapter(node=gw, model="quiet", caps=caps).capabilities()["tool_use"] is True


def test_the_default_model_is_the_named_one_or_the_adapters_own_word():
    node = FleetNode(id="n", base_url="http://x", default_model="glm")
    assert is_default_model(node, "glm") and is_default_model(node, "") and is_default_model(node, "default")
    assert not is_default_model(node, "qwen")
    bare = FleetNode(id="n", base_url="http://x")
    assert is_default_model(bare, "default") and not is_default_model(bare, "glm")


# --------------------------------------------------------------------------- #
# 2. saving a check
# --------------------------------------------------------------------------- #


def test_a_check_saves_per_model_and_touches_the_node_flags_for_its_default_only(tmp_path):
    saved: list = []
    node = FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="glm", routable=True)
    reg, _cfg = _registry(tmp_path, [node], saved)
    reg.record_check("gw", "qwen", tool_use=False, vision=True, at=10.0)
    got = reg.get("gw")
    assert got.model_caps["qwen"] == {"tool_use": False, "vision": True, "verified_at": 10.0}
    assert got.tool_use is None and got.vision is None, "another model never speaks for the gateway"
    reg.record_check("gw", "glm", tool_use=True, at=11.0)
    got = reg.get("gw")
    assert got.tool_use is True and got.vision is None
    assert got.model_caps["glm"] == {"tool_use": True, "verified_at": 11.0}
    # A later check of one flag keeps the other.
    reg.record_check("gw", "qwen", vision=False, at=12.0)
    assert reg.get("gw").model_caps["qwen"] == {"tool_use": False, "vision": False, "verified_at": 12.0}
    # Persisted through the config writer (TOML has no null: none is sent).
    last = saved[-1]["fleet_nodes"][-1]
    assert last["model_caps"]["qwen"]["vision"] is False
    assert all(v is not None for v in last.values())


def test_an_empty_map_is_never_written_and_an_older_row_loads(tmp_path):
    saved: list = []
    reg, cfg = _registry(tmp_path, [], saved)
    reg.add(FleetNode(id="box", base_url="http://10.0.0.3:11434", routable=True))
    assert "model_caps" not in saved[-1]["fleet_nodes"][0]
    # A row saved before v1.424.0 (no model_caps key) loads with an empty map.
    cfg.fleet_nodes = [{"id": "old", "base_url": "http://10.0.0.4:8000/v1", "tool_use": True}]
    old = reg.get("old")
    assert old is not None and old.model_caps == {} and old.tool_use is True


def test_the_per_model_map_is_bounded(tmp_path):
    reg, _cfg = _registry(tmp_path, [FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1")])
    for i in range(FleetRegistry.MAX_MODEL_CHECKS + 5):
        reg.record_check("gw", f"m{i}", tool_use=True, at=float(i))
    caps = reg.get("gw").model_caps
    assert len(caps) == FleetRegistry.MAX_MODEL_CHECKS
    assert "m0" not in caps and f"m{FleetRegistry.MAX_MODEL_CHECKS + 4}" in caps


def test_the_registry_answers_per_provider_and_model(tmp_path):
    reg, _cfg = _registry(tmp_path, [
        FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="quiet", tool_use=True, routable=True),
    ])
    reg.set_routes("gw", _table(STATED))
    assert reg.model_capability("fleet-gw", "quiet")["tool_use"] is True
    assert reg.model_capability("fleet-gw", "blind")["tool_use"] is False
    assert reg.model_capability("fleet-gw", "unlisted")["tool_use"] is None
    assert reg.model_capability("openai", "gpt")["tool_use"] is None


# --------------------------------------------------------------------------- #
# 3. the routes
# --------------------------------------------------------------------------- #


class _Answers:
    """A node's adapter for the live check: calls the ping tool and names the
    colour red (it can do both)."""

    provider = "fleet-gw"

    def __init__(self, model):
        self.model = model

    async def complete(self, *, system, messages, tools, **kw):
        return types.SimpleNamespace(tool_calls=[{"name": "ping"}] if tools else [], text="red")


@pytest.fixture
def app_with_gateway(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    d = app.state.d
    d.fleet.add(FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="glm", routable=True))
    real_get = d.platform.providers.get

    def get(name="", model=None, *a, **k):
        if name == "fleet-gw":
            return _Answers(model or "glm")
        return real_get(name, model, *a, **k)

    monkeypatch.setattr(d.platform.providers, "get", get)
    return app, d


def test_verify_records_the_model_it_asked(app_with_gateway):
    app, d = app_with_gateway
    client = TestClient(app)
    r = client.post("/fleet/nodes/gw/verify", json={"model": "qwen"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "qwen" and r.json()["tool_use"] is True
    node = d.fleet.get("gw")
    assert node.model_caps["qwen"]["tool_use"] is True and node.model_caps["qwen"]["vision"] is True
    assert node.tool_use is None and node.vision is None, "qwen's check never speaks for glm"
    r = client.post("/fleet/nodes/gw/verify", json={})
    assert r.json()["model"] == "glm"
    assert d.fleet.get("gw").tool_use is True


def test_the_fleet_rows_say_each_models_own_answer(app_with_gateway, monkeypatch):
    app, d = app_with_gateway
    d.fleet.record_check("gw", "qwen", tool_use=False, at=time.time())
    d.fleet.record_check("gw", "glm", tool_use=True, vision=True, at=time.time())
    node = d.fleet.get("gw")
    snap = NodeSnapshot(node=node, status="online",
                        models=[ModelEntry(id="glm"), ModelEntry(id="qwen"), ModelEntry(id="new")])
    monkeypatch.setattr(d.fleet_sampler, "listing", lambda: [(snap, True)])
    rows = TestClient(app).get("/fleet").json()["nodes"]
    row = rows[0]
    caps = row["model_capabilities"]
    assert caps["glm"]["tool_use"] is True and caps["glm"]["tool_use_from"] == "checked"
    assert caps["qwen"]["tool_use"] is False
    assert caps["new"]["tool_use"] is None and caps["new"]["tool_use_from"] == ""
    by_id = {m["id"]: m for m in row["models"]}
    assert by_id["qwen"]["tool_use"] is False and by_id["new"]["tool_use"] is None
    assert by_id["glm"]["vision"] is True


def test_a_node_with_no_model_named_answers_its_default_by_the_empty_name(tmp_path, monkeypatch):
    """The Connections row of a node that saved no model reads its default
    model's answer under ``""``: the node's own record (a check made with no
    model named) still shows, never "not known"."""
    from iron_jarvis.daemon.routes import fleet as F

    reg, _cfg = _registry(tmp_path, [FleetNode(id="box", base_url="http://10.0.0.3:11434", tool_use=True)])
    node = reg.get("box")
    snap = NodeSnapshot(node=node, status="online", models=[ModelEntry(id="llama3")])
    row = F._with_capabilities(snap.model_dump(), snap, reg)
    assert row["model_capabilities"][""]["tool_use"] is True
    assert row["model_capabilities"][""]["tool_use_from"] == "checked"
    assert row["model_capabilities"]["llama3"]["tool_use"] is None  # another name: not known


def test_a_proxys_model_is_answered_by_its_proxy(tmp_path):
    reg, _cfg = _registry(tmp_path, [
        FleetNode(id="gw", base_url="http://10.0.0.2:4000/v1", default_model="quiet", routable=True),
    ])
    reg.set_routes("gw", _table(STATED))
    reg.record_check("gw", "blind", tool_use=True, at=1.0)
    child = FleetNode(id="gw-blind", base_url="http://10.0.0.5:8000/v1", parent_id="gw",
                      alias="blind", source="topology", tool_use=False)
    rows = reg.capability_rows(child, ["blind"])
    assert rows["blind"]["tool_use"] is True and rows["blind"]["tool_use_from"] == "checked"
    assert rows["blind"]["vision"] is False and rows["blind"]["vision_from"] == "stated"


def test_the_models_rows_carry_each_models_answer_before_reasoning_default(app_with_gateway, monkeypatch):
    app, d = app_with_gateway
    from iron_jarvis.daemon.routes import connections as C
    from iron_jarvis.providers import discovery

    d.fleet.record_check("gw", "qwen", tool_use=False, vision=False, at=time.time())
    d.fleet.record_check("gw", "glm", tool_use=True, at=time.time())
    monkeypatch.setattr(discovery, "discover_models",
                        lambda prov, cred, **kw: ["glm", "qwen", "new"] if prov == "fleet-gw" else [])
    rows = [m for m in C.build_selectable_models(d) if m["provider"] == "fleet-gw"]
    by = {m["model"]: m for m in rows}
    assert by["glm"]["tool_use"] is True and by["glm"]["vision"] is None
    assert by["qwen"]["tool_use"] is False and by["qwen"]["vision"] is False
    assert by["new"]["tool_use"] is None
    assert all(list(m)[-1] == "reasoning_default" for m in rows)
    # A cloud API row is not an endpoint: no per-model keys are invented.
    others = [m for m in C.build_selectable_models(d) if m["provider"] == "openai"]
    assert all("tool_use" not in m for m in others)


def test_the_coding_route_reads_the_target_models_answer(app_with_gateway):
    app, d = app_with_gateway
    d.platform.config.fleet_code_route_enabled = True
    d.platform.config.fleet_code_target = "fleet-gw:qwen"
    d.fleet.record_check("gw", "glm", tool_use=True, at=time.time())
    d.fleet.record_check("gw", "qwen", tool_use=False, at=time.time())
    d.fleet.set_reachable("gw", True)
    view = TestClient(app).get("/fleet/code-route").json()
    eff = view.get("effective") or view.get("code_route", {}).get("effective")
    assert eff["tool_use"] is False, eff
    assert "can't call tools" in eff["why"] and "qwen" in eff["why"]
