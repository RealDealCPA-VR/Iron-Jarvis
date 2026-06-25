"""Model Router (§6).

Selects a ``(provider, model)`` for a request from policy/availability and
executes the completion. Fails over to the offline ``mock`` provider when the
requested provider is unavailable or errors, emitting ``provider.failed`` (§31).
"""

from __future__ import annotations

from typing import Any

from ..core.events import EventBus, EventType
from .adapters.base import LLMAdapter, LLMMessage, LLMResponse
from .manager import ProviderManager


class RouteResult:
    def __init__(self, response: LLMResponse, provider: str, model: str) -> None:
        self.response = response
        self.provider = provider
        self.model = model


class ModelRouter:
    def __init__(
        self,
        manager: ProviderManager,
        default_provider: str,
        event_bus: EventBus,
    ) -> None:
        self.manager = manager
        self.default_provider = default_provider
        self.event_bus = event_bus

    def _select(self, provider: str | None) -> LLMAdapter:
        wanted = provider or self.default_provider
        if self.manager.available(wanted):
            return self.manager.get(wanted)
        # fail over to offline mock
        return self.manager.get("mock")

    async def complete(
        self,
        *,
        provider: str | None = None,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> RouteResult:
        adapter = self._select(provider)
        try:
            response = await adapter.complete(
                system=system, messages=messages, tools=tools
            )
            return RouteResult(response, adapter.provider, adapter.model)
        except Exception as exc:
            await self.event_bus.publish(
                EventType.PROVIDER_FAILED,
                {"provider": adapter.provider, "error": f"{type(exc).__name__}: {exc}"},
                session_id=session_id,
            )
            fallback = self.manager.get("mock")
            if fallback is adapter:
                raise
            response = await fallback.complete(
                system=system, messages=messages, tools=tools
            )
            return RouteResult(response, fallback.provider, fallback.model)
