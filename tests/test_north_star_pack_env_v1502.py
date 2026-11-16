"""v1.502.0 (north-star plan 2.2): an app pack starts with a CLEAN environment.

THE DEFECT. ``mcp/tools._build_transport`` started every third-party pack
with the daemon's whole ``os.environ`` merged under the pack's own settings
(and with ``env=None``, inheriting it outright, when the pack had none). The
install token (``IRONJARVIS_TOKEN``, which unlocks the whole daemon) and any
provider key the daemon was started with went to a program someone else
wrote. Only a ``locked`` pack (Microsoft 365) was cleaned.

THE RULE. Every stdio pack's environment is ``sandbox/native.build_env`` (the
one drop list a coding session's commands already get: secrets by name, the
vendors' and this app's own variables, a URL's sign-in) with the pack's own
``env`` and ``env_secrets`` added AFTER it, so what the user gave the pack is
always there. A locked pack is then cleaned of its own settings as before.
PATH, the home folders, APPDATA and proxy settings stay. No value below is
shaped like a real credential.
"""

from __future__ import annotations

import sys

import pytest

from iron_jarvis.mcp import tools as mcp_tools

STRAYS = {
    "IRONJARVIS_TOKEN": "install-bearer-for-the-test",
    "IRONJARVIS_MCP_TOKEN": "another-pane-for-the-test",
    "ANTHROPIC_API_KEY": "not-a-real-key",
    "OPENAI_API_KEY": "not-a-real-key",
    "SOME_VENDOR_SECRET": "not-a-real-secret",
}
KEPT = {
    "HTTPS_PROXY": "http://proxy.example.test:8080",
    "NO_PROXY": "localhost,127.0.0.1",
    "APPDATA": "C:/Users/someone/AppData/Roaming",
    "NPM_CONFIG_REGISTRY": "https://registry.example.test/",
}


def _env_of(cfg, resolver=None) -> dict[str, str]:
    transport = mcp_tools._build_transport(cfg, resolver)
    return {str(k).upper(): str(v) for k, v in dict(transport.env or {}).items()}


@pytest.fixture()
def daemon_env(monkeypatch):
    for k, v in {**STRAYS, **KEPT}.items():
        monkeypatch.setenv(k, v)


def test_a_pack_with_settings_gets_no_install_token_and_no_provider_key(daemon_env):
    env = _env_of({"name": "notes", "command": sys.executable, "args": ["-c", "pass"],
                   "env": {"NOTES_DIR": "C:/notes"}})
    for name in STRAYS:
        assert name not in env, f"{name} reached a third-party pack"
    assert env["NOTES_DIR"] == "C:/notes"
    for name, value in KEPT.items():
        assert env.get(name) == value, name
    assert env.get("PATH"), "PATH must survive or npx/uvx cannot start"


def test_a_pack_with_no_settings_does_not_inherit_the_daemons_environment(daemon_env):
    """``env=None`` used to mean "inherit everything"."""
    transport = mcp_tools._build_transport(
        {"name": "plain", "command": sys.executable, "args": ["-c", "pass"]}, None,
    )
    assert transport.env is not None, "None inherits the daemon's whole environment"
    env = {str(k).upper() for k in transport.env}
    assert not (env & set(STRAYS)), env & set(STRAYS)
    assert "PATH" in env


def test_the_packs_own_secret_is_added_after_the_clean(daemon_env):
    """A key the USER gave this pack (vault name -> value) still reaches it,
    even when its name is one the drop list removes from the daemon's."""
    vault = {"mcp.notes.NOTES_API_KEY": "the-users-own-value"}
    env = _env_of(
        {"name": "notes", "command": sys.executable, "args": ["-c", "pass"],
         "env": {"OPENAI_API_KEY": "the-users-choice-for-this-pack"},
         "env_secrets": {"NOTES_API_KEY": "mcp.notes.NOTES_API_KEY"}},
        vault.get,
    )
    assert env["NOTES_API_KEY"] == "the-users-own-value"
    assert env["OPENAI_API_KEY"] == "the-users-choice-for-this-pack"
    assert "IRONJARVIS_TOKEN" not in env


def test_a_locked_pack_is_still_cleaned_of_its_own_settings(daemon_env, monkeypatch):
    monkeypatch.setenv("MS365_MCP_OAUTH_TOKEN", "stray-for-the-test")
    env = _env_of({"name": "microsoft_365", "command": sys.executable, "args": ["-c", "pass"],
                   "locked": "ms365", "env": {"MS365_MCP_CLOUD_TYPE": "global"}})
    assert "MS365_MCP_OAUTH_TOKEN" not in env
    assert "IRONJARVIS_TOKEN" not in env
    assert env["MS365_MCP_CLOUD_TYPE"] == "global"
