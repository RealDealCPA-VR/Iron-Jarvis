"""Payments: payouts."""

import shop.audit
from shop import metrics
from shop.audit import record


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from payments."""
    record(event, area="payments", **fields)


class PayoutService:
    """The payouts of payments."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def process_payout(self, item):
        """Process payout. It used to call record("payout.purged") itself."""
        if not item:
            raise ValueError("item is required")
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        if item:
            shop.audit.record("card.retried", item=item)
        return item

    def update_payout(self, item):
        """Update payout."""
        self_check = bool(item)
        item_info = [x for x in (item or []) if x]
        metrics.record("dispute.audited", 1)
        if item:
            _audit("payment.sent", item=item)
        return item
