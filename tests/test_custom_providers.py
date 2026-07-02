"""OpenRouter + custom OpenAI-compatible endpoint + refreshed Grok models."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.agents.dynamic import available_models
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.manager import (
    OPENROUTER_ENDPOINT,
    ProviderManager,
)


# --- provider manager --------------------------------------------------------


def test_openrouter_routes_through_openai_adapter():
    pm = ProviderManager(credential_resolver=lambda p: "sk-or-key")
    adapter = pm.get("openrouter", "openrouter/auto")
    assert adapter.provider == "openrouter"
    assert adapter._endpoint == OPENROUTER_ENDPOINT
    assert adapter.model == "openrouter/auto"


def test_custom_endpoint_normalized_and_gated_on_base_url():
    # Host-only URL normalizes to the real chat endpoint (Ollama Cloud style).
    pm = ProviderManager(custom_base_url="https://ollama.com", custom_model="qwen3")
    assert pm.available("custom") is True
    adapter = pm.get("custom")
    assert adapter._endpoint == "https://ollama.com/v1/chat/completions"
    assert adapter.model == "qwen3"
    # Unconfigured -> NOT available (no dead picker entries, no silent mock).
    assert ProviderManager().available("custom") is False


def test_openrouter_availability_is_credential_gated():
    with_key = ProviderManager(presence_resolver=lambda p: p == "openrouter")
    assert with_key.available("openrouter") is True
    without = ProviderManager(presence_resolver=lambda p: False)
    assert without.available("openrouter") is False


def test_custom_counts_as_a_real_provider():
    pm = ProviderManager(
        presence_resolver=lambda p: False, custom_base_url="http://localhost:1234"
    )
    assert pm.has_available_api_provider() is True  # no mock-trap downgrade


# --- model catalog -------------------------------------------------------------


def test_known_models_carry_current_grok_lineup():
    ids = {(m["provider"], m["model"]) for m in available_models()}
    assert ("xai", "grok-build-0.1") in ids  # the Grok Build CLI model
    assert ("xai", "grok-code-fast-1") in ids
    assert ("xai", "grok-4-1-fast") in ids
    assert ("openrouter", "openrouter/auto") in ids
    # Stale generation removed.
    assert ("xai", "grok-2-latest") not in ids


def test_models_endpoint_lights_up_local_and_custom(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    # Unconfigured: no ollama/custom entries (no dead options in the pickers).
    before = client.get("/models").json()["models"]
    assert not any(m["provider"] in ("ollama", "custom") for m in before)

    r = client.put(
        "/settings",
        json={
            "values": {
                "ollama_base_url": "http://localhost:11434",
                "custom_base_url": "https://ollama.com",
                "custom_model": "qwen3-coder",
            }
        },
    )
    assert r.status_code == 200

    after = client.get("/models").json()["models"]
    pairs = {(m["provider"], m["model"]) for m in after}
    assert ("ollama", "llama3.1") in pairs
    assert ("custom", "qwen3-coder") in pairs


def test_connections_page_lists_openrouter_and_custom(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    listing = {c["provider"]: c for c in client.get("/connections").json()["connections"]}
    assert listing["openrouter"]["supports_api_key"] is True
    assert listing["custom"]["supports_api_key"] is True
    assert "openrouter.ai" in listing["openrouter"]["key_help"]
    assert "custom_base_url" in listing["custom"]["key_help"]
