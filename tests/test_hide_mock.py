"""The internal 'mock' offline model is hidden from every user-facing list,
but stays live in the engine as the fallback + autopromote sentinel."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_mock_hidden_from_models(tmp_path):
    models = _client(tmp_path).get("/models").json()["models"]
    assert not any(m["provider"] == "mock" for m in models)


def test_mock_hidden_from_connections(tmp_path):
    conns = _client(tmp_path).get("/connections").json()["connections"]
    assert not any(c["provider"] == "mock" for c in conns)
    # real providers still listed
    assert any(c["provider"] == "anthropic" for c in conns)


def test_mock_hidden_from_health_and_providers(tmp_path):
    client = _client(tmp_path)
    assert not any(p["provider"] == "mock" for p in client.get("/health").json()["providers"])
    # v1.439.0: GET /providers (an older copy of /health's list) was removed.
    assert client.get("/providers").status_code in (404, 405)


def test_mock_still_works_as_engine_fallback(tmp_path):
    # A fresh install defaults to mock and a session still runs (fallback intact),
    # even though mock never appears in the pickers above.
    client = _client(tmp_path)
    assert client.get("/health").json()["default_provider"] == "mock"
    r = client.post("/sessions", json={"task": "say hi", "wait": True})
    assert r.status_code == 200
    assert r.json()["status"] in ("completed", "failed")  # ran, didn't crash
