"""Agent-facing secrets tools (§19 tool interface).

Two thin tools over :class:`SecretsManager`, each constructed with the manager
injected:

* ``secret_set``  — store/update a secret (UPSERT). Permission default ``ask``.
* ``secret_list`` — list secret names + kinds ONLY. Permission default ``allow``.

There is deliberately **no** ``secret_get`` tool: decrypted values must never be
exposed to the model. Retrieval stays server-side via ``SecretsManager.get``.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .manager import KINDS, SecretsManager


class SecretListTool(Tool):
    """List stored secrets — names and kinds only, never the values (§7)."""

    name = "secret_list"
    description = (
        "List stored secrets by name and kind. Never returns secret values."
    )
    permission_key = "secret_list"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, manager: SecretsManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        secrets = [{"name": s["name"], "kind": s["kind"]} for s in self.manager.list()]
        output = "\n".join(f"{s['name']} ({s['kind']})" for s in secrets)
        return ToolResult(
            ok=True,
            output=output,
            data={"secrets": secrets, "count": len(secrets)},
        )


class SecretSetTool(Tool):
    """Store or update a secret value (UPSERT by name); value is encrypted (§10)."""

    name = "secret_set"
    description = (
        "Store or update a secret (api_key/oauth/token/password/generic). "
        "The value is encrypted at rest and never returned by any tool."
    )
    permission_key = "secret_set"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "description": {"type": "string"},
        },
        "required": ["name", "value"],
    }

    def __init__(self, manager: SecretsManager) -> None:
        self.manager = manager

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        # The secret VALUE must never be persisted to the invocation transcript
        # (DB at rest / export / backups) — that would defeat the encrypted vault.
        red = dict(args)
        if "value" in red:
            red["value"] = "***REDACTED***"
        return red

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        record = self.manager.set(
            args["name"],
            args["value"],
            kind=args.get("kind", "generic"),
            description=args.get("description", ""),
        )
        return ToolResult(
            ok=True,
            output=f"stored secret '{record.name}' ({record.kind})",
            data={"name": record.name, "kind": record.kind},
        )


def secret_tools(manager: SecretsManager) -> list[Tool]:
    """Build the secrets tool pair bound to a single ``SecretsManager``."""
    return [SecretListTool(manager), SecretSetTool(manager)]
