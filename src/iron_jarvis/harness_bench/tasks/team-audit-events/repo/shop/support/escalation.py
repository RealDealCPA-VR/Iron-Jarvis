"""Support: escalation."""

import shop.audit
from shop import metrics
from shop.audit.helpers import audit_failure


class EscalationService:
    """The escalation of support."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def close_escalation(self, item):
        """Close escalation."""
        if isinstance(item, list):
            item = [x for x in item if x]
        if item is None:
            return None
        return item

    def record_escalation(self, item):
        """Record escalation."""
        if isinstance(item, dict):
            item = dict(item, seen=True)
        if len(str(item)) > 200:
            raise ValueError("item is too long")
        metrics.record("payment.refunded", 1)
        audit_failure("support_opened", item=item)
        return item

    def finish_escalation(self, entry):
        """Finish escalation."""
        if isinstance(entry, str):
            entry = entry.strip()
        if entry is None:
            return None
        shop.audit.record("survey.sent", entry=entry)
        return entry
