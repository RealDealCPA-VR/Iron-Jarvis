"""Account-login OAuth for Anthropic + OpenAI (subscription login, not just keys)."""

from __future__ import annotations

from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter


def test_anthropic_supports_both_oauth_and_key(tmp_path):
    reg = build_platform(str(tmp_path)).connections
    spec = reg.get_spec("anthropic")
    assert spec.supports_oauth and spec.supports_api_key
    assert spec.oauth_client_id  # embedded PUBLIC client id (no app registration)
    assert "claude.ai" in spec.auth_url
    # Manual-code flow: the console callback is the ONLY redirect the public
    # Claude Code client accepts from a third-party app; the daemon's
    # localhost:8787 callback is hard-rejected by claude.ai.
    assert spec.oauth_manual_code is True
    assert spec.oauth_redirect_uri == "https://console.anthropic.com/oauth/code/callback"
    assert spec.oauth_token_format == "json"


def test_openai_oauth_uses_codex_loopback_and_key_mint(tmp_path):
    # The public Codex client ONLY accepts its registered loopback redirect
    # (localhost:1455) — anything else fails at auth.openai.com with
    # authorize_hydra_invalid_request — and its account token can't call
    # api.openai.com, so the flow must mint a REAL API key afterwards.
    reg = build_platform(str(tmp_path)).connections
    spec = reg.get_spec("openai")
    assert spec.supports_oauth and spec.supports_api_key
    assert spec.oauth_redirect_uri == "http://localhost:1455/auth/callback"
    assert spec.oauth_key_exchange is True
    # The daemon must bind the one-shot loopback listener for openai only:
    assert reg.loopback_redirect("openai") == (1455, "/auth/callback")
    assert reg.loopback_redirect("anthropic") is None  # manual-code — no listener
    assert reg.loopback_redirect("google") is None  # custom app -> daemon callback

    out = reg.start_oauth("openai")
    url = out["authorization_url"]
    assert out["manual_code"] is False
    assert "auth.openai.com/oauth/authorize" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" in url
    assert "codex_cli_simplified_flow=true" in url
    assert "id_token_add_organizations=true" in url


def test_start_oauth_uses_embedded_public_client(tmp_path):
    out = build_platform(str(tmp_path)).connections.start_oauth("anthropic")
    assert out["state"]
    assert out["manual_code"] is True  # UI shows the paste-code box
    url = out["authorization_url"]
    assert "claude.ai/oauth/authorize" in url
    assert "9d1c250a-e61b-44d9-88ed-5944d1962f5e" in url  # the public Claude Code client
    assert "code_challenge=" in url and "code_challenge_method=S256" in url  # PKCE
    # The REGISTERED redirect (urlencoded console callback) — not localhost:8787.
    assert "redirect_uri=https%3A%2F%2Fconsole.anthropic.com%2Foauth%2Fcode%2Fcallback" in url
    assert "localhost" not in url
    assert "code=true" in url  # ask claude.ai to DISPLAY the code (manual flow)
    # Google-isms must not leak into Anthropic's authorize request.
    assert "access_type=" not in url and "prompt=" not in url


def test_connections_listing_carries_manual_code_flag(tmp_path):
    listing = {s["provider"]: s for s in build_platform(str(tmp_path)).connections.status()}
    assert listing["anthropic"]["oauth_manual_code"] is True
    assert listing["anthropic"]["supports_oauth"] is True
    assert listing["openai"]["supports_oauth"] is True  # Codex loopback flow
    assert listing["openai"]["oauth_manual_code"] is False  # redirect, not paste


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
