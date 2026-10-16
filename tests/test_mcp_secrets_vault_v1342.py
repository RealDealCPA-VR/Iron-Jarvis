"""v1.342.0 ("the pack says why"), lane C-secrets: a pack's env SECRETS live
in the vault, never in config.toml and never in a route answer.

Before this lane ``POST /mcp/servers`` stored ``env`` verbatim (a GitHub
token in ``config.mcp_servers``) and ``GET /mcp/servers`` echoed every value
to every dashboard window. Now the KEY decides (``SECRET_KEY_RX``: token /
key / secret / passw / auth / credential, any case): a secret value goes to
the vault as ``mcp.<server>.<KEY>``, the stored row keeps ``env`` (plain
only) + ``env_secrets[KEY] = vault name`` (what ``_build_transport`` already
resolves at spawn), rows carry ``env_keys`` / ``secret_keys`` and no value,
a delete takes the vault rows + the manifest with it, and
``migrate_mcp_env_secrets`` moves what an older install stored ONCE.

Every route here is driven on the REAL app (``create_app``), and every test
that claims a secret is gone asserts the VALUE is absent from the config
object, the config.toml bytes and the answer — a test that passes with the
secret still in config is wrong.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import persist_config_values
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.manifest import PackManifest
from iron_jarvis.mcp.secrets import (
    SECRET_KEY_RX,
    delete_pack_secrets,
    migrate_mcp_env_secrets,
    split_env,
    vault_name,
)

#: A launcher that cannot exist: the POST's best-effort live load fails at
#: once (no child, no network) and the row is persisted exactly as it would
#: be for a pack whose command is installed later.
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1342c"

TOKEN = "ghp_x"


def _toml_text(platform) -> str:
    return (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")


def _entry(platform, name: str) -> dict:
    for s in platform.config.mcp_servers:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no config entry named {name!r}")


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


# --------------------------------------------------------------------------- #
# The rule: the KEY's name decides.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "key",
    [
        "GITHUB_TOKEN",
        "api_key",
        "ApiKey",
        "Authorization",
        "DB_PASSWORD",
        "PASSWD",
        "CLIENT_SECRET",
        "AWS_CREDENTIALS",
        "OAUTH_CLIENT_ID",
    ],
)
def test_secret_key_regex_matches_secret_shaped_keys_in_any_case(key):
    assert SECRET_KEY_RX.search(key), key


@pytest.mark.parametrize("key", ["PORT", "HOME", "PATH", "DEBUG", "LOG_LEVEL", "REGION", "NODE_ENV"])
def test_secret_key_regex_leaves_plain_settings_alone(key):
    assert not SECRET_KEY_RX.search(key), key


def test_split_env_keeps_plain_entries_and_hands_back_secret_values():
    plain, secret = split_env("gh", {"GITHUB_TOKEN": TOKEN, "PORT": "1", "EMPTY_KEY": ""})
    assert plain == {"PORT": "1"}
    assert secret == {"GITHUB_TOKEN": TOKEN}, "an empty secret is nothing to keep"
    assert vault_name("gh", "GITHUB_TOKEN") == "mcp.gh.GITHUB_TOKEN"


# --------------------------------------------------------------------------- #
# POST /mcp/servers: the secret goes to the vault, the row keeps the name.
# --------------------------------------------------------------------------- #
def test_post_stores_the_secret_in_the_vault_and_the_plain_entry_in_env(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={"name": "gh", "command": NO_SUCH_COMMAND, "env": {"GITHUB_TOKEN": TOKEN, "PORT": "1"}},
    )
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text, "the add answer must not echo the value"
    assert res.json()["secret_keys"] == ["GITHUB_TOKEN"]
    assert res.json()["env_keys"] == ["PORT"]

    # The vault holds the value under the contract's name.
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN
    # The stored config carries the plain entry and the vault NAME only.
    entry = _entry(platform, "gh")
    assert entry["env"] == {"PORT": "1"}
    assert entry["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}
    assert TOKEN not in json.dumps(platform.config.mcp_servers)
    # And the bytes on disk — what an older install's backup would carry.
    text = _toml_text(platform)
    assert TOKEN not in text
    on_disk = tomllib.loads(text)["mcp_servers"]
    assert [s["name"] for s in on_disk] == ["gh"]
    assert on_disk[0]["env"] == {"PORT": "1"}
    assert on_disk[0]["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}


def test_get_rows_carry_key_names_and_never_a_value(client):
    client.post(
        "/mcp/servers",
        json={"name": "gh", "command": NO_SUCH_COMMAND, "env": {"GITHUB_TOKEN": TOKEN, "PORT": "1"}},
    )
    res = client.get("/mcp/servers")
    assert res.status_code == 200
    assert TOKEN not in res.text
    row = next(r for r in res.json()["servers"] if r["name"] == "gh")
    assert row["env_keys"] == ["PORT"]
    assert row["secret_keys"] == ["GITHUB_TOKEN"]
    assert "env" not in row and "env_secrets" not in row, "a row names keys, it carries no map"
    # The rest of the row is what it was.
    assert row["args"] == [] and "tools_loaded" in row and "tool_names" in row


def test_get_masks_a_pre_migration_row_that_still_holds_a_plain_token(client):
    """An older config.toml may still carry the value (a migration the vault
    refused). The row still lists the key and never the value."""
    platform = client.app.state.platform
    platform.config.mcp_servers = [
        {"name": "old", "command": "npx", "env": {"BRAVE_API_KEY": "brave-plain-value", "PORT": "2"}}
    ]
    res = client.get("/mcp/servers")
    assert "brave-plain-value" not in res.text
    row = res.json()["servers"][0]
    assert row["env_keys"] == ["BRAVE_API_KEY", "PORT"]
    assert row["secret_keys"] == []


def test_post_refuses_the_add_when_the_vault_refuses_the_write(client, monkeypatch):
    """A secret never lands in config.toml as a fallback."""
    platform = client.app.state.platform

    def _refuse(*a, **k):
        raise RuntimeError("vault closed")

    monkeypatch.setattr(platform.secrets, "set", _refuse)
    res = client.post(
        "/mcp/servers",
        json={"name": "gh", "command": NO_SUCH_COMMAND, "env": {"GITHUB_TOKEN": TOKEN}},
    )
    assert res.status_code == 503
    assert TOKEN not in res.text
    assert "GITHUB_TOKEN" in res.json()["detail"]
    assert platform.config.mcp_servers == []
    assert TOKEN not in _toml_text(platform) if (Path(platform.config.home) / "config.toml").exists() else True


# --------------------------------------------------------------------------- #
# The migration: an older install's plain token moves ONCE.
# --------------------------------------------------------------------------- #
def _plant_plain_token(platform, value: str = "ghp_planted") -> None:
    platform.config.mcp_servers = [
        {"name": "gh", "command": "npx", "args": ["-y", "x"], "env": {"GITHUB_TOKEN": value, "PORT": "1"}},
        {"name": "plain", "command": "npx", "env": {"REGION": "us"}},
    ]
    persist_config_values(platform.config.home, {"mcp_servers": platform.config.mcp_servers})
    assert value in _toml_text(platform), "the plant must be on disk for the test to mean anything"


def test_migration_moves_a_planted_plain_token_once_and_never_twice(client, caplog):
    platform = client.app.state.platform
    _plant_plain_token(platform)

    with caplog.at_level(logging.INFO, logger="iron_jarvis"):
        first = migrate_mcp_env_secrets(platform)
    assert first == {"moved": ["mcp.gh.GITHUB_TOKEN"], "skipped": []}
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == "ghp_planted"
    gh = _entry(platform, "gh")
    assert gh["env"] == {"PORT": "1"}
    assert gh["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}
    assert gh["args"] == ["-y", "x"], "the rest of the entry is untouched"
    assert _entry(platform, "plain") == {"name": "plain", "command": "npx", "env": {"REGION": "us"}}
    text = _toml_text(platform)
    assert "ghp_planted" not in text
    assert tomllib.loads(text)["mcp_servers"][0]["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}
    # The log names the row, never the value.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "mcp.gh.GITHUB_TOKEN" in joined
    assert "ghp_planted" not in joined

    # Idempotent: the second run moves nothing and changes nothing.
    before = json.dumps(platform.config.mcp_servers, sort_keys=True)
    mtime = (Path(platform.config.home) / "config.toml").stat().st_mtime_ns
    second = migrate_mcp_env_secrets(platform)
    assert second == {"moved": [], "skipped": []}
    assert json.dumps(platform.config.mcp_servers, sort_keys=True) == before
    assert (Path(platform.config.home) / "config.toml").stat().st_mtime_ns == mtime
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == "ghp_planted"


def test_migration_accepts_config_and_secrets_without_a_platform(client):
    """``build_platform`` calls it before the platform object exists."""
    platform = client.app.state.platform
    _plant_plain_token(platform, "ghp_parts")
    out = migrate_mcp_env_secrets(config=platform.config, secrets=platform.secrets)
    assert out["moved"] == ["mcp.gh.GITHUB_TOKEN"]
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == "ghp_parts"
    assert "ghp_parts" not in _toml_text(platform)


def test_migration_leaves_an_entry_untouched_when_the_vault_refuses(client, monkeypatch, caplog):
    platform = client.app.state.platform
    _plant_plain_token(platform, "ghp_stays")

    def _refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(platform.secrets, "set", _refuse)
    with caplog.at_level(logging.WARNING, logger="iron_jarvis"):
        out = migrate_mcp_env_secrets(platform)
    assert out["moved"] == []
    assert out["skipped"] == ["gh.GITHUB_TOKEN: OSError"]
    # Untouched means untouched: the value is still where it was (not lost),
    # the file still carries it, and nothing was half-written.
    assert _entry(platform, "gh")["env"] == {"GITHUB_TOKEN": "ghp_stays", "PORT": "1"}
    assert "env_secrets" not in _entry(platform, "gh")
    assert "ghp_stays" in _toml_text(platform)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "GITHUB_TOKEN" in joined and "OSError" in joined
    assert "ghp_stays" not in joined


def test_migration_with_no_vault_logs_why_and_touches_nothing(client, caplog):
    platform = client.app.state.platform
    _plant_plain_token(platform, "ghp_novault")
    with caplog.at_level(logging.WARNING, logger="iron_jarvis"):
        out = migrate_mcp_env_secrets(config=platform.config, secrets=None)
    assert out["moved"] == []
    assert out["skipped"] == ["gh.GITHUB_TOKEN: no vault"]
    assert "ghp_novault" in _toml_text(platform)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "no vault" in joined and "ghp_novault" not in joined


def test_migration_is_a_no_op_on_an_empty_config(client):
    platform = client.app.state.platform
    platform.config.mcp_servers = []
    assert migrate_mcp_env_secrets(platform) == {"moved": [], "skipped": []}
    assert not (Path(platform.config.home) / "config.toml").exists() or "mcp_servers" not in _toml_text(
        platform
    ) or tomllib.loads(_toml_text(platform)).get("mcp_servers", []) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "lane C needs: build_platform must call "
        "migrate_mcp_env_secrets(config=config, secrets=secrets) right before "
        "`_mcp_configs = [...]` (platform.py, before mark_starting / the sync "
        "mcp_tools load). Remove this marker once the integrator places it."
    ),
)
def test_boot_migrates_a_planted_plain_token_before_packs_load(tmp_path):
    first = TestClient(create_app(str(tmp_path)))
    platform = first.app.state.platform
    _plant_plain_token(platform, "ghp_boot")
    first.close()

    second = TestClient(create_app(str(tmp_path)))
    p2 = second.app.state.platform
    assert p2.secrets.get("mcp.gh.GITHUB_TOKEN") == "ghp_boot"
    assert _entry(p2, "gh")["env"] == {"PORT": "1"}
    assert "ghp_boot" not in _toml_text(p2)
    assert "ghp_boot" not in second.get("/mcp/servers").text


# --------------------------------------------------------------------------- #
# DELETE /mcp/servers/{name}: the vault rows and the manifest go with it.
# --------------------------------------------------------------------------- #
def test_delete_removes_the_vault_rows_and_the_manifest(client):
    platform = client.app.state.platform
    client.post(
        "/mcp/servers",
        json={"name": "gh", "command": NO_SUCH_COMMAND, "env": {"GITHUB_TOKEN": TOKEN, "API_KEY": "k2"}},
    )
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN
    assert platform.secrets.get("mcp.gh.API_KEY") == "k2"
    # A manifest as a first load would have written it, plus a stray vault
    # row under the pack's prefix (what a failed earlier delete leaves).
    manifest = PackManifest(platform.config.home)
    manifest.record("gh", [{"name": "list_repos", "description": "", "input_schema": {}}])
    assert manifest.path("gh").is_file()
    platform.secrets.set("mcp.gh.STRAY_TOKEN", "stray")
    # A neighbour pack's row must survive.
    platform.secrets.set("mcp.other.TOKEN", "keep")

    res = client.delete("/mcp/servers/gh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["removed"] == "gh"
    assert body["secrets_removed"] == 3
    assert body["manifest_removed"] is True
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") is None
    assert platform.secrets.get("mcp.gh.API_KEY") is None
    assert platform.secrets.get("mcp.gh.STRAY_TOKEN") is None
    assert platform.secrets.get("mcp.other.TOKEN") == "keep"
    assert not manifest.path("gh").exists()
    assert platform.config.mcp_servers == []
    assert "gh" not in [r["name"] for r in client.get("/mcp/servers").json()["servers"]]
    # A re-add is a first load again: no manifest, a fresh vault row.
    res = client.post(
        "/mcp/servers", json={"name": "gh", "command": NO_SUCH_COMMAND, "env": {"GITHUB_TOKEN": "ghp_2"}}
    )
    assert res.status_code == 200
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == "ghp_2"
    assert manifest.load("gh") is None


def test_delete_pack_secrets_only_touches_the_packs_own_prefix(client):
    platform = client.app.state.platform
    platform.secrets.set("mcp.gh.TOKEN", "a")
    platform.secrets.set("mcp.ghx.TOKEN", "b")
    platform.secrets.set("conn_github_token", "c")
    removed = delete_pack_secrets(platform.secrets, "gh", {"env_secrets": {"TOKEN": "mcp.gh.TOKEN"}})
    assert removed == ["mcp.gh.TOKEN"]
    assert platform.secrets.get("mcp.ghx.TOKEN") == "b"
    assert platform.secrets.get("conn_github_token") == "c"
