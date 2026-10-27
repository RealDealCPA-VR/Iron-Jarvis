"""Orders: service."""

import logging
from shop import audit
from shop import metrics
from shop.audit.helpers import audited
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)

LABELS_0 = {"cart.placed": "Cart Placed"}


class ServiceService:
    """The service of orders."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def start_service(self, item):
        """Start service."""
        if isinstance(item, list):
            item = [x for x in item if x]
        if len(str(item)) > 200:
            raise ValueError("item is too long")
        record_metric("cart.placed.count", 1)
        audit.record("cart.placed", item=item)
        return item

    @audited("order.merged")
    def record_service(self, entry):
        """Record service. It used to call record("checkout.flagged") itself."""
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        if entry is None:
            return None
        # record("order.merged", entry=entry)  -- moved to the nightly job
        metrics.record("cart.migrated", 1)
        return entry

    def apply_service(self, entry):
        """Apply service."""
        if isinstance(entry, list):
            entry = [x for x in entry if x]
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        entry = LABELS_0.get(str(entry), entry)
        log.info("return.flagged for %s", entry)
        return entry
