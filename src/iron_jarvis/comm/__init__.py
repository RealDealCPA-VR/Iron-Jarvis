"""Communication channels + Notifier.

User-choosable outbound channels (Slack / Telegram / Discord / Mock / Console)
behind a :class:`Notifier`, plus a :class:`NotifyTool` for agents and an
EventBus adapter (:meth:`Notifier.on_event`) for automatic alerts. All external
HTTP flows through an injected ``http_post`` so the platform runs fully offline.
"""

from __future__ import annotations

from .base import (
    Channel,
    HttpPost,
    SecretResolver,
    httpx_post,
    interpret_response,
)
from .channels import (
    CHANNEL_TYPES,
    ConsoleChannel,
    DiscordChannel,
    MockChannel,
    SlackChannel,
    TelegramChannel,
)
from .integrations import (
    IntegrationSpec,
    build_notifier,
    channel_integrations,
)
from .notifier import DEFAULT_ALERT_EVENTS, Notifier, format_event
from .tools import NotifyTool, notify_tools

__all__ = [
    "Channel",
    "HttpPost",
    "SecretResolver",
    "httpx_post",
    "interpret_response",
    "SlackChannel",
    "DiscordChannel",
    "TelegramChannel",
    "MockChannel",
    "ConsoleChannel",
    "CHANNEL_TYPES",
    "Notifier",
    "format_event",
    "DEFAULT_ALERT_EVENTS",
    "NotifyTool",
    "notify_tools",
    "IntegrationSpec",
    "build_notifier",
    "channel_integrations",
]
