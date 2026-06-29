"""Connection specs — the static catalog of connectable LLM providers.

A :class:`ConnectionSpec` declares *how* a provider is connected (API key vs
OAuth 2.0 + PKCE vs browser session), the public endpoints needed for OAuth, and
human-facing help text. Specs carry **no** secret values — only the *name* of the
vault entry a credential is stored under. The actual credential always lives in
the encrypted :class:`~iron_jarvis.secrets.manager.SecretsManager`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Connection methods understood by the registry.
METHODS = ("api_key", "oauth", "browser")


@dataclass
class ConnectionSpec:
    """Declarative description of a connectable provider (no secrets)."""

    provider: str
    display_name: str
    method: str  # "api_key" | "oauth" | "browser" — the PRIMARY connect method
    auth_url: str = ""
    token_url: str = ""
    scopes: list[str] = field(default_factory=list)
    docs_url: str = ""
    key_help: str = ""
    key_secret_name: str = ""
    #: Embedded PUBLIC OAuth client id (a native/PKCE app id shipped by the
    #: provider's own CLI) so a user can "log in with their account" with NO app
    #: registration. Overridable via the ``<provider>_oauth_client_id`` secret.
    oauth_client_id: str = ""
    #: Help text for the account-login (OAuth) option, when distinct from key_help.
    oauth_help: str = ""

    @property
    def supports_oauth(self) -> bool:
        """True when this provider can be connected by an OAuth account login.
        The client id may be embedded here (a public CLI app id) OR supplied by
        the registry's ``oauth_app`` resolver, so only the endpoints are required."""
        return bool(self.auth_url and self.token_url)

    @property
    def supports_api_key(self) -> bool:
        return bool(self.key_secret_name)


def generic_oauth_spec(
    provider: str,
    *,
    auth_url: str,
    token_url: str,
    display_name: str = "",
    scopes: list[str] | None = None,
    docs_url: str = "",
) -> ConnectionSpec:
    """Build an OAuth :class:`ConnectionSpec` for an arbitrary provider.

    Lets callers wire up any standards-compliant OAuth 2.0 + PKCE provider
    without hard-coding it into :data:`BUILTIN_SPECS`.
    """

    return ConnectionSpec(
        provider=provider,
        display_name=display_name or provider.replace("_", " ").title(),
        method="oauth",
        auth_url=auth_url,
        token_url=token_url,
        scopes=list(scopes or []),
        docs_url=docs_url,
    )


#: Built-in, ready-to-connect providers.
BUILTIN_SPECS: dict[str, ConnectionSpec] = {
    "anthropic": ConnectionSpec(
        provider="anthropic",
        display_name="Anthropic (Claude)",
        method="api_key",  # also supports OAuth account login (see oauth_* below)
        docs_url="https://console.anthropic.com/settings/keys",
        key_help="Get a key at console.anthropic.com",
        key_secret_name="anthropic_api_key",
        # Log in with a Claude Pro/Max account via the public Claude Code OAuth
        # client (PKCE, no secret). The minted token (sk-ant-oat...) calls the
        # Messages API with the oauth beta header (see the Anthropic adapter).
        auth_url="https://claude.ai/oauth/authorize",
        token_url="https://console.anthropic.com/v1/oauth/token",
        scopes=["org:create_api_key", "user:profile", "user:inference"],
        oauth_client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        oauth_help="Log in with your Claude Pro/Max account (no API key needed).",
    ),
    "openai": ConnectionSpec(
        provider="openai",
        display_name="OpenAI",
        method="api_key",  # also supports OAuth account login (ChatGPT / Codex)
        docs_url="https://platform.openai.com/api-keys",
        key_help="Get a key at platform.openai.com/api-keys (recommended — works for inference today).",
        key_secret_name="openai_api_key",
        # Account login via the public Codex CLI OAuth client (PKCE) is EXPERIMENTAL:
        # a ChatGPT/Codex OAuth token is minted at auth.openai.com and is NOT accepted
        # by api.openai.com/v1/chat/completions (the OpenAI adapter has no separate
        # ChatGPT-backend path yet), so it will fail the connection Test and fall back
        # to mock. Use an API key for reliable OpenAI inference until that path lands.
        auth_url="https://auth.openai.com/oauth/authorize",
        token_url="https://auth.openai.com/oauth/token",
        scopes=["openid", "profile", "email", "offline_access"],
        oauth_client_id="app_EMoamEEZ73f0CkXaXp7hrann",
        oauth_help="Experimental — ChatGPT account login may not run inference yet; use an API key for reliable OpenAI access.",
    ),
    "xai": ConnectionSpec(
        provider="xai",
        display_name="xAI (Grok)",
        method="api_key",  # xAI uses API keys today (OpenAI-compatible api.x.ai)
        docs_url="https://console.x.ai",
        key_help="Get a key at console.x.ai",
        key_secret_name="xai_api_key",
        # OAuth-READY: leave auth_url/token_url/oauth_client_id unset so this is
        # key-only for now. The moment xAI publishes a public OAuth/PKCE client,
        # set those three here (or override via the xai_oauth_client_id secret) and
        # "Log in with your account" lights up through the SAME registry path used
        # by Anthropic/OpenAI — no other code change needed.
        oauth_help="xAI uses an API key today; account login activates once xAI ships a public OAuth client.",
    ),
    "google": ConnectionSpec(
        provider="google",
        display_name="Google (Gemini)",
        method="oauth",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            # Authorizes generateContent on the Generative Language API. The
            # ``.retriever`` scope only covers semantic retrieval, so an access
            # token minted with it is rejected (401) by generateContent.
            "https://www.googleapis.com/auth/generative-language",
            "openid",
            "email",
        ],
        docs_url="https://console.cloud.google.com/apis/credentials",
        key_help="Create an OAuth 2.0 Client ID in Google Cloud Console.",
    ),
    "mock": ConnectionSpec(
        provider="mock",
        display_name="Mock (offline)",
        method="api_key",
        key_help="No key required — always connectable for offline testing.",
        key_secret_name="mock_api_key",
    ),
}
