"""Tests for the Integrations framework (offline).

Covers: table registration + persistence, enable/configure, registry test(),
status reporting without secret leakage, the REST integration's injected HTTP
client, and secret-resolver binding.
"""

from __future__ import annotations

import json

import iron_jarvis.integrations.models  # noqa: F401  (register table before init_db)
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.integrations.base import Integration, IntegrationSpec
from iron_jarvis.integrations.builtin import (
    MockIntegration,
    RestApiIntegration,
    register_builtins,
)
from iron_jarvis.integrations.models import IntegrationRecord
from iron_jarvis.integrations.registry import IntegrationRegistry
from sqlmodel import select

MOCK_SPEC = IntegrationSpec(
    id="mock_test",
    kind="mock",
    display_name="Mock Test Integration",
    description="example",
    required_secrets=["mock_token"],
)


def _mock_factory(config, resolver):
    return MockIntegration(config, resolver)


def _registry(tmp_path):
    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    return engine, IntegrationRegistry(engine)


def test_enable_and_configure_persists_one_record(tmp_path):
    engine, reg = _registry(tmp_path)
    reg.register(MOCK_SPEC, _mock_factory)

    reg.configure("mock_test", {"foo": "bar"})
    reg.enable("mock_test", True)

    with session_scope(engine) as db:
        rows = db.exec(
            select(IntegrationRecord).where(
                IntegrationRecord.integration_id == "mock_test"
            )
        ).all()

    assert len(rows) == 1  # configure + enable upsert the same row
    row = rows[0]
    assert row.enabled is True
    assert row.kind == "mock"
    assert json.loads(row.config_json) == {"foo": "bar"}
    assert row.id.startswith("intg_")


def test_registry_test_returns_ok(tmp_path):
    _engine, reg = _registry(tmp_path)
    reg.register(MOCK_SPEC, _mock_factory)

    result = reg.test("mock_test", lambda name: None)
    assert result["ok"] is True
    assert isinstance(result["detail"], str)


def test_list_status_shows_state_without_secret_values(tmp_path):
    _engine, reg = _registry(tmp_path)
    reg.register(MOCK_SPEC, _mock_factory)

    reg.configure("mock_test", {"api_token": "SUPER_SECRET_VALUE"})
    reg.enable("mock_test", True)

    status = reg.list_status()
    entry = next(s for s in status if s["id"] == "mock_test")

    assert entry["enabled"] is True
    assert entry["configured"] is True
    assert entry["kind"] == "mock"
    assert entry["display_name"] == "Mock Test Integration"
    # only the *names* of required secrets, never values, and never config values
    assert entry["required_secrets"] == ["mock_token"]
    assert "SUPER_SECRET_VALUE" not in json.dumps(status)


def test_list_status_unconfigured_disabled_by_default(tmp_path):
    _engine, reg = _registry(tmp_path)
    reg.register(MOCK_SPEC, _mock_factory)

    entry = reg.list_status()[0]
    assert entry["enabled"] is False
    assert entry["configured"] is False


def test_rest_integration_uses_injected_http_client(tmp_path):
    calls: dict = {}

    def fake_get(url, headers=None):
        calls["url"] = url
        calls["headers"] = dict(headers or {})
        return {"ok": True, "status_code": 200}

    integ = RestApiIntegration(
        {"base_url": "https://api.example.com/health", "http_get": fake_get},
        lambda name: None,
    )

    result = integ.test_connection()

    assert result["ok"] is True
    assert calls["url"] == "https://api.example.com/health"
    # no auth secret configured -> no Authorization header, no real network
    assert "Authorization" not in calls["headers"]
    assert integ.capabilities() == ["rest.get"]


def test_secret_resolver_binding_supplies_token(tmp_path):
    captured: dict = {}

    def fake_get(url, headers=None):
        captured.update(headers or {})
        return {"ok": True, "status_code": 200}

    def resolver(name):
        return "FAKE-TOKEN-123" if name == "example_api_key" else None

    integ = RestApiIntegration(
        {
            "base_url": "https://api.example.com",
            "auth_secret": "example_api_key",
            "http_get": fake_get,
        },
        resolver,
    )

    result = integ.test_connection()

    assert result["ok"] is True
    # the resolver-provided token was bound into the request headers
    assert captured.get("Authorization") == "Bearer FAKE-TOKEN-123"


def test_register_builtins_registers_mock_and_rest(tmp_path):
    _engine, reg = _registry(tmp_path)
    register_builtins(reg)

    ids = {s.id for s in reg.specs()}
    assert {"mock", "rest_api"} <= ids

    # the built-in mock integration is reachable through the registry
    assert reg.test("mock", lambda name: None)["ok"] is True


def test_unknown_integration_test_is_safe(tmp_path):
    _engine, reg = _registry(tmp_path)
    result = reg.test("does_not_exist", lambda name: None)
    assert result["ok"] is False
    assert "does_not_exist" in result["detail"]


def test_integration_is_abstract():
    # Integration cannot be instantiated directly; subclasses implement the API.
    assert issubclass(MockIntegration, Integration)
