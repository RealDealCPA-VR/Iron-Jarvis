"""Integration Registry.

Central place to register integration *specs* + *factories*, persist their
enabled/config state as :class:`IntegrationRecord` rows, instantiate a live
:class:`Integration` from stored config, and test connectivity.

Sibling feature modules (comm / ltm / webhooks) register their own specs here
externally; this module deliberately imports none of them.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import dumps, session_scope
from .base import Integration, IntegrationSpec, SecretResolver
from .models import IntegrationRecord

#: ``factory(config, secret_resolver) -> Integration``
Factory = Callable[[dict, SecretResolver], Integration]


class IntegrationRegistry:
    """Registers, configures, enables, instantiates and tests integrations."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._specs: dict[str, IntegrationSpec] = {}
        self._factories: dict[str, Factory] = {}

    # --- registration -----------------------------------------------------

    def register(self, spec: IntegrationSpec, factory: Factory) -> None:
        """Register an integration spec and its construction factory."""
        if not spec.id:
            raise ValueError("integration spec must have an id")
        self._specs[spec.id] = spec
        self._factories[spec.id] = factory

    def specs(self) -> list[IntegrationSpec]:
        """All registered specs, ordered by id."""
        return [self._specs[k] for k in sorted(self._specs)]

    def get_spec(self, integration_id: str) -> IntegrationSpec | None:
        return self._specs.get(integration_id)

    # --- persistence ------------------------------------------------------

    def get_record(self, integration_id: str) -> IntegrationRecord | None:
        """Return the stored record for ``integration_id`` (detached), or None."""
        with session_scope(self.engine) as db:
            record = db.exec(
                select(IntegrationRecord).where(
                    IntegrationRecord.integration_id == integration_id
                )
            ).first()
            if record is not None:
                db.expunge(record)  # safe to read after the session closes
            return record

    def _upsert(
        self,
        integration_id: str,
        *,
        enabled: bool | None = None,
        config: dict | None = None,
    ) -> IntegrationRecord:
        spec = self._specs.get(integration_id)
        if spec is None:
            raise KeyError(f"unknown integration '{integration_id}'")
        with session_scope(self.engine) as db:
            record = db.exec(
                select(IntegrationRecord).where(
                    IntegrationRecord.integration_id == integration_id
                )
            ).first()
            if record is None:
                record = IntegrationRecord(
                    integration_id=integration_id, kind=spec.kind
                )
            if enabled is not None:
                record.enabled = enabled
            if config is not None:
                record.config_json = dumps(config)
            db.add(record)
            db.commit()
            db.refresh(record)
            db.expunge(record)
            return record

    def enable(self, integration_id: str, enabled: bool = True) -> IntegrationRecord:
        """Enable (or disable) an integration; persists an IntegrationRecord."""
        return self._upsert(integration_id, enabled=enabled)

    def configure(self, integration_id: str, config: dict) -> IntegrationRecord:
        """Persist ``config`` for an integration; persists an IntegrationRecord."""
        return self._upsert(integration_id, config=config)

    def stored_config(self, integration_id: str) -> dict:
        """Return the persisted config dict for ``integration_id`` ({} if none)."""
        record = self.get_record(integration_id)
        if record is None or not record.config_json:
            return {}
        try:
            value = json.loads(record.config_json)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    # --- instantiation / testing -----------------------------------------

    def instantiate(
        self, integration_id: str, secret_resolver: SecretResolver
    ) -> Integration:
        """Build a live integration from its stored config."""
        factory = self._factories.get(integration_id)
        if factory is None:
            raise KeyError(f"unknown integration '{integration_id}'")
        return factory(self.stored_config(integration_id), secret_resolver)

    def test(self, integration_id: str, secret_resolver: SecretResolver) -> dict:
        """Instantiate and probe an integration; never raises."""
        try:
            integration = self.instantiate(integration_id, secret_resolver)
        except KeyError as exc:
            return {"ok": False, "detail": str(exc)}
        try:
            result = integration.test_connection()
        except Exception as exc:  # an integration must not crash the caller
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {
            "ok": bool(result.get("ok")),
            "detail": str(result.get("detail", "")),
        }

    # --- status (never leaks secret values) ------------------------------

    def list_status(self) -> list[dict]:
        """Per-integration status. Reports only the *names* of required secrets."""
        out: list[dict] = []
        for spec in self.specs():
            record = self.get_record(spec.id)
            configured = bool(
                record
                and record.config_json
                and record.config_json not in ("{}", "")
            )
            out.append(
                {
                    "id": spec.id,
                    "kind": spec.kind,
                    "display_name": spec.display_name,
                    "enabled": bool(record.enabled) if record else False,
                    "configured": configured,
                    "required_secrets": list(spec.required_secrets),
                }
            )
        return out
