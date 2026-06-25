"""Integration framework — core types (external-service integrations).

An *integration* binds the platform to an external service (a REST API, a chat
provider, a webhook sink, …). Each integration is described by an
:class:`IntegrationSpec` (static metadata + the names of the secrets it needs)
and implemented by an :class:`Integration` subclass.

Secrets are never stored on the integration. Instead the constructor receives a
``secret_resolver`` callable that maps a *named* secret to its value at call
time (wired to e.g. ``SecretsManager.get``). External network calls go through
an injected client so the unit tests run fully offline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

#: Resolve a named secret to its value (or ``None`` if unset). Injected so the
#: integration layer never reads/persists raw secret material itself.
SecretResolver = Callable[[str], "str | None"]


@dataclass
class IntegrationSpec:
    """Static description of an integration (advertised to the UI/agents)."""

    id: str
    kind: str
    display_name: str
    description: str = ""
    required_secrets: list[str] = field(default_factory=list)
    config_schema: dict = field(default_factory=dict)


class Integration(ABC):
    """Live, configured instance of an integration.

    Constructed with the stored ``config`` (a plain JSON-able dict) and a
    ``secret_resolver`` that yields secret values on demand. Subclasses must
    never copy resolved secrets into ``config`` or any persisted state.
    """

    def __init__(self, config: dict, secret_resolver: SecretResolver) -> None:
        self.config: dict = dict(config or {})
        self._secret_resolver: SecretResolver = secret_resolver

    def secret(self, name: str) -> str | None:
        """Resolve a named secret via the injected resolver."""
        if not name:
            return None
        return self._secret_resolver(name)

    @abstractmethod
    def test_connection(self) -> dict:
        """Probe the service. Return ``{"ok": bool, "detail": str}``."""
        ...

    @abstractmethod
    def capabilities(self) -> list[str]:
        """Return the capability identifiers this integration exposes."""
        ...
