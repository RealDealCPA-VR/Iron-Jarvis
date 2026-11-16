"""Second review F9 (v1.514.0): the Model servers page counted tokens a
model proxy sent on to a CLOUD service as "local" and as "estimated avoided
spend".

``GET /fleet/usage`` judged each usage row by its provider NAME alone
(``fleet-*`` = local). A LiteLLM gateway the user runs serves some names
from their own boxes and others from a cloud service (``fleet/upstreams``);
the second kind was billed, yet the page said "local … tok", "N% of tokens
ran locally" and "est. $X avoided" for it. Each per-model row is now judged
by the proxy's own routing table (the one place that knows, read from the
registry's cached table: never a network request). A row that does not stay
on this network is cloud, adds nothing to "avoided", and its cost is "not
known" when no price is known.

Offline: a real FleetRegistry holding a parsed routing table, the real
ProviderManager oracle, and the usage rollup replaced by fixed rows (so no
CLI store on this computer is read).
"""

from __future__ import annotations

import types
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import fleet as fleet_routes
from iron_jarvis.eval import usage_view
from iron_jarvis.fleet.registry import FleetRegistry
from iron_jarvis.fleet.upstreams import parse_model_info

#: A gateway on this network that serves ``local-coder`` from a box on the
#: same network and ``frontier`` from a cloud service (no api_base, a hosted
#: provider prefix).
GATEWAY = "http://192.168.1.40:4000/v1"
GATEWAY_INFO = {
    "data": [
        {
            "model_name": "local-coder",
            "litellm_params": {"model": "hosted_vllm/coder", "api_base": "http://192.168.1.41:8000/v1"},
        },
        {
            "model_name": "frontier",
            "litellm_params": {"model": "openrouter/openrouter/auto-beta"},
        },
    ]
}

M = 1_000_000


class _Sampler:
    def touch(self) -> None:  # pragma: no cover - not reached by /fleet/usage
        pass


def _wire(tmp_path, monkeypatch, rows):
    platform = create_app(str(tmp_path / "home")).state.platform
    cfg = SimpleNamespace(
        home=tmp_path / "fleet", fleet_nodes=[], ollama_base_url="", ollama_model="",
        custom_base_url=GATEWAY, custom_model="local-coder",
    )
    (tmp_path / "fleet").mkdir()
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    reg.set_routes("custom", parse_model_info(GATEWAY_INFO))
    # The manager's upstream oracle IS the registry's table (platform.py
    # wires exactly this); a fresh registry here keeps the test's table.
    monkeypatch.setattr(platform.providers, "_upstream_place", reg.route_place)
    monkeypatch.setattr(
        usage_view, "merged_usage", lambda _p, _days=30: {"by_model": list(rows)}
    )
    d = types.SimpleNamespace(platform=platform, fleet=reg, fleet_sampler=_Sampler())
    app = FastAPI()
    fleet_routes.register(app, d)
    return TestClient(app), d


def _row(provider, model, tin, tout=0, cost=0.0):
    return {
        "provider": provider, "model": model, "input_tokens": tin,
        "output_tokens": tout, "cost_usd": cost, "runs": 1,
    }


def test_a_gateway_name_served_from_the_cloud_is_cloud_and_avoids_nothing(tmp_path, monkeypatch):
    client, d = _wire(
        tmp_path, monkeypatch,
        [
            _row("fleet-custom", "local-coder", M),  # stays on this network
            _row("fleet-custom", "frontier", 2 * M),  # the gateway sent it to a cloud service
        ],
    )
    # The oracle the page now reads agrees with the table.
    assert d.platform.providers.upstream_place("fleet-custom", "frontier") == "cloud"
    out = client.get("/fleet/usage").json()
    assert out["local_tokens"] == M, out
    assert out["cloud_tokens"] == 2 * M, out
    # Priced on the default baseline ($5 per million in): only the local name.
    assert out["est_avoided_usd"] == 5.0, out
    # Billed somewhere, at a price Iron Jarvis does not know: never $0 spend
    # read as nothing, never avoided.
    assert out["cloud_cost_unknown_tokens"] == 2 * M, out
    rows = {e["node_id"]: e for e in out["by_node"]}
    assert rows["custom"]["models"] == ["local-coder"], rows
    assert rows["custom"]["input_tokens"] == M
    assert rows["custom"]["est_avoided_usd"] == 5.0


def test_a_plain_local_server_is_unchanged(tmp_path, monkeypatch):
    client, _d = _wire(
        tmp_path, monkeypatch,
        [
            _row("fleet-tower", "qwen3-coder", M, M),  # no routing table: the address decides
            _row("ollama", "llama3.1", M),
            _row("anthropic", "claude-opus-4-8", M, 0, cost=5.0),
        ],
    )
    out = client.get("/fleet/usage").json()
    assert out["local_tokens"] == 3 * M
    assert out["cloud_tokens"] == M
    assert out["cloud_cost_usd"] == 5.0
    assert out["cloud_cost_unknown_tokens"] == 0
    assert out["est_avoided_usd"] == 35.0  # (2M in * $5 + 1M out * $25) / 1M


def test_the_row_judge_never_asks_the_network_and_fails_toward_cloud(monkeypatch):
    """``usage_row_is_local`` reads the manager's cached oracle only; an
    oracle that raises reads as cloud (a privacy question fails closed)."""

    class _Mgr:
        def upstream_place(self, name, model):
            raise RuntimeError("table unreadable")

        def get(self, *_a, **_k):
            return SimpleNamespace(model="")

    assert usage_view.usage_row_is_local("fleet-gw", "x", _Mgr()) is False
    assert usage_view.usage_row_is_local("anthropic", "x", None) is False
    assert usage_view.usage_row_is_local("fleet-gw", "x", None) is True  # no oracle: the name decides
