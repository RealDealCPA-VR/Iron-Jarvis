"""Agent-facing webhook tool (§19 tool interface).

``webhook_add`` lets an agent register its *own* inbound trigger or outbound
delivery through the tool loop. It is constructed with the assembled ``platform``
(like :class:`~iron_jarvis.agents.delegate_tool.DelegateTool`) and acts on
``platform.inbound_webhooks`` / ``platform.outbound_webhooks``, resolving any
secret via ``platform.secrets``. ``webhook_tools(platform)`` builds it for
registration.

For an inbound webhook the tool registers a default handler that simply
publishes a ``webhook.received`` event onto the platform event bus (so the rest
of the system — observability, outbound deliveries, notifier — can react).
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult


class WebhookAddTool(Tool):
    """Register an inbound webhook trigger or an outbound webhook delivery."""

    name = "webhook_add"
    description = (
        "Register a webhook. direction 'inbound' (default) wires a `slug` that, "
        "when POSTed to, publishes a `webhook.received` event carrying the body. "
        "direction 'outbound' POSTs matching events to `target_url` (required) "
        "for the given `event_types`. Pass `secret_name` to HMAC-sign/verify "
        "using a stored secret. Returns the slug and direction."
    )
    permission_key = "webhook_add"
    input_schema = {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "direction": {"type": "string", "enum": ["inbound", "outbound"]},
            "target_url": {"type": "string"},
            "event_types": {"type": "array", "items": {"type": "string"}},
            "secret_name": {"type": "string"},
        },
        "required": ["slug"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        slug = args.get("slug") or ""
        if not slug:
            return ToolResult(ok=False, error="slug is required")
        direction = args.get("direction", "inbound")

        secret_name = args.get("secret_name")
        secret = self.platform.secrets.get(secret_name) if secret_name else None

        if direction == "outbound":
            target_url = args.get("target_url")
            if not target_url:
                return ToolResult(
                    ok=False, error="target_url is required for an outbound webhook"
                )
            try:
                self.platform.outbound_webhooks.register(
                    slug,
                    target_url,
                    args.get("event_types") or [],
                    secret=secret,
                    secret_name=secret_name,  # persist the real vault key (survives restart)
                )
            except ValueError as exc:
                return ToolResult(ok=False, error=str(exc))
        else:
            async def handler(body, _slug=slug):
                await self.platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": body}
                )
                return {"ok": True}

            self.platform.inbound_webhooks.register(
                slug, handler, secret=secret, secret_name=secret_name
            )

        return ToolResult(
            ok=True,
            output=f"registered {direction} webhook '{slug}'",
            data={"slug": slug, "direction": direction},
        )


def webhook_tools(platform) -> list[Tool]:
    """Build the webhook tool bound to the assembled ``platform``."""
    return [WebhookAddTool(platform)]
