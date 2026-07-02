"""POST /connections/{provider}/default — user picks the active provider."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_make_connected_provider_default(tmp_path):
    client = _client(tmp_path)
    client.post("/connections/anthropic/key", json={"key": "sk-ant-x"})
    r = client.post("/connections/anthropic/default")
    assert r.status_code == 200
    assert r.json()["default_provider"] == "anthropic"
    assert r.json()["default_model"] == "claude-opus-4-8"
    assert client.get("/health").json()["default_provider"] == "anthropic"


def test_cannot_default_an_unconnected_provider(tmp_path):
    client = _client(tmp_path)
    r = client.post("/connections/xai/default")  # never connected
    assert r.status_code == 400


def test_unknown_provider_404(tmp_path):
    assert _client(tmp_path).post("/connections/nope/default").status_code == 404


def test_switch_default_between_two_connected(tmp_path):
    client = _client(tmp_path)
    client.post("/connections/anthropic/key", json={"key": "sk-ant-x"})
    client.post("/connections/openai/key", json={"key": "sk-openai-x"})
    client.post("/connections/anthropic/default")
    assert client.get("/health").json()["default_provider"] == "anthropic"
    client.post("/connections/openai/default")
    assert client.get("/health").json()["default_provider"] == "openai"
