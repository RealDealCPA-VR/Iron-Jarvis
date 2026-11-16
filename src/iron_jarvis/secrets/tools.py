"""Agent-facing secrets tools (§19 tool interface).

Two thin tools over :class:`SecretsManager`, each constructed with the manager
injected:

* ``secret_set``  — ask for a secret by NAME; never takes a value (v1.502.0:
  a secret is never a tool argument — the owner saves it on the Secrets page
  or a chat's secure card). Permission default ``ask``.
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


#: v1.502.0 (north-star 2.11): where the OWNER saves a key — the only place a
#: secret is typed (the Secrets page, or a chat's secure card).
SECRET_SAVE_WORDS = (
    "A secret is never typed into a tool, so nothing was saved. Ask the user to "
    "save “{name}” in Settings › Connections › Secrets (or from a chat, "
    "where a secure card takes it); then use it by its name."
)


class SecretSetTool(Tool):
    """Ask for a secret by NAME (v1.502.0, north-star 2.11): never takes a value.

    It used to store the value the MODEL wrote as an argument. A secret is
    typed on the secure card and kept in the vault, never a tool argument: a
    value in a model's call has already passed through the model and its
    provider, and a model that "saves" a key it read in a document stores
    someone else's words as the user's credential. Chat swaps this tool for
    ``config_secret``'s secure card (``daemon/chat_turn``); an agent run has no
    card to raise, so here it says where the owner saves it. A call that still
    carries a value stores nothing and never repeats it (``redact_args`` keeps
    it off the ledger row)."""

    name = "secret_set"
    description = (
        "Ask for a secret (an API key, token or password) by NAME. You never pass "
        "or see the value: the user saves it themselves on the Secrets page, and "
        "a tool then reads it by its name."
    )
    permission_key = "secret_set"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "description": {"type": "string"},
        },
        "required": ["name"],
    }

    def __init__(self, manager: SecretsManager) -> None:
        self.manager = manager

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        # A VALUE a model still put in the call must never be persisted to the
        # invocation transcript (DB at rest / export / backups).
        red = dict(args)
        if "value" in red:
            red["value"] = "***REDACTED***"
        return red

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = " ".join(str(args.get("name") or "").split())[:120] or "the key"
        return ToolResult(
            ok=False,
            error=SECRET_SAVE_WORDS.format(name=name),
            data={"name": name, "stored": False},
        )


def secret_tools(manager: SecretsManager) -> list[Tool]:
    """Build the secrets tool pair bound to a single ``SecretsManager``."""
    return [SecretListTool(manager), SecretSetTool(manager)]
