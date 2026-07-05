"""Provider Manager (§5).

Registers provider adapters lazily and reports health. ``mock`` is always
available (offline). API providers (``anthropic``/``openai``/``google``) become
available the moment a real credential exists — resolved from the Connections
layer / secrets vault (or, for Anthropic, the ANTHROPIC_API_KEY env var). This is
what makes "connect a model and it just works" true. Browser-session providers
(§7, §10) surface via the vault.
"""

from __future__ import annotations

import os
from typing import Callable

from .adapters.anthropic import AnthropicAdapter
from .adapters.base import LLMAdapter
from .adapters.google import GoogleAdapter
from .adapters.mock import MockLLMAdapter
from .adapters.openai import OpenAIAdapter
from .vault import BrowserVault

CredentialResolver = Callable[[str], "str | None"]
#: Presence-only check (NO network refresh) used for availability/health.
PresenceResolver = Callable[[str], bool]
AdapterFactory = Callable[..., LLMAdapter]

#: API providers whose availability is gated on a real credential.
API_PROVIDERS = ("anthropic", "openai", "google", "xai", "openrouter")

#: xAI (Grok) is OpenAI-compatible, so it routes through the OpenAI adapter with
#: a base_url override (same pattern as a local Ollama server).
XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"

#: OpenRouter — one key routes every lab's models (OpenAI-compatible aggregator).
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def _normalize_ollama_url(url: str | None) -> str | None:
    """Accept a host, a ``/v1`` base, or a full chat URL → the chat endpoint.

    Any OpenAI-compatible server (Ollama, Ollama Cloud, LM Studio, vLLM...)
    serves chat at ``<host>/v1/chat/completions``. Users naturally enter
    ``http://localhost:11434`` or ``.../v1``; without this the adapter POSTs to
    the URL verbatim and every call 404s. Mirrors the host-normalization the
    embeddings layer already does on the same value.
    """
    if not url:
        return url
    u = url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


