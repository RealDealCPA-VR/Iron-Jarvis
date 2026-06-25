"""Notify tool (§19) — lets an agent send a message through a channel.

Wraps a :class:`Notifier` injected at construction; ``notify_tools(notifier)``
builds the list for registration in the tool registry.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .notifier import Notifier


class NotifyTool(Tool):
    """Send a message to a named communication channel (or all configured)."""

    name = "notify"
    description = (
        "Send a notification message to a communication channel "
        "(Slack/Discord/Telegram/...). Omit `channel` to use the default/all."
    )
    permission_key = "notify"
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "channel": {
                "type": "string",
                "description": "Optional channel name; default routes to all configured.",
            },
        },
        "required": ["message"],
    }

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        message = str(args.get("message", ""))
        if not message:
            return ToolResult(ok=False, error="notify: `message` is required")
        channel = args.get("channel")
        channels = [channel] if channel else None
        results = self._notifier.notify(message, channels)
        if not results:
            return ToolResult(ok=False, error="notify: no channels configured")
        any_ok = any(r.get("ok") for r in results.values())
        summary = ", ".join(
            f"{name}={'ok' if r.get('ok') else 'fail'}" for name, r in results.items()
        )
        return ToolResult(
            ok=any_ok,
            output=summary,
            data={"results": results},
            error=None if any_ok else f"all channels failed: {summary}",
        )


def notify_tools(notifier: Notifier) -> list[Tool]:
    """Build the notify tool bound to a single :class:`Notifier`."""
    return [NotifyTool(notifier)]
