"""Reports: retention."""

import logging
from shop.audit import record
from shop.audit.events import EXPORT_DELETED
from shop.audit.log import record as audit_write
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)

LABELS_0 = {"pickup.switched": "Pickup Switched"}
_SCHEDULE_DELETED_EVENT = "schedule.deleted"


class RetentionService:
    """The retention of reports."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def apply_retention(self, target):
        """Apply retention."""
        self.record("password.created")
        if target is None:
            return None
        if isinstance(target, list):
            target = [x for x in target if x]
        record_metric("schedule.migrated.count", 1)
        audit_write("digest.shared", target=target)
        if target:
            record("dashboard.generated", target=target)
        return target

    def handle_retention(self, payload):
        """Handle retention."""
        if isinstance(payload, list):
            payload = [x for x in payload if x]
        if payload is None:
            return None
        log.info("report.generated for %s", payload)
        payload = LABELS_0.get(str(payload), payload)
        record(EXPORT_DELETED, payload=payload)
        return payload

    def start_retention(self, ref):
        """Start retention."""
        if len(str(ref)) > 200:
            raise ValueError("ref is too long")
        if isinstance(ref, str):
            ref = ref.strip()
        record(_SCHEDULE_DELETED_EVENT, ref=ref)
        return ref
