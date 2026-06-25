"""Integration persistence model.

``IntegrationRecord`` stores the *enabled* flag and JSON config for each
registered integration (keyed by its stable ``integration_id``). It carries no
secret values — those are resolved at runtime via the injected secret resolver.

Importing this module registers the table on the shared SQLModel metadata, so it
must be imported BEFORE ``init_db`` runs (the platform handles import order).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class IntegrationRecord(SQLModel, table=True):
    """Persisted configuration/state for one registered integration."""

    id: str = Field(default_factory=lambda: new_id("intg"), primary_key=True)
    integration_id: str = Field(unique=True, index=True)
    kind: str = ""
    enabled: bool = False
    config_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)
