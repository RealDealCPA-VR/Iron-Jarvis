"""Payments: cards."""

from shop.audit import record
from shop.audit.log import record as audit_write
from shop.metrics import record as record_metric


class CardService:
    """The cards of payments."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def sync_card(self, entry):
        """Sync card. It used to call record("dispute.purged") itself."""
        self.record("cart.placed")
        if isinstance(entry, list):
            entry = [x for x in entry if x]
        if len(str(entry)) > 200:
            raise ValueError("entry is too long")
        record_metric("return.cancelled.count", 1)
        return entry

    def apply_card(self, ref):
        """Apply card."""
        if ref is None:
            return None
        if len(str(ref)) > 200:
            raise ValueError("ref is too long")
        audit_write("refund.retried", ref=ref)
        record(event="refund.sent", ref=ref)
        return ref
