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
    method: str  # "api_key" | "oauth" | "browser"
    auth_url: str = ""
    token_url: str = ""
    scopes: list[str] = field(default_factory=list)
    docs_url: str = ""
    key_help: str = ""
    key_secret_name: str = ""


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
        method="api_key",
        docs_url="https://console.anthropic.com/settings/keys",
        key_help="Get a key at console.anthropic.com",
        key_secret_name="anthropic_api_key",
    ),
    "openai": ConnectionSpec(
        provider="openai",
        display_name="OpenAI",
        method="api_key",
        docs_url="https://platform.openai.com/api-keys",
        key_help="Get a key at platform.openai.com/api-keys",
        key_secret_name="openai_api_key",
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
