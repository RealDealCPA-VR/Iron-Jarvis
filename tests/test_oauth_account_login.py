"""Account-login OAuth for Anthropic + OpenAI (subscription login, not just keys)."""

from __future__ import annotations

from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter


def test_anthropic_and_openai_support_both_oauth_and_key(tmp_path):
    reg = build_platform(str(tmp_path)).connections
    for prov, auth_host in (("anthropic", "claude.ai"), ("openai", "auth.openai.com")):
        spec = reg.get_spec(prov)
        assert spec.supports_oauth and spec.supports_api_key
        assert spec.oauth_client_id  # embedded PUBLIC client id (no app registration)
        assert auth_host in spec.auth_url


def test_start_oauth_uses_embedded_public_client(tmp_path):
    out = build_platform(str(tmp_path)).connections.start_oauth("anthropic")
    assert out["state"]
    url = out["authorization_url"]
    assert "claude.ai/oauth/authorize" in url
    assert "9d1c250a-e61b-44d9-88ed-5944d1962f5e" in url  # the public Claude Code client
    assert "code_challenge=" in url and "code_challenge_method=S256" in url  # PKCE


def test_credential_prefers_oauth_token_then_falls_back_to_key(tmp_path):
    p = build_platform(str(tmp_path))
    reg, sec = p.connections, p.secrets
    sec.set("anthropic_api_key", "sk-ant-api03-key", kind="api_key")
    assert reg.credential("anthropic") == "sk-ant-api03-key"  # only a key -> the key
    assert reg.has_credential("anthropic")
    sec.set_oauth("anthropic_oauth", {"access_token": "sk-ant-oat01-token"})
    assert reg.credential("anthropic") == "sk-ant-oat01-token"  # OAuth wins when present


def test_api_key_still_settable_on_oauth_capable_provider(tmp_path):
    rec = build_platform(str(tmp_path)).connections.set_api_key("openai", "sk-proj-abc")
    assert rec.status == "connected"


def test_disconnect_clears_both_credentials(tmp_path):
    p = build_platform(str(tmp_path))
    reg, sec = p.connections, p.secrets
    sec.set("anthropic_api_key", "sk-ant-api03-key", kind="api_key")
    sec.set_oauth("anthropic_oauth", {"access_token": "sk-ant-oat01-token"})
    reg.disconnect("anthropic")
    assert reg.credential("anthropic") is None
    assert not reg.has_credential("anthropic")


def test_anthropic_adapter_oauth_token_uses_bearer_not_api_key():
    a = AnthropicAdapter(credential=lambda: "sk-ant-oat01-abc")
    client = a._client()
    assert getattr(client, "auth_token", None) == "sk-ant-oat01-abc"  # Bearer
    assert not client.api_key  # NOT sent as x-api-key


def test_anthropic_adapter_api_key_uses_x_api_key():
    a = AnthropicAdapter(credential=lambda: "sk-ant-api03-xyz")
    client = a._client()
    assert client.api_key == "sk-ant-api03-xyz"
    assert not getattr(client, "auth_token", None)
