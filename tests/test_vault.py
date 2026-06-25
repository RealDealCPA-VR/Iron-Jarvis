from __future__ import annotations

import pytest

from iron_jarvis.providers.vault import BrowserVault


def test_store_load_roundtrip_is_encrypted(tmp_path):
    vault = BrowserVault(tmp_path / "browser")
    vault.store("claude", {"cookies": [{"name": "sid", "value": "abc"}]})

    assert vault.has_session("claude")
    loaded = vault.load("claude")
    assert loaded["cookies"][0]["value"] == "abc"

    # on-disk blob must not contain the plaintext value
    blob = (tmp_path / "browser" / "claude" / "session.enc").read_bytes()
    assert b"abc" not in blob


def test_vault_refuses_secret_like_keys(tmp_path):
    vault = BrowserVault(tmp_path / "browser")
    with pytest.raises(ValueError):
        vault.store("claude", {"password": "hunter2"})


def test_providers_listing(tmp_path):
    vault = BrowserVault(tmp_path / "browser")
    names = {p["provider"] for p in vault.providers()}
    assert {"claude", "chatgpt", "gemini"} <= names
    assert all(p["logged_in"] is False for p in vault.providers())
