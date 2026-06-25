"""Provider Manager (§5).

Registers provider adapters lazily and reports health. ``mock`` is always
available (offline). ``anthropic`` registers but is only healthy when
ANTHROPIC_API_KEY is present. Browser-session providers (§7, §10) surface via
the vault.
"""

from __future__ import annotations

import os
from typing import Callable

from .adapters.anthropic import AnthropicAdapter
from .adapters.base import LLMAdapter
from .adapters.mock import MockLLMAdapter
from .vault import BrowserVault

AdapterFactory = Callable[[], LLMAdapter]


class ProviderManager:
    def __init__(self, vault: BrowserVault | None = None, default_model: str = "claude-opus-4-8") -> None:
        self.vault = vault
        self._default_model = default_model
        self._factories: dict[str, AdapterFactory] = {}
        self._cache: dict[str, LLMAdapter] = {}
        self.register("mock", lambda: MockLLMAdapter())
        self.register("anthropic", lambda: AnthropicAdapter(model=default_model))

    def register(self, name: str, factory: AdapterFactory) -> None:
        self._factories[name] = factory
        self._cache.pop(name, None)

    def available(self, name: str) -> bool:
        if name == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        return name in self._factories

    def get(self, name: str) -> LLMAdapter:
        if name not in self._factories:
            raise KeyError(f"unknown provider '{name}'")
        if name not in self._cache:
            self._cache[name] = self._factories[name]()
        return self._cache[name]

    def health(self) -> list[dict]:
        rows = [
            {"provider": name, "available": self.available(name), "class": "api" if name == "anthropic" else "mock"}
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
