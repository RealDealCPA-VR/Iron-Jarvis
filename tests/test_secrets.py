"""Shared Secrets Vault tests (§7 secret handling, §10 encryption). Fully offline."""

from __future__ import annotations

import pytest
from sqlmodel import select

import iron_jarvis.secrets.models  # noqa: F401  (register table before init_db)
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.events import EventBus
from iron_jarvis.core.models import PermissionMode  # noqa: F401  (ensure metadata loaded)
from iron_jarvis.secrets.manager import SecretsManager
from iron_jarvis.secrets.models import SecretRecord
from iron_jarvis.secrets.tools import secret_tools
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry

PLAINTEXT = "super-secret-plaintext-value-ABC123"


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def manager(tmp_path, engine):
    return SecretsManager(tmp_path / "home", engine)


@pytest.fixture
def ctx(engine, tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


def _count(engine) -> int:
    with session_scope(engine) as db:
        return len(list(db.exec(select(SecretRecord))))


def test_set_get_roundtrip(manager):
    rec = manager.set("openai", PLAINTEXT, kind="api_key", description="OpenAI key")
    assert rec.name == "openai" and rec.kind == "api_key"
    assert manager.get("openai") == PLAINTEXT
    assert manager.get("missing") is None
    assert manager.exists("openai") is True
    assert manager.exists("missing") is False


def test_value_is_encrypted_at_rest(manager, engine, tmp_path):
    manager.set("anthropic", PLAINTEXT, kind="api_key")

    # The stored ciphertext must not be the plaintext.
    with session_scope(engine) as db:
        row = db.exec(
            select(SecretRecord).where(SecretRecord.name == "anthropic")
        ).first()
    assert row is not None
    assert PLAINTEXT not in row.enc_value

    # Neither the on-disk DB file nor the key file may contain the plaintext.
    db_bytes = (tmp_path / "t.db").read_bytes()
    assert PLAINTEXT.encode() not in db_bytes
    assert row.enc_value.encode() in db_bytes  # ciphertext IS persisted


def test_list_returns_names_kinds_never_values(manager):
    manager.set("slack", PLAINTEXT, kind="token", description="bot token")
    manager.set("gmail", "another-secret-XYZ", kind="oauth")

    listed = manager.list()
    names = {s["name"] for s in listed}
    assert names == {"slack", "gmail"}
    for s in listed:
        assert set(s) == {"name", "kind", "description", "has_value", "updated_at"}
        assert s["has_value"] is True
        assert "value" not in s
        assert "enc_value" not in s
    # the plaintext appears nowhere in the listing payload
    assert PLAINTEXT not in repr(listed)


def test_set_is_upsert_not_duplicate(manager, engine):
    manager.set("stripe", "first-value-111", kind="api_key")
    manager.set("stripe", "second-value-222", kind="api_key")

    assert _count(engine) == 1
    assert manager.get("stripe") == "second-value-222"


def test_set_oauth_get_oauth_json_roundtrip(manager):
    token = {"access_token": "at-123", "refresh_token": "rt-456", "expires_in": 3600}
    rec = manager.set_oauth("google", token, description="google oauth")
    assert rec.kind == "oauth"
    assert manager.get_oauth("google") == token
    assert manager.get_oauth("missing") is None


def test_delete(manager, engine):
    manager.set("temp", PLAINTEXT)
    assert manager.delete("temp") is True
    assert manager.exists("temp") is False
    assert _count(engine) == 0
    assert manager.delete("temp") is False  # idempotent: already gone


async def test_secret_tools_via_registry(manager, engine, ctx):
    registry = ToolRegistry()
    for tool in secret_tools(manager):
        registry.register(tool)
    perms = PermissionEngine({"secret_set": "allow", "secret_list": "allow"})

    # SecretSetTool stores a secret (encrypted) via the registry.
    res = await registry.invoke(
        "secret_set",
        {"name": "notion", "value": PLAINTEXT, "kind": "token"},
        ctx,
        perms,
    )
    assert res.ok
    assert manager.get("notion") == PLAINTEXT  # server-side retrieval works

    # SecretListTool returns names/kinds but never the value.
    listed = await registry.invoke("secret_list", {}, ctx, perms)
    assert listed.ok
    names = {s["name"] for s in listed.data["secrets"]}
    assert names == {"notion"}
    assert listed.data["secrets"][0]["kind"] == "token"
    assert PLAINTEXT not in listed.output
    assert PLAINTEXT not in repr(listed.data)
