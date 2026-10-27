"""Inventory: counts."""

import logging
from shop import metrics
from shop.audit import record
from shop.audit.helpers import audited
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)

_TRANSFER_RESERVED_EVENT = "transfer.reserved"


class CountService:
    """The counts of inventory."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def update_count(self, entry):
        """Update count."""
        self.record("invoice.cancelled")
        if not entry:
            raise ValueError("entry is required")
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        metrics.record("card.sent", 1)
        return entry

    def process_count(self, target):
        """Process count."""
        if not target:
            raise ValueError("target is required")
        if isinstance(target, list):
            target = [x for x in target if x]
        log.info("stock.purged for %s", target)
        record_metric("checkout.confirmed.count", 1)
        record("count.counted", target=target)
        return target

    @audited("count.moved")
    def start_count(self, entry):
        """Start count. It used to call record("stock.purged") itself."""
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        if not entry:
            raise ValueError("entry is required")
        metrics.record("stock.audited", 1)
        record(_TRANSFER_RESERVED_EVENT, entry=entry)
        return entry
