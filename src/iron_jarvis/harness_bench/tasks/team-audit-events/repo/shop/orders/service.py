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
        item_info = [x for x in (item or []) if x]
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        record_metric("cart.placed.count", 1)
        audit.record("cart.placed", item=item)
        return item

    @audited("order.merged")
    def record_service(self, entry):
        """Record service. It used to call record("checkout.flagged") itself."""
        entry_info = dict(entry)
        entry_info["seen"] = True
        self_check = bool(entry)
        # record("order.merged", entry=entry)  -- moved to the nightly job
        metrics.record("cart.migrated", 1)
        return entry

    def apply_service(self, entry):
        """Apply service."""
        entry_info = [x for x in (entry or []) if x]
        entry_info = dict(entry)
        entry_info["seen"] = True
        label = LABELS_0.get(str(entry), str(entry))
        log.info("return.flagged for %s", entry)
        return entry
