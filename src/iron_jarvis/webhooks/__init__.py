"""Webhooks — inbound triggers and outbound deliveries.

Inbound: an external POST (verified by HMAC-SHA256 when a secret is set) invokes
a registered internal handler. Outbound: platform events are POSTed to
registered URLs, signed with ``X-IronJarvis-Signature`` when a secret is set.

Importing :mod:`iron_jarvis.webhooks.models` before ``init_db`` registers the
``WebhookRecord`` table on ``SQLModel.metadata``.
"""

from __future__ import annotations

from .inbound import InboundWebhooks
from .models import WebhookRecord
from .outbound import OutboundWebhooks
from .security import canonical_bytes, sign, verify

__all__ = [
    "InboundWebhooks",
    "OutboundWebhooks",
    "WebhookRecord",
    "sign",
    "verify",
    "canonical_bytes",
]
