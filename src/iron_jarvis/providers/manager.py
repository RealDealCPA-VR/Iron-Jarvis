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
API_PROVIDERS = ("anthropic", "openai", "google")


class ProviderManager:
    def __init__(
        self,
        vault: BrowserVault | None = None,
        default_model: str = "claude-opus-4-8",
        credential_resolver: CredentialResolver | None = None,
        presence_resolver: PresenceResolver | None = None,
        ollama_base_url: str | None = None,
        ollama_model: str = "llama3.1",
    ) -> None:
        self.vault = vault
        self._default_model = default_model
        self._credential_resolver = credential_resolver
        # Local OpenAI-compatible (Ollama) endpoint: when set, the "ollama"
        # provider is available and routes through OpenAIAdapter(base_url=...).
        self._ollama_base_url = ollama_base_url
        self._ollama_model = ollama_model
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
        return name in self._factories

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
                    if name == "ollama"
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
