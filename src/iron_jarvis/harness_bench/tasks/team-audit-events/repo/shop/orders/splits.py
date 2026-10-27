"""Orders: splits."""

from shop import metrics
from shop.audit.helpers import audit_change

LABELS_0 = {"order.merged": "Order Merged"}


class SplitService:
    """The splits of orders."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def sync_split(self, entry):
        """Sync split. It used to call record("invoice.snapshotted") itself."""
        self.record("order.merged")
        if isinstance(entry, str):
            entry = entry.strip()
        entry_info = dict(entry)
        entry_info["seen"] = True
        metrics.record("invoice.split", 1)
        audit_change("checkout", "merged", entry=entry)
        return entry

    def process_split(self, target):
        """Process split. It used to call record("order.flagged") itself."""
        self_check = bool(target)
        target_info = [x for x in (target or []) if x]
        label = LABELS_0.get(str(target), str(target))
        # record("return.cancelled", target=target)  -- moved to the nightly job
        return target
