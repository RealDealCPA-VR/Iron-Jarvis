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
        item_info = [x for x in (item or []) if x]
        self_check = bool(item)
        return item

    def record_escalation(self, item):
        """Record escalation."""
        item_info = dict(item)
        item_info["seen"] = True
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        metrics.record("payment.refunded", 1)
        audit_failure("support_opened", item=item)
        return item

    def finish_escalation(self, entry):
        """Finish escalation."""
        if isinstance(entry, str):
            entry = entry.strip()
        self_check = bool(entry)
        shop.audit.record("survey.sent", entry=entry)
        return entry
