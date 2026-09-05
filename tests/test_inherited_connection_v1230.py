"""v1.230.0 (audit Wave 4, U5) — connected means connected, on every surface.

The live finding: Connections said Anthropic / OpenAI "Not connected" while
/health said ``available: true`` and the model switcher offered claude-opus
undimmed. No key was stored anywhere. The mechanism is
``ProviderManager._INHERIT_ALIAS`` (anthropic -> claude-cli, openai ->
codex-cli): a keyless API provider is served through the logged-in CLI, and
``available()`` knew that — the Connections status read only the
ConnectionRecord.

One truth now: ``ProviderManager.inherited_from(name)`` answers for
``available()``, ``health()``'s row, ``ConnectionRegistry.status()`` (status
``connected`` + source ``inherited from claude-cli``), ``test()`` and the
/models picker rows (``inherited_from`` so the chat picker says "included",
not "metered"). A stored key still wins and reports source ``vault``.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.connections.models  # noqa: F401  (register table before init_db)
from iron_jarvis.connections import ConnectionRegistry
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.manager import ProviderManager


class FakeSecrets:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, name, value, kind="generic", description=""):
        self.store[name] = value
        return {"name": name, "kind": kind}

    def get(self, name):
        return self.store.get(name)

    def set_oauth(self, name, token, description=""):
        self.store[name] = json.dumps(token)
        return {"name": name, "kind": "oauth"}

    def get_oauth(self, name):
        raw = self.store.get(name)
        return json.loads(raw) if raw is not None else None

    def delete(self, name):
        return self.store.pop(name, None) is not None


@pytest.fixture
def claude_cli_only(monkeypatch):
    """The `claude` binary is on this box, `codex` is not — deterministically
    (the session conftest forces both OFF; this overrides for one test)."""
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude")
    )


# --- the manager: one answer ---------------------------------------------------


def test_manager_inherited_from_names_the_cli_and_a_key_wins(claude_cli_only):
    m = ProviderManager(inherit_cli_logins=True)
    assert m.inherited_from("anthropic") == "claude-cli"
    assert m.available("anthropic") is True
    # codex is absent: openai is neither inherited nor available.
    assert m.inherited_from("openai") is None
    assert m.available("openai") is False
    # Not an API provider → never inherited.
    assert m.inherited_from("claude-cli") is None
    # A stored key takes the raw adapter: inherited_from must say None.
    keyed = ProviderManager(
        inherit_cli_logins=True,
        credential_resolver=lambda n: "sk-ant-api-xyz" if n == "anthropic" else None,
    )
    assert keyed.inherited_from("anthropic") is None
    assert keyed.available("anthropic") is True


def test_manager_health_row_carries_inherited_from(claude_cli_only):
    m = ProviderManager(inherit_cli_logins=True)
    rows = {r["provider"]: r for r in m.health()}
    assert rows["anthropic"]["available"] is True
    assert rows["anthropic"]["inherited_from"] == "claude-cli"
    assert rows["openai"]["inherited_from"] is None
    assert rows["claude-cli"]["inherited_from"] is None


# --- the registry: status + test read that answer -----------------------------


def _registry(tmp_path, inherited_from):
    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    return ConnectionRegistry(engine, FakeSecrets(), inherited_from=inherited_from)


def test_status_reports_connected_with_inherited_source(tmp_path, claude_cli_only):
    m = ProviderManager(inherit_cli_logins=True)
    registry = _registry(tmp_path, m.inherited_from)
    rows = {s["provider"]: s for s in registry.status()}
    assert rows["anthropic"]["connected"] is True
    assert rows["anthropic"]["status"] == "connected"
    assert rows["anthropic"]["source"] == "inherited from claude-cli"
    # No credential is stored — the row must not invent an account either.
    assert rows["anthropic"]["account"] == ""
    # openai: codex is absent → honestly disconnected, with no source.
    assert rows["openai"]["connected"] is False
    assert rows["openai"]["status"] == "disconnected"
    assert rows["openai"]["source"] == ""
    # Test agrees with the row it sits under.
    probe = registry.test("anthropic")
    assert probe["ok"] is True
    assert "claude CLI" in probe["detail"]
    assert "not connected" not in probe["detail"]


def test_status_without_an_oracle_is_record_only(tmp_path):
    registry = _registry(tmp_path, None)
    rows = {s["provider"]: s for s in registry.status()}
    assert rows["anthropic"]["connected"] is False
    assert rows["anthropic"]["source"] == ""


def test_stored_key_reports_vault_not_inherited(tmp_path, claude_cli_only):
    registry = _registry(tmp_path, None)
    m = ProviderManager(
        inherit_cli_logins=True,
        credential_resolver=registry.credential,
        presence_resolver=registry.has_credential,
    )
    registry.inherited_from = m.inherited_from
    registry.set_api_key("anthropic", "sk-ant-api-stored")
    rows = {s["provider"]: s for s in registry.status()}
    assert rows["anthropic"]["connected"] is True
    assert rows["anthropic"]["source"] == "vault"
    assert "sk-ant-api-stored" not in json.dumps(registry.status())


def test_oracle_fault_is_not_inherited(tmp_path):
    def boom(_name):
        raise RuntimeError("detection exploded")

    registry = _registry(tmp_path, boom)
    rows = {s["provider"]: s for s in registry.status()}
    assert rows["anthropic"]["connected"] is False


# --- the app: /connections, /health and /models tell one story ----------------


def test_connections_health_and_models_agree(tmp_path, claude_cli_only):
    client = TestClient(create_app(str(tmp_path)))

    conns = {c["provider"]: c for c in client.get("/connections").json()["connections"]}
    assert conns["anthropic"]["connected"] is True
    assert conns["anthropic"]["status"] == "connected"
    assert conns["anthropic"]["source"] == "inherited from claude-cli"
    assert conns["openai"]["connected"] is False

    health = {p["provider"]: p for p in client.get("/health").json()["providers"]}
    assert health["anthropic"]["available"] is True
    assert health["anthropic"]["inherited_from"] == "claude-cli"
    assert health["openai"]["available"] is False
    # The two surfaces cannot disagree: connected on one ⇔ available on the other.
    for prov in ("anthropic", "openai"):
        assert conns[prov]["connected"] == health[prov]["available"]

    models = client.get("/models").json()["models"]
    anthropic_rows = [m for m in models if m["provider"] == "anthropic"]
    assert anthropic_rows, "the picker still offers Anthropic's models"
    assert all(m["inherited_from"] == "claude-cli" for m in anthropic_rows)
    assert all(m["available"] is True for m in anthropic_rows)
    assert all(m.get("inherited_from") is None for m in models if m["provider"] != "anthropic")

    probe = client.post("/connections/anthropic/test").json()
    assert probe["ok"] is True
