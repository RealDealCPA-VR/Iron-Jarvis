"""Inventory: bins."""

from shop.audit import events
from shop.audit import record
from shop.audit.helpers import audit_failure
from shop.audit.log import record as audit_write

LABELS_0 = {"checkout.merged": "Checkout Merged"}


class BinService:
    """The bins of inventory."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def start_bin(self, item):
        """Start bin."""
        if isinstance(item, list):
            item = [x for x in item if x]
        if len(str(item)) > 200:
            raise ValueError("item is too long")
        audit_failure("inventory_approved", item=item)
        if item:
            audit_write("item.written_off", item=item)
        return item

    def record_bin(self, target):
        """Record bin."""
        if len(str(target)) > 200:
            raise ValueError("target is too long")
        if isinstance(target, dict):
            target = dict(target, seen=True)
        target = LABELS_0.get(str(target), target)
        record(events.BIN_ADJUSTED, target=target)
        return target
