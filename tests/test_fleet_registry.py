"""Fleet registry + adapter: which endpoints exist, and which may serve traffic.

Two invariants carry this module and both are load-bearing:

* **Capability honesty.** ``LLMAdapter.capabilities()`` defaults tool_use to
  True, so an unverified local model would be handed tool-using (coding) work
  and silently return no tool calls forever. A fleet node reports only what its
  record asserts.
* **Availability is observed, not assumed.** ``reachable()`` runs on the routing
  hot path, must never touch the network, and must not claim a node is ready
  before anything has confirmed it.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.config import Config
from iron_jarvis.fleet.adapter import FleetAdapter
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.fleet.registry import FleetRegistry, provider_name


def _config(tmp_path, **kw):
    cfg = Config(home=tmp_path / ".ironjarvis", project_root=tmp_path, **kw)
    cfg.ensure_dirs()
    return cfg


def _reload(tmp_path) -> Config:
    """Re-read the config the registry just wrote.

    Deliberately NOT ``load_config``: that layers the developer's real global
    ``~/.ironjarvis/config.toml`` on top, which would drag their actual local
    endpoints into these assertions.
    """
    import tomllib

    raw = tomllib.loads((tmp_path / ".ironjarvis" / "config.toml").read_text("utf-8"))
    return _config(tmp_path, **raw)


# --- seeds are derived, never persisted ---------------------------------------


def test_config_slots_are_seeded_as_nodes_without_being_persisted(tmp_path):
    cfg = _config(
        tmp_path,
        ollama_base_url="http://tower:8003",
        ollama_model="qwen3.6:27b",
        custom_base_url="http://proxy:4000",
        custom_model="fleet",
    )
    reg = FleetRegistry(cfg)
    ids = {n.id for n in reg.nodes()}
    assert ids == {"ollama", "custom"}
    seeded = {n.id: n for n in reg.nodes()}
    assert seeded["ollama"].kind == "ollama"
    assert seeded["ollama"].default_model == "qwen3.6:27b"
    assert seeded["ollama"].routable is True
    # Derived on every read — nothing was written into fleet_nodes.
    assert cfg.fleet_nodes == []


def test_no_endpoints_configured_means_no_nodes(tmp_path):
    assert FleetRegistry(_config(tmp_path)).nodes() == []


def test_stored_node_overrides_its_seed(tmp_path):
    cfg = _config(tmp_path, ollama_base_url="http://tower:8003")
    reg = FleetRegistry(cfg)
    reg.add(
        FleetNode(id="ollama", label="My tower", base_url="http://tower:8003", tool_use=True)
    )
    node = reg.get("ollama")
    assert node.label == "My tower" and node.tool_use is True
    assert len([n for n in reg.nodes() if n.id == "ollama"]) == 1  # not duplicated


# --- persistence round-trip ----------------------------------------------------


def test_add_survives_a_config_reload(tmp_path):
    """The None-drop in atomic_write_toml must not lose a node (TOML has no null,
    and an unverified node carries tool_use=None)."""
    reg = FleetRegistry(_config(tmp_path))
    reg.add(FleetNode(id="spark", label="Spark", base_url="http://s:8000/v1", routable=True))
    reloaded = _reload(tmp_path)
    assert [n["id"] for n in reloaded.fleet_nodes] == ["spark"]
    again = FleetRegistry(reloaded)
    node = again.get("spark")
    assert node is not None and node.base_url == "http://s:8000/v1"
    assert node.tool_use is None  # absent key reads back as "never verified"


def test_update_and_remove_round_trip(tmp_path):
    reg = FleetRegistry(_config(tmp_path))
    reg.add(FleetNode(id="spark", base_url="http://s:8000/v1"))
    reg.update("spark", label="Renamed", tool_use=True)
    assert reg.get("spark").label == "Renamed"
    assert FleetRegistry(_reload(tmp_path)).get("spark").tool_use is True
    reg.remove("spark")
    assert reg.get("spark") is None
    assert FleetRegistry(_reload(tmp_path)).get("spark") is None


def test_invalid_ids_are_rejected(tmp_path):
    reg = FleetRegistry(_config(tmp_path))
    # A colon would break parse_pm ("fleet-a:b" parses as provider + model).
    for bad in ("has:colon", "Upper", "has space", "", "-leading", "x" * 40):
        with pytest.raises(ValueError):
            reg.add(FleetNode(id=bad, base_url="http://x"))
    with pytest.raises(ValueError):
        reg.add(FleetNode(id="ok", base_url="   "))


def test_removing_a_config_seed_refuses_and_points_at_settings(tmp_path):
    reg = FleetRegistry(_config(tmp_path, ollama_base_url="http://tower:8003"))
    with pytest.raises(ValueError, match="Settings"):
        reg.remove("ollama")


# --- topology children ---------------------------------------------------------


def test_absorbed_children_are_in_memory_and_replaced_wholesale(tmp_path):
    cfg = _config(tmp_path)
    reg = FleetRegistry(cfg)
    kids = [
        FleetNode(id="p-brain", parent_id="p", alias="brain", source="topology"),
        FleetNode(id="p-coder", parent_id="p", alias="coder", source="topology"),
    ]
    reg.absorb_children("p", kids)
    assert {n.id for n in reg.nodes()} == {"p-brain", "p-coder"}
    assert cfg.fleet_nodes == []  # never persisted
    # An alias removed on the proxy disappears here on the next absorb.
    reg.absorb_children("p", kids[:1])
    assert {n.id for n in reg.nodes()} == {"p-brain"}


def test_topology_children_are_never_routable(tmp_path):
    """They are already reachable via their proxy's alias; registering them
    again would show the same GPU twice in every picker."""
    reg = FleetRegistry(_config(tmp_path, custom_base_url="http://proxy:4000"))
    reg.absorb_children(
        "custom",
        [FleetNode(id="custom-fleet", parent_id="custom", alias="fleet", routable=True)],
    )
    assert [n.id for n in reg.routable_nodes()] == ["custom"]


# --- reachability: hot path, no network ---------------------------------------


def test_reachable_defers_for_unknown_providers_and_never_calls_out(tmp_path, monkeypatch):
    import httpx

    def _boom(*a, **k):  # any network use on the routing hot path is a bug
        raise AssertionError("reachable() must never touch the network")

    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(httpx, "post", _boom)

    reg = FleetRegistry(_config(tmp_path, ollama_base_url="http://tower:8003"))
    assert reg.reachable("anthropic") is None  # not ours — other logic decides
    assert reg.reachable("fleet-nope") is None  # unknown node defers to the factory test
    assert reg.reachable("fleet-ollama") is None  # known but UNPROBED is not a claim
    reg.set_reachable("ollama", True)
    assert reg.reachable("fleet-ollama") is True
    reg.set_reachable("ollama", False)
    assert reg.reachable("fleet-ollama") is False


def test_disabled_node_is_unavailable_even_if_it_answered(tmp_path):
    reg = FleetRegistry(_config(tmp_path))
    reg.add(FleetNode(id="spark", base_url="http://s:8000/v1", enabled=False))
    reg.set_reachable("spark", True)
    assert reg.reachable("fleet-spark") is False


# --- provider registration -----------------------------------------------------


class _Manager:
    def __init__(self, explode_on: str = "") -> None:
        self.registered: dict[str, object] = {}
        self._explode_on = explode_on

    def register(self, name: str, factory) -> None:
        if name == self._explode_on:
            raise RuntimeError("bad node")
        self.registered[name] = factory


def test_register_providers_covers_routable_nodes_only(tmp_path):
    reg = FleetRegistry(_config(tmp_path, ollama_base_url="http://tower:8003"))
    reg.add(FleetNode(id="spark", base_url="http://s:8000/v1", routable=True))
    reg.add(FleetNode(id="watch", base_url="http://w:9000/v1", routable=False))
    mgr = _Manager()
    assert reg.register_providers(mgr) == 2
    assert set(mgr.registered) == {"fleet-ollama", "fleet-spark"}
    assert provider_name("spark") == "fleet-spark"


def test_one_bad_node_cannot_crash_boot(tmp_path):
    reg = FleetRegistry(_config(tmp_path))
    reg.add(FleetNode(id="good", base_url="http://g:1/v1", routable=True))
    reg.add(FleetNode(id="bad", base_url="http://b:1/v1", routable=True))
    mgr = _Manager(explode_on="fleet-bad")
    assert reg.register_providers(mgr) == 1
    assert set(mgr.registered) == {"fleet-good"}


def test_registered_factory_builds_a_fleet_adapter(tmp_path):
    reg = FleetRegistry(_config(tmp_path))
    reg.add(
        FleetNode(id="spark", base_url="http://s:8000/v1", routable=True, default_model="coder")
    )
    mgr = _Manager()
    reg.register_providers(mgr)
    adapter = mgr.registered["fleet-spark"]()
    assert isinstance(adapter, FleetAdapter)
    assert adapter.provider == "fleet-spark"  # distinct name => its own circuit breaker
    assert adapter.model == "coder"
    assert mgr.registered["fleet-spark"]("other").model == "other"


# --- capability honesty (the silent-stall guard) -------------------------------


def test_unverified_node_reports_no_tool_use(tmp_path):
    """The base adapter assumes tool_use=True. A node nobody has verified must
    NOT inherit that, or the router hands it agent work it silently can't do."""
    node = FleetNode(id="spark", base_url="http://s:8000/v1")
    caps = FleetAdapter(node=node).capabilities()
    assert caps["tool_use"] is False
    assert caps["vision"] is False


def test_verified_node_reports_its_real_capabilities(tmp_path):
    node = FleetNode(id="spark", base_url="http://s:8000/v1", tool_use=True, vision=True)
    caps = FleetAdapter(node=node).capabilities()
    assert caps["tool_use"] is True and caps["vision"] is True

    denied = FleetNode(id="s2", base_url="http://s:8000/v1", tool_use=False)
    assert FleetAdapter(node=denied).capabilities()["tool_use"] is False
