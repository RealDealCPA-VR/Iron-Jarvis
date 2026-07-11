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
    #: Redirect URI REGISTERED for the embedded public client. OAuth servers
    #: hard-reject any unregistered redirect_uri ("Redirect URI ... is not
    #: supported by client"), so when riding a provider's own public CLI client
    #: this MUST be one of ITS registered values — the daemon's localhost
    #: callback only works for user-registered custom apps.
    oauth_redirect_uri: str = ""
    #: Provider-specific extra query params for the authorization URL (e.g.
    #: Google's ``access_type``/``prompt``, Anthropic's ``code=true``
    #: manual-code switch). NOT hardcoded in the OAuth client — Google-isms sent
    #: to other providers can invalidate the whole authorize request.
    oauth_extra_auth_params: dict[str, str] = field(default_factory=dict)
    #: Body encoding for token exchange/refresh: "form" (the RFC 6749 default)
    #: or "json" (Anthropic's console token endpoint).
    oauth_token_format: str = "form"
    #: Manual-code flow: instead of redirecting to a local callback, the provider
    #: shows the user an authorization code (``code#state``) to paste back into
    #: the Connections page.
    oauth_manual_code: bool = False
    #: After the code exchange, mint a REAL API key from the login's ``id_token``
    #: via the RFC 8693 token-exchange grant (the OpenAI Codex flow) and store
    #: THAT as the credential — the account's access token itself is NOT accepted
    #: by the provider's inference API.
    oauth_key_exchange: bool = False

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
    # Anthropic and OpenAI are API-KEY-ONLY here. The subscription (Claude
    # Pro/Max, ChatGPT/Codex) is used the SANCTIONED way — by inheriting the
    # login you already performed in the provider's own CLI (`claude` / `codex`),
    # never by this app minting its own account token. So there is deliberately
    # NO embedded-public-client OAuth flow on these two cards: the app never runs
    # a claude.ai / ChatGPT login on your behalf. See providers/adapters/
    # subprocess_cli.py (the inherited-CLI adapters) and the router alias that
    # routes a keyless Claude/OpenAI request to the logged-in CLI.
    "anthropic": ConnectionSpec(
        provider="anthropic",
        display_name="Anthropic (Claude)",
        method="api_key",
        docs_url="https://console.anthropic.com/settings/keys",
        key_help=(
            "Paste an Anthropic API key (sk-ant-…) for direct API use. Or skip "
            "this: if you're logged into the Claude CLI (`claude`) on this "
            "machine, Iron Jarvis inherits that subscription login automatically "
            "— no key needed."
        ),
        key_secret_name="anthropic_api_key",
    ),
    "openai": ConnectionSpec(
        provider="openai",
        display_name="OpenAI",
        method="api_key",
        docs_url="https://platform.openai.com/api-keys",
        key_help=(
            "Paste an OpenAI API key (sk-…) for direct API use. Or skip this: if "
            "you're logged into the Codex CLI (`codex`) on this machine, Iron "
            "Jarvis inherits that ChatGPT subscription login automatically — no "
            "key needed."
        ),
        key_secret_name="openai_api_key",
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
        # Google-specific authorize params (moved out of the generic OAuth client):
        # offline access_type + forced consent are what make Google return a
        # refresh_token; other providers reject or ignore these.
        oauth_extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
    "openrouter": ConnectionSpec(
        provider="openrouter",
        display_name="OpenRouter",
        method="api_key",  # one key routes every lab's models (OpenAI-compatible)
        docs_url="https://openrouter.ai/settings/keys",
        key_help=(
            "Get a key at openrouter.ai — one key routes hundreds of models "
            "from every lab (model ids are namespaced, e.g. "
            "x-ai/grok-code-fast-1 or openrouter/auto)."
        ),
        key_secret_name="openrouter_api_key",
    ),
    "custom": ConnectionSpec(
        provider="custom",
        display_name="Custom endpoint",
        method="api_key",
        key_help=(
            "Any OpenAI-compatible inference API: Ollama Cloud "
            "(https://ollama.com), LM Studio, vLLM, llama.cpp server, a private "
            "gateway... Set the base URL in Settings (custom_base_url) + the "
            "default model (custom_model); the key here is OPTIONAL — keyless "
            "local servers work without connecting one."
        ),
        key_secret_name="custom_api_key",
    ),
    # --- Creative media (NOT an LLM) ------------------------------------------
    # Pixio powers the pixio_* agent tools + the Creative gallery's publish
    # button. The secret name is the bare "pixio" the tools already resolve.
    "pixio": ConnectionSpec(
        provider="pixio",
        display_name="Pixio (creative media)",
        method="api_key",
        docs_url="https://beta.pixio.myapps.ai",
        key_help=(
            "Generate images, video, and audio (the Creative gallery + the "
            "pixio_* agent tools). Keys look like pxio_live_… — get one at "
            "beta.pixio.myapps.ai."
        ),
        key_secret_name="pixio",
    ),
    "mock": ConnectionSpec(
        provider="mock",
        display_name="Mock (offline)",
        method="api_key",
        key_help="No key required — always connectable for offline testing.",
        key_secret_name="mock_api_key",
    ),
    # --- Cloud STORAGE providers (memory/RAG sources, NOT LLMs) --------------
    # Connected as OAuth apps so a Drive/OneDrive/Dropbox folder can be a
    # long-term-memory source (see ltm/sources.py CLOUD_DRIVE_KINDS). They use a
    # DISTINCT provider id from any LLM (e.g. Gemini's "google") so credentials
    # never collide. There's no public CLI client to ride for storage, so the
    # user registers their own OAuth app (a "Desktop"/"Mobile & desktop" client
    # that permits a localhost redirect) and supplies its client id/secret via
    # the <provider>_oauth_client_id / _oauth_client_secret vault entries — the
    # daemon then rides its own localhost callback + refreshes tokens as usual.
    "google_drive": ConnectionSpec(
        provider="google_drive",
        display_name="Google Drive (memory)",
        method="oauth",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/drive", "openid", "email"],
        docs_url="https://console.cloud.google.com/apis/credentials",
        oauth_help=(
            "Connect a Google Drive to search + ingest its files into memory. "
            "Register a 'Desktop app' OAuth client in Google Cloud Console and "
            "paste its client id/secret."
        ),
        # offline access_type + forced consent are what make Google return a
        # refresh_token (so the connection survives token expiry).
        oauth_extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
    "onedrive": ConnectionSpec(
        provider="onedrive",
        display_name="OneDrive (memory)",
        method="oauth",
        auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        # offline_access is what yields a refresh_token on Microsoft identity.
        scopes=["Files.ReadWrite", "offline_access", "openid"],
        docs_url="https://portal.azure.com",
        oauth_help=(
            "Connect OneDrive to search + ingest its files into memory. Register "
            "an app in Entra ID (Azure AD) with a 'Mobile and desktop' platform "
            "and a http://localhost redirect, then paste its client id/secret."
        ),
    ),
    "dropbox": ConnectionSpec(
        provider="dropbox",
        display_name="Dropbox (memory)",
        method="oauth",
        auth_url="https://www.dropbox.com/oauth2/authorize",
        token_url="https://api.dropboxapi.com/oauth2/token",
        scopes=[
            "files.metadata.read",
            "files.content.read",
            "files.content.write",
        ],
        docs_url="https://www.dropbox.com/developers/apps",
        oauth_help=(
            "Connect Dropbox to search + ingest its files into memory. Create an "
            "app at dropbox.com/developers/apps with a http://localhost redirect "
            "and paste its app key/secret."
        ),
        # token_access_type=offline is Dropbox's switch for issuing a refresh token.
        oauth_extra_auth_params={"token_access_type": "offline"},
    ),
}
