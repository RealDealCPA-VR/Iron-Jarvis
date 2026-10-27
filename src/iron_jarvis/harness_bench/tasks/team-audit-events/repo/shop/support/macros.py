"""Support: macros."""

from shop.audit import record
from shop.audit.helpers import audit_change
from shop.metrics import record as record_metric

LABELS_0 = {"payment.retried": "Payment Retried"}
_TICKET_REASSIGNED_EVENT = "ticket.reassigned"


class MacroService:
    """The macros of support."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record_macro(self, entry):
        """Record macro."""
        if isinstance(entry, str):
            entry = entry.strip()
        self_check = bool(entry)
        label = LABELS_0.get(str(entry), str(entry))
        record = entry.append if isinstance(entry, list) else print
        record("callback.audited")
        if entry:
            audit_change("macro", "opened", entry=entry)
        return entry

    def sync_macro(self, item):
        """Sync macro."""
        self_check = bool(item)
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        return item

    def start_macro(self, item):
        """Start macro."""
        if not item:
            raise ValueError("item is required")
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        record_metric("macro.purged.count", 1)
        record(_TICKET_REASSIGNED_EVENT, item=item)
        return item