class ProviderManager:
    def __init__(
        self,
        vault: BrowserVault | None = None,
        default_model: str = "claude-opus-4-8",
        credential_resolver: CredentialResolver | None = None,
        presence_resolver: PresenceResolver | None = None,
        ollama_base_url: str | None = None,
        ollama_model: str = "llama3.1",
        custom_base_url: str | None = None,
        custom_model: str = "",
        grok_cli_available: Callable[[], bool] | None = None,
    ) -> None:
        self.vault = vault
        self._default_model = default_model
        self._credential_resolver = credential_resolver
        # Local OpenAI-compatible (Ollama) endpoint: when set, the "ollama"
        # provider is available and routes through OpenAIAdapter(base_url=...).
        # Normalized so a host-only URL ("http://localhost:11434") still resolves
        # to the real /v1/chat/completions endpoint instead of 404-ing.
        self._ollama_base_url = _normalize_ollama_url(ollama_base_url)
        self._ollama_model = ollama_model
        # CUSTOM OpenAI-compatible endpoint (Ollama Cloud / LM Studio / vLLM /
        # any aggregator) — same normalization; key is OPTIONAL (resolved from
        # the vault when connected, keyless local servers just work).
        self._custom_base_url = _normalize_ollama_url(custom_base_url)
        self._custom_model = custom_model
        # Live availability probe for the locally-installed Grok CLI, INJECTED by
        # the platform (reads ~/.grok). Kept out of the manager itself so unit
        # tests that build a bare ProviderManager() stay hermetic — a bare manager
        # reports grok-cli unavailable regardless of what's installed on the box.
        self._grok_cli_available_fn = grok_cli_available
        # Presence-only resolver for availability/health: when wired it avoids a
        # blocking OAuth refresh on the async loop. Falls back to the (possibly
        # refreshing) credential check when None, preserving legacy behavior.
        self._presence_resolver = presence_resolver
        self._factories: dict[str, AdapterFactory] = {}
        self._cache: dict[tuple[str, str | None], LLMAdapter] = {}
        self.register("mock", lambda model=None: MockLLMAdapter())
        self.register(
            "anthropic",
            lambda model=None: AnthropicAdapter(
                model=model or default_model, credential=lambda: self._cred("anthropic")
            ),
        )
        self.register(
            "openai",
            lambda model=None: OpenAIAdapter(
                model=model or "gpt-4o-mini", credential=lambda: self._cred("openai")
            ),
        )
        self.register(
            "google",
            lambda model=None: GoogleAdapter(
                model=model or "gemini-1.5-flash",
                credential=lambda: self._cred("google"),
                # google connects via OAuth (specs.py method="oauth"): the
                # credential is an access token, sent as Authorization: Bearer.
                oauth=True,
            ),
        )
        # xAI (Grok) — OpenAI-compatible hosted API; routes through the OpenAI
        # adapter pointed at api.x.ai. Availability is gated on a real credential
        # (an xAI API key, or an OAuth token if xAI later ships a public client).
        self.register(
            "xai",
            lambda model=None: OpenAIAdapter(
                model=model or "grok-2-latest",
                base_url=XAI_ENDPOINT,
                credential=lambda: self._cred("xai"),
                provider_name="xai",
            ),
        )
        # OpenRouter — one key, every lab's models, OpenAI-compatible. Model ids
        # are namespaced ("x-ai/grok-code-fast-1", "openrouter/auto"...).
        self.register(
            "openrouter",
            lambda model=None: OpenAIAdapter(
                model=model or "openrouter/auto",
                base_url=OPENROUTER_ENDPOINT,
                credential=lambda: self._cred("openrouter"),
                provider_name="openrouter",
            ),
        )
        # Local "ollama" provider — an OpenAI-compatible server reached over a
        # configured base_url, needing no API key. Always registered so get()
        # works once configured; availability is gated on ollama_base_url.
        self.register(
            "ollama",
            lambda model=None: OpenAIAdapter(
                model=model or self._ollama_model,
                base_url=self._ollama_base_url,
                api_key=None,
                provider_name="ollama",
            ),
        )
        # CUSTOM endpoint — user-pointed OpenAI-compatible server/aggregator
        # (Ollama Cloud, LM Studio, vLLM, llama.cpp...). Key optional: resolved
        # from the vault when the user connected one on the Connections page.
        self.register(
            "custom",
            lambda model=None: OpenAIAdapter(
                model=model or self._custom_model or "default",
                base_url=self._custom_base_url,
                credential=lambda: self._cred("custom"),
                provider_name="custom",
            ),
        )
        # LOCALLY-INSTALLED CLI provider: Grok (xAI's `grok` CLI). Detected on
        # disk (~/.grok) rather than configured — routes through its own account
        # session against the CLI chat proxy. Always registered so get() works
        # the moment the CLI is installed+logged-in; availability is a LIVE check
        # of the on-disk session (see available()), so it lights up/greys out
        # without a daemon restart. The adapter import is lazy to avoid pulling
        # the CLI stack into every manager construction.
        self.register("grok-cli", lambda model=None: self._make_grok_cli(model))
        # Subscription CLIs (§arbitrage): a logged-in `claude` / `codex` binary
        # is a FLAT-RATE provider — headless print-mode, no API key, the CLI
        # owns auth + model churn. Text-only (no tool calls) by design.
        self.register("claude-cli", lambda model=None: self._make_subprocess_cli("claude-cli"))
        self.register("codex-cli", lambda model=None: self._make_subprocess_cli("codex-cli"))

    def _make_subprocess_cli(self, which: str) -> LLMAdapter:
        from .adapters.subprocess_cli import make_claude_cli, make_codex_cli

        return make_claude_cli() if which == "claude-cli" else make_codex_cli()

    @staticmethod
    def _cli_binary_present(binary: str) -> bool:
        """Availability for subscription CLIs — the binary on PATH (or the
        common per-user bin dirs the terminals launcher already scans)."""
        try:
            from ..terminals.ai_clis import _find  # shared detection heuristics

            return _find(binary) is not None
        except Exception:  # noqa: BLE001
            import shutil

            return shutil.which(binary) is not None

    def _make_grok_cli(self, model: str | None) -> LLMAdapter:
        from .adapters.grok_cli import GrokCliAdapter

        return GrokCliAdapter(model=model or "grok-build")

    def _grok_cli_available(self) -> bool:
        """Availability for the locally-installed Grok CLI via the injected
        probe. A bare manager (no probe wired — the unit-test path) reports
        unavailable, so availability never depends on the host's ~/.grok."""
        if self._grok_cli_available_fn is None:
            return False
        try:
            return bool(self._grok_cli_available_fn())
        except Exception:  # noqa: BLE001
            return False

    def _cred(self, name: str) -> str | None:
        """Resolve a live credential for an API provider (vault/connections → env)."""
        if self._credential_resolver is not None:
            try:
                cred = self._credential_resolver(name)
                if cred:
                    return cred
            except Exception:
                pass
        if name == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        return None

    def _present(self, name: str) -> bool:
        """Presence-only availability for an API provider — NEVER refreshes.

        Prefers the injected ``presence_resolver`` (e.g. the Connections layer's
        ``has_credential``, which only checks the vault). With no presence
        resolver wired, falls back to the existing credential check so behavior
        is unchanged. The ANTHROPIC_API_KEY env var is always honored (no I/O).
        """
        if self._presence_resolver is not None:
            try:
                if self._presence_resolver(name):
                    return True
            except Exception:
                pass
        elif self._credential_resolver is not None:
            try:
                if self._credential_resolver(name):
                    return True
            except Exception:
                pass
        if name == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        return False

    def register(self, name: str, factory: AdapterFactory) -> None:
        self._factories[name] = factory
        for key in [k for k in self._cache if k[0] == name]:
            self._cache.pop(key, None)

    def available(self, name: str) -> bool:
        if name in API_PROVIDERS:
            return self._present(name)
        if name == "ollama":
            # Local provider: available only once a base_url is configured.
            return self._ollama_base_url is not None
        if name == "custom":
            # Custom endpoint: gated on the base_url, NOT a key (keyless local
            # servers are the common case; a vault key is used when present).
            return self._custom_base_url is not None
        if name == "grok-cli":
            # Locally-installed Grok CLI: live on-disk session check.
            return self._grok_cli_available()
        if name == "claude-cli":
            return self._cli_binary_present("claude")
        if name == "codex-cli":
            return self._cli_binary_present("codex")
        return name in self._factories

    def has_available_api_provider(self) -> bool:
        """True if at least one REAL (non-mock) provider is connected/available.

        Used by the router to detect the "default is still mock while a real
        provider is connected" trap and emit a downgrade signal instead of
        silently returning fabricated mock output.
        """
        return (
            any(self.available(p) for p in API_PROVIDERS)
            or self.available("ollama")
            or self.available("custom")
            or self.available("grok-cli")
        )

    def get(self, name: str, model: str | None = None) -> LLMAdapter:
        if name not in self._factories:
            raise KeyError(f"unknown provider '{name}'")
        key = (name, model)
        if key not in self._cache:
            factory = self._factories[name]
            try:  # model-aware factories take the model; legacy ones take nothing
                self._cache[key] = factory(model)
            except TypeError:
                self._cache[key] = factory()
        return self._cache[key]

    def health(self) -> list[dict]:
        rows = [
            {
                "provider": name,
                "available": self.available(name),
                "class": (
                    "api"
                    if name in API_PROVIDERS
                    else "local"
                    if name in ("ollama", "custom", "grok-cli")
                    else "mock"
                ),
            }
            for name in sorted(self._factories)
        ]
        if self.vault is not None:
            for entry in self.vault.providers():
                rows.append(
                    {
                        "provider": entry["provider"],
                        "available": entry["logged_in"],
                        "class": "browser",
                    }
                )
        return rows
