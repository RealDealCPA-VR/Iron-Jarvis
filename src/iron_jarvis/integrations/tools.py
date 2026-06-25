"""Integration tools (§19 tool interface).

Exposes the integration registry to agents:

* ``integration_list`` — list integrations with enabled/configured status
  (never secret values).
* ``integration_test`` — probe one integration's connectivity by id.

Both wrap an :class:`IntegrationRegistry` plus the injected ``secret_resolver``.
``integration_tools(registry, secret_resolver)`` builds the pair for
registration in the tool registry.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .base import SecretResolver
from .registry import IntegrationRegistry


class IntegrationListTool(Tool):
    """List registered integrations and their status (§19)."""

    name = "integration_list"
    description = (
        "List registered external-service integrations with their enabled and "
        "configured status. Never returns secret values."
    )
    input_schema = {"type": "object", "properties": {}}

    def __init__(
        self, registry: IntegrationRegistry, secret_resolver: SecretResolver
    ) -> None:
        self._registry = registry
        self._resolver = secret_resolver

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        status = self._registry.list_status()
        lines = [
            f"{s['id']} ({s['kind']}) enabled={s['enabled']} configured={s['configured']}"
            for s in status
        ]
        return ToolResult(
            ok=True,
            output="\n".join(lines) if lines else "(no integrations registered)",
            data={"integrations": status},
        )


class IntegrationTestTool(Tool):
    """Test connectivity for one integration by id (§19)."""

    name = "integration_test"
    description = "Test connectivity for a configured integration by its id."
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    def __init__(
        self, registry: IntegrationRegistry, secret_resolver: SecretResolver
    ) -> None:
        self._registry = registry
        self._resolver = secret_resolver

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        integration_id = str(args.get("id", "")).strip()
        if not integration_id:
            return ToolResult(ok=False, error="missing required arg 'id'")
        result = self._registry.test(integration_id, self._resolver)
        return ToolResult(
            ok=bool(result.get("ok")),
            output=str(result.get("detail", "")),
            data=result,
        )


def integration_tools(
    registry: IntegrationRegistry, secret_resolver: SecretResolver
) -> list[Tool]:
    """Build the integration tools bound to ``registry`` + ``secret_resolver``."""
    return [
        IntegrationListTool(registry, secret_resolver),
        IntegrationTestTool(registry, secret_resolver),
    ]
