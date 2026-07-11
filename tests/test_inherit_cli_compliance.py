"""Compliance fix: Anthropic/OpenAI are API-key-only + inherit the logged-in
CLI. The app no longer mints its own account OAuth token, and the raw Messages
API is never handed a subscription (sk-ant-oat) token. Google/Dropbox/etc.
keep their (user-registered) OAuth. See providers/adapters/subprocess_cli.py,
providers/manager.py (inherit alias), connections/registry.py (migration).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.connections.specs import BUILTIN_SPECS
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.subprocess_cli import (
    ClaudeCliAdapter,
    make_claude_cli,
    _claude_model_arg,
)
from iron_jarvis.providers.manager import ProviderManager


# --- specs: anthropic/openai are API-key-only; no in-app account login --------

def test_anthropic_openai_are_api_key_only_no_oauth():
    for prov in ("anthropic", "openai"):
        spec = BUILTIN_SPECS[prov]
        assert spec.method == "api_key"
        assert spec.supports_api_key is True
        # The embedded-public-client OAuth flow is GONE: no auth/token url, no
        # client id, no manual-code flow, no key-exchange.
        assert spec.supports_oauth is False
        assert not spec.auth_url and not spec.token_url
        assert not spec.oauth_client_id
        assert spec.oauth_manual_code is False
        assert spec.oauth_key_exchange is False


def test_user_registered_oauth_providers_are_untouched():
    # Memory/Gemini providers connect with the user's OWN registered OAuth app —
    # that's standard and stays.
    for prov in ("google", "dropbox", "google_drive", "onedrive"):
        spec = BUILTIN_SPECS.get(prov)
        assert spec is not None and spec.supports_oauth is True


# --- raw adapter: API keys only; a subscription token is rejected -------------

def test_raw_anthropic_adapter_rejects_subscription_oauth_token():
    with pytest.raises(RuntimeError, match="OAuth account tokens are not used"):
        AnthropicAdapter(api_key="sk-ant-oat-abc123")._client()


def test_raw_anthropic_adapter_still_accepts_api_key():
    # The true API path is unchanged: a normal key builds a client without error.
    client = AnthropicAdapter(api_key="sk-ant-api-abc123")._client()
    assert client is not None


# --- manager: keyless Claude/OpenAI inherit the CLI; a key keeps the raw path -

def _mgr(**kw):
    m = ProviderManager(inherit_cli_logins=True, **kw)
    # Force claude present, codex absent, deterministically.
    m._cli_binary_present = staticmethod(lambda b: b == "claude")
    return m


def test_keyless_anthropic_resolves_to_claude_cli():
    m = _mgr()
    assert m.available("anthropic") is True  # via inherited CLI
    assert isinstance(m.get("anthropic", "claude-opus-4-8"), ClaudeCliAdapter)


def test_api_key_keeps_raw_adapter():
    m = _mgr(
        credential_resolver=lambda n: "sk-ant-api-xyz" if n == "anthropic" else None,
        presence_resolver=lambda n: n == "anthropic",
    )
    assert isinstance(m.get("anthropic", "claude-opus-4-8"), AnthropicAdapter)


def test_inherit_is_opt_in_bare_manager_stays_hermetic():
    # Without the flag, availability never depends on a local CLI binary.
    m = ProviderManager()  # inherit off
    m._cli_binary_present = staticmethod(lambda b: True)
    assert m.available("anthropic") is False


def test_model_arg_mapping():
    assert _claude_model_arg("claude-opus-4-8") == "opus"
    assert _claude_model_arg("claude-sonnet-5") == "sonnet"
    assert _claude_model_arg("haiku") == "haiku"
    assert _claude_model_arg("subscription") is None


# --- upgraded claude-cli adapter: single-step structured tool-calls -----------

def test_claude_cli_adapter_returns_structured_tool_call():
    def runner(argv, stdin=None):
        assert "--tools" in argv and "--json-schema" in argv
        return 0, json.dumps({
            "is_error": False,
            "usage": {"input_tokens": 12, "output_tokens": 3},
            "structured_output": {
                "reply": None,
                "tool_call": {"name": "read_file", "arguments": {"path": "a.txt"}},
            },
        }), ""

    a = make_claude_cli(model="claude-opus-4-8", runner=runner, which=lambda b: "claude")
    resp = asyncio.run(a.complete(
        system="sys",
        messages=[LLMMessage(role="user", content="read a.txt")],
        tools=[{"name": "read_file", "description": "read a file",
                "input_schema": {"type": "object",
                                 "properties": {"path": {"type": "string"}},
                                 "required": ["path"]}}],
    ))
    assert resp.wants_tools and resp.finish_reason == "tool_use"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "a.txt"}
    assert resp.usage["input_tokens"] == 12


def test_claude_cli_adapter_returns_final_text():
    def runner(argv, stdin=None):
        return 0, json.dumps({
            "is_error": False,
            "structured_output": {"reply": "all done", "tool_call": None},
        }), ""

    a = make_claude_cli(runner=runner, which=lambda b: "claude")
    resp = asyncio.run(a.complete(
        system="", messages=[LLMMessage(role="user", content="hi")],
        tools=[{"name": "x", "description": "", "input_schema": {"type": "object"}}],
    ))
    assert not resp.wants_tools and resp.text == "all done"


def test_claude_cli_adapter_raises_on_not_logged_in():
    def runner(argv, stdin=None):
        return 0, json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}), ""

    a = make_claude_cli(runner=runner, which=lambda b: "claude")
    with pytest.raises(RuntimeError, match="Not logged in"):
        asyncio.run(a.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))


def test_claude_cli_adapter_rejects_images_honestly():
    a = make_claude_cli(runner=lambda *a, **k: (0, "{}", ""), which=lambda b: "claude")
    with pytest.raises(RuntimeError, match="image"):
        asyncio.run(a.complete(
            system="",
            messages=[LLMMessage(role="user", content="what is this",
                                 images=[{"data_b64": "x", "media_type": "image/png"}])],
            tools=[],
        ))


# --- migration: existing minted tokens are purged -----------------------------

class _FakeSecrets:
    def __init__(self, oauth=None, keys=None):
        self._oauth = dict(oauth or {})
        self._keys = dict(keys or {})

    def get_oauth(self, name):
        return self._oauth.get(name)

    def get(self, name):
        return self._keys.get(name)

    def delete(self, name):
        self._oauth.pop(name, None)
        self._keys.pop(name, None)

    def set_oauth(self, name, val):
        self._oauth[name] = val

    def set(self, name, val, kind="api_key"):
        self._keys[name] = val


def test_migration_purges_minted_oauth(tmp_path):
    from sqlmodel import SQLModel, create_engine
    from iron_jarvis.connections.registry import ConnectionRegistry
    from iron_jarvis.connections import models as _m  # noqa: F401 — register tables

    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    SQLModel.metadata.create_all(engine)
    secrets = _FakeSecrets(oauth={"anthropic_oauth": {"access_token": "sk-ant-oat-x"}})
    reg = ConnectionRegistry(engine, secrets)
    # Simulate a pre-migration OAuth connection row.
    reg._upsert("anthropic", method="oauth", status="connected", account="a@b.com")

    purged = reg.purge_app_minted_oauth()
    assert "anthropic" in purged
    assert secrets.get_oauth("anthropic_oauth") is None
    row = reg._get_record("anthropic")
    assert row is not None and row.status == "disconnected"
    # Idempotent second run.
    assert reg.purge_app_minted_oauth() == []
