"""Multiple custom endpoints (the Connections card) — the multi-endpoint fix.

The old flow had ONE settings slot (custom_base_url/custom_model): saving a
second endpoint silently overwrote the first, and the add form round-tripped
the saved value. Now each endpoint is a routable fleet node with its own
provider ("fleet-<id>"): adding registers the provider LIVE, /models lists
every endpoint (labeled), deleting unregisters it — and a keyed endpoint's
adapter actually resolves its vault credential (it used to get none at all).

Fully offline: node URLs point at closed local ports, so probes fail fast and
the honest failed-probe-still-saves path is what's exercised.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

DEAD_A = "http://127.0.0.1:9/v1"
DEAD_B = "http://127.0.0.1:7/v1"


def _add(client: TestClient, label: str, url: str, model: str = "m-default") -> dict:
    r = client.post(
        "/fleet/nodes",
        json={"base_url": url, "label": label, "routable": True, "default_model": model},
    )
    assert r.status_code == 200, r.text
    return r.json()["node"]


def test_second_endpoint_never_overwrites_the_first(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a = _add(client, "vLLM box", DEAD_A)
    b = _add(client, "Ollama Cloud", DEAD_B, model="llama3")
    assert a["id"] != b["id"]
    assert a["routable"] and b["routable"]
    # Both providers registered LIVE — no restart needed.
    providers = client.app.state.platform.providers
    assert f"fleet-{a['id']}" in providers._factories  # noqa: SLF001
    assert f"fleet-{b['id']}" in providers._factories  # noqa: SLF001


def test_models_lists_every_endpoint_with_its_label(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a = _add(client, "vLLM box", DEAD_A)
    b = _add(client, "Ollama Cloud", DEAD_B, model="llama3")
    models = client.get("/models").json()["models"]
    entries = {(m["provider"], m["model"]): m for m in models}
    ea = entries[(f"fleet-{a['id']}", "m-default")]
    eb = entries[(f"fleet-{b['id']}", "llama3")]
    assert ea["name"] == "vLLM box" and ea["source"] == "endpoint"
    assert eb["name"] == "Ollama Cloud"


def test_delete_endpoint_removes_provider_and_listing(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a = _add(client, "box", DEAD_A)
    prov = f"fleet-{a['id']}"
    providers = client.app.state.platform.providers
    assert prov in providers._factories  # noqa: SLF001

    r = client.delete(f"/fleet/nodes/{a['id']}")
    assert r.status_code == 200
    # No ghost provider: factory gone, availability honestly False, picker clean.
    assert prov not in providers._factories  # noqa: SLF001
    assert providers.available(prov) is False
    models = client.get("/models").json()["models"]
    assert all(m["provider"] != prov for m in models)


def test_keyed_endpoint_adapter_resolves_its_vault_credential(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    a = _add(client, "keyed", DEAD_A)
    secret = f"endpoint_{a['id']}_key"
    r = client.post(
        "/secrets", json={"name": secret, "value": "sk-test-123", "kind": "api_key"}
    )
    assert r.status_code == 200, r.text
    r = client.patch(f"/fleet/nodes/{a['id']}", json={"api_key_name": secret})
    assert r.status_code == 200, r.text
    # The PATCH re-registered the provider; its adapter must resolve the key
    # (regression: fleet adapters used to be built with NO credential at all).
    adapter = client.app.state.platform.providers.get(f"fleet-{a['id']}")
    cred = adapter._credential  # noqa: SLF001
    assert cred is not None and cred() == "sk-test-123"


def test_unrouted_node_is_not_a_picker_entry(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/fleet/nodes", json={"base_url": DEAD_A, "label": "watch-only"}
    )
    assert r.status_code == 200
    node = r.json()["node"]
    assert node["routable"] is False  # discovering a server is not consent to route
    models = client.get("/models").json()["models"]
    assert all(m["provider"] != f"fleet-{node['id']}" for m in models)
