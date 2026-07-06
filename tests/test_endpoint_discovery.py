"""Local-endpoint model discovery: the endpoint reports its own models."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import discovery


@pytest.fixture(autouse=True)
def _fresh_cache():
    discovery.clear_cache()
    yield
    discovery.clear_cache()


def _fake_openai_listing(monkeypatch, seen: list[str]):
    def fake_get_json(url, headers):
        seen.append(url)
        if url.endswith("/v1/models"):
            return {"data": [{"id": "llama3.2"}, {"id": "qwen2.5-coder"}]}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(discovery, "_get_json", fake_get_json)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:1234",
        "http://localhost:1234/v1",
        "http://localhost:1234/v1/chat/completions",
    ],
)
def test_list_endpoint_models_normalizes_any_base_form(monkeypatch, base_url):
    seen: list[str] = []
    _fake_openai_listing(monkeypatch, seen)
    models = discovery.list_endpoint_models(base_url)
    assert models == ["llama3.2", "qwen2.5-coder"]
    assert seen == ["http://localhost:1234/v1/models"]


def test_list_endpoint_models_falls_back_to_ollama_native(monkeypatch):
    def fake_get_json(url, headers):
        if url.endswith("/v1/models"):
            raise RuntimeError("404")  # pre-shim Ollama
        assert url.endswith("/api/tags")
        return {"models": [{"name": "llama3:8b"}]}

    monkeypatch.setattr(discovery, "_get_json", fake_get_json)
    assert discovery.list_endpoint_models("http://localhost:11434") == ["llama3:8b"]


def test_list_endpoint_models_sends_key_when_given(monkeypatch):
    headers_seen: dict = {}

    def fake_get_json(url, headers):
        headers_seen.update(headers)
        return {"data": [{"id": "m"}]}

    monkeypatch.setattr(discovery, "_get_json", fake_get_json)
    discovery.list_endpoint_models("http://gw.local/v1", "sekret")
    assert headers_seen == {"Authorization": "Bearer sekret"}


def test_discover_models_supports_custom_endpoints(monkeypatch):
    _fake_openai_listing(monkeypatch, [])
    ids = discovery.discover_models(
        "custom", lambda: None, base_url="http://localhost:1234"
    )
    assert ids == ["llama3.2", "qwen2.5-coder"]


def test_endpoint_models_probe_honest_on_unreachable(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/providers/endpoint-models", json={"base_url": "http://127.0.0.1:9"}
    )
    assert r.status_code == 200  # probe failures are data, not server errors
    data = r.json()
    assert data["models"] == []
    assert data["error"]


def test_endpoint_models_probe_rejects_empty_url(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/providers/endpoint-models", json={"base_url": "  "})
    assert r.status_code == 400


def test_saving_endpoint_lights_provider_up_without_restart(tmp_path):
    """The ProviderManager used to capture ollama/custom config at boot — a
    saved endpoint stayed unavailable until the daemon restarted."""
    client = TestClient(create_app(str(tmp_path)))

    def avail(name):
        rows = client.get("/health").json()["providers"]
        return any(r["provider"] == name and r["available"] for r in rows)

    assert not avail("custom") and not avail("ollama")
    client.put(
        "/settings",
        json={"values": {"custom_base_url": "http://127.0.0.1:9",
                         "ollama_base_url": "http://127.0.0.1:9"}},
    )
    assert avail("custom") and avail("ollama")  # live, no restart
    # And clearing the endpoints turns them back off, also live.
    client.put(
        "/settings",
        json={"values": {"custom_base_url": "", "ollama_base_url": ""}},
    )
    assert not avail("custom") and not avail("ollama")


def test_models_picker_expands_custom_endpoint(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"custom_base_url": "http://127.0.0.1:9"}})
    monkeypatch.setattr(
        discovery,
        "discover_models",
        lambda prov, cred, base_url="": (
            ["llama3.2", "qwen2.5-coder"] if prov == "custom" else []
        ),
    )
    models = client.get("/models").json()["models"]
    customs = sorted(m["model"] for m in models if m["provider"] == "custom")
    assert customs == ["llama3.2", "qwen2.5-coder"]
    assert all(
        m["available"] for m in models if m["provider"] == "custom"
    )  # base_url configured => runnable
