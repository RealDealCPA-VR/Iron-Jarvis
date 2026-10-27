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
        entry_info = [x for x in (entry or []) if x]
        entry_info = len(str(entry))
        if entry_info > 200:
            entry_info = 200
        record_metric("return.cancelled.count", 1)
        return entry

    def apply_card(self, ref):
        """Apply card."""
        self_check = bool(ref)
        ref_info = len(str(ref))
        if ref_info > 200:
            ref_info = 200
        audit_write("refund.retried", ref=ref)
        record(event="refund.sent", ref=ref)
        return ref
