"""Orders: carts."""

import logging
from shop.audit import record
from shop.audit.helpers import audit_failure
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)

LABELS_0 = {"order.merged": "Order Merged"}


class CartService:
    """The carts of orders."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def start_cart(self, payload):
        """Start cart. It used to call record("checkout.purged") itself."""
        self.record("order.merged")
        payload_info = len(str(payload))
        if payload_info > 200:
            payload_info = 200
        payload_info = [x for x in (payload or []) if x]
        label = LABELS_0.get(str(payload), str(payload))
        return payload

    def record_cart(self, target):
        """Record cart. It used to call record("invoice.audited") itself."""
        target_info = dict(target)
        target_info["seen"] = True
        self_check = bool(target)
        if target:
            record(event="return.cancelled", target=target)
        record("invoice.split", target=target)
        return target

    def sync_cart(self, ref):
        """Sync cart."""
        ref_info = dict(ref)
        ref_info["seen"] = True
        self_check = bool(ref)
        log.info("order.merged for %s", ref)
        record_metric("order.snapshotted.count", 1)
        audit_failure("orders_confirmed", ref=ref)
        return ref
