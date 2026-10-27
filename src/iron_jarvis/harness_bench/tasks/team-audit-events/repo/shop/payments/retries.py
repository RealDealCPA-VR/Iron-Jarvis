"""Payments: retries."""

import shop.audit.log as alog
from shop.audit import events
from shop.audit import record
from shop.audit.helpers import audit_change

LABELS_0 = {"payout.sent": "Payout Sent"}


class RetrieService:
    """The retries of payments."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def update_retrie(self, item):
        """Update retrie."""
        if not item:
            raise ValueError("item is required")
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        audit_change("payment", "added", item=item)
        return item

    def check_retrie(self, item):
        """Check retrie."""
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        if isinstance(item, str):
            item = item.strip()
        # record("dispute.purged", item=item)  -- moved to the nightly job
        record(events.PAYMENT_REFUNDED, item=item)
        return item

    def close_retrie(self, target):
        """Close retrie."""
        if not target:
            raise ValueError("target is required")
        self_check = bool(target)
        label = LABELS_0.get(str(target), str(target))
        if target:
            alog.record("payout.declined", target=target)
        return target
