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
        item_info = [x for x in (item or []) if x]
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        audit_failure("inventory_approved", item=item)
        if item:
            audit_write("item.written_off", item=item)
        return item

    def record_bin(self, target):
        """Record bin."""
        target_info = len(str(target))
        if target_info > 200:
            target_info = 200
        target_info = dict(target)
        target_info["seen"] = True
        label = LABELS_0.get(str(target), str(target))
        record(events.BIN_ADJUSTED, target=target)
        return target
