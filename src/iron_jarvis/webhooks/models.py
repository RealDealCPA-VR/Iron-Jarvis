"""Webhook persistence model.

``WebhookRecord`` is the registry row for one inbound or outbound webhook. The
live handler callables / injected ``http_post`` and the resolved secret values
stay in memory; this table records the durable registration (slug, direction,
target URL, which event types fire it, and whether it is enabled).

Importing this module before ``init_db`` registers the table on
``SQLModel.metadata`` so it auto-creates with the rest of the schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class WebhookRecord(SQLModel, table=True):
    """One registered webhook (inbound trigger or outbound delivery)."""

    id: str = Field(default_factory=lambda: new_id("whk"), primary_key=True)
    slug: str = Field(index=True, unique=True)
    direction: str = "inbound"  # "inbound" | "outbound"
    target_url: str = ""
    secret_name: str = ""
    event_types_json: str = "[]"
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
