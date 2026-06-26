"""Tests for the optional bearer-token auth middleware (§ deploy hardening).

These build a tiny standalone FastAPI app and attach ``TokenAuthMiddleware``
directly, so the middleware's behavior is proven independently of how
``daemon/app.py`` wires it up.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.daemon.auth import TokenAuthMiddleware, auth_enabled


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/x")
    def x() -> dict:
        return {"ok": True}

    @app.get("/oauth/{provider}/callback")
    def oauth_cb(provider: str) -> dict:
        return {"provider": provider}

    return app


def test_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    assert auth_enabled() is False
    client = TestClient(_make_app())

    assert client.get("/health").status_code == 200
    # Any route is reachable without a token when auth is off.
    assert client.get("/x").status_code == 200


def test_empty_token_treated_as_disabled(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "   ")
    assert auth_enabled() is False
    client = TestClient(_make_app())
    assert client.get("/x").status_code == 200


def test_enabled_requires_token(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "secret")
    assert auth_enabled() is True
    client = TestClient(_make_app())

    # No header -> 401.
    res = client.get("/x")
    assert res.status_code == 401
    assert res.json()["detail"] == "missing or invalid token"

    # Correct bearer token -> 200.
    res = client.get("/x", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    # Wrong token -> 401.
    assert client.get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_health_exempt_even_when_enabled(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "secret")
    client = TestClient(_make_app())
    assert client.get("/health").status_code == 200


def test_query_token_accepted(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "secret")
    client = TestClient(_make_app())
    # The ?token= form is what the browser uses for WS / OAuth URLs.
    assert client.get("/x?token=secret").status_code == 200
    assert client.get("/x?token=wrong").status_code == 401


def test_oauth_callback_exempt(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "secret")
    client = TestClient(_make_app())
    # Provider redirect carries no Authorization header; must be allowed.
    assert client.get("/oauth/google/callback").status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_exempt(monkeypatch, path):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "secret")
    client = TestClient(_make_app())
    assert client.get(path).status_code == 200
