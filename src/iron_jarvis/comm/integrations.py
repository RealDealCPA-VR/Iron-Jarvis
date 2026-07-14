"""Config-driven channel construction + integration specs.

``build_notifier`` turns a ``config.comm`` mapping into a wired :class:`Notifier`
(channels constructed from :data:`~.channels.CHANNEL_TYPES`, secrets resolved via
the injected resolver, HTTP via the injected transport). ``channel_integrations``
exposes one :class:`IntegrationSpec` per channel type for the integrations
registry / dashboard so users can discover and configure channels.

Expected ``config.comm`` shape::

    [comm]
    default_channel = "slack"
    [comm.channels.slack]
    type = "slack"
    webhook_url = "https://hooks.slack.com/services/..."
    [comm.channels.tg]
    type = "telegram"
    token_secret = "telegram_bot_token"
    chat_id = 12345
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import HttpGet, HttpPost, SecretResolver, httpx_get, httpx_post
from .channels import CHANNEL_TYPES, MockChannel
from .notifier import DEFAULT_ALERT_EVENTS, Notifier


@dataclass
class IntegrationSpec:
    """Describes a configurable communication integration (for discovery/UI)."""

    name: str
    kind: str  # always "communication" here
    description: str
    config_fields: list[str] = field(default_factory=list)
    secret_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "config_fields": self.config_fields,
            "secret_fields": self.secret_fields,
        }


def channel_integrations() -> list[IntegrationSpec]:
    """One spec per channel type, for the integrations registry."""
    return [
        IntegrationSpec(
            "slack",
            "communication",
            "Post to Slack via incoming webhook or chat.postMessage bot token.",
            config_fields=["webhook_url", "channel"],
            secret_fields=["token_secret"],
        ),
        IntegrationSpec(
            "discord",
            "communication",
            "Post to a Discord channel via an incoming webhook.",
            config_fields=["webhook_url"],
        ),
        IntegrationSpec(
            "telegram",
            "communication",
            "Send AND receive Telegram messages via the Bot API. Two-way comm: "
            "set inbound_enabled + allowed_senders to text Iron Jarvis a task.",
            config_fields=["chat_id", "inbound_enabled", "allowed_senders"],
            secret_fields=["token_secret"],
        ),
        IntegrationSpec(
            "email",
            "communication",
            "Send AND receive email (SMTP out, IMAP in). Inbound trigger (CX-05): "
            "set inbound_enabled + allowed_senders (From-addresses) so an "
            "allowlisted sender can email Iron Jarvis a task.",
            config_fields=[
                "imap_host",
                "imap_port",
                "username",
                "mailbox",
                "inbound_enabled",
                "allowed_senders",
            ],
            secret_fields=["password_secret"],
        ),
        IntegrationSpec(
            "mock",
            "communication",
            "Offline test channel that records messages in-memory.",
        ),
        IntegrationSpec(
            "console",
            "communication",
            "Print/log notifications locally; a safe always-available fallback.",
        ),
    ]


def build_notifier(
    comm_config: dict[str, Any] | None,
    *,
    secret_resolver: SecretResolver | None = None,
    http_post: HttpPost | None = None,
    http_get: HttpGet | None = None,
) -> Notifier:
    """Construct a :class:`Notifier` from a ``config.comm`` mapping.

    Falls back to a single :class:`MockChannel` named ``"mock"`` when no channels
    are configured, so the platform always has a safe offline default. Each
    channel's per-registration config (including the two-way fields
    ``inbound_enabled`` and ``allowed_senders``) is preserved on the channel so
    the inbound poller can read it.
    """
    comm_config = comm_config or {}
    transport = http_post or httpx_post
    get_transport = http_get or httpx_get

    event_types = set(comm_config.get("event_types") or DEFAULT_ALERT_EVENTS)
    notifier = Notifier(
        default_channel=comm_config.get("default_channel"),
        event_types=event_types,
    )

    channels = comm_config.get("channels") or {}
    for reg_name, spec in channels.items():
        spec = dict(spec or {})
        ctype = spec.pop("type", reg_name)
        cls = CHANNEL_TYPES.get(ctype)
        if cls is None:
            continue
        notifier.add_channel(
            reg_name,
            cls(
                spec,
                http_post=transport,
                http_get=get_transport,
                secret_resolver=secret_resolver,
            ),
        )

    if not notifier.channels():
        notifier.add_channel("mock", MockChannel())
    return notifier
