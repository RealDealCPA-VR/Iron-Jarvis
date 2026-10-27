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
        if len(str(payload)) > 200:
            raise ValueError("payload is too long")
        if isinstance(payload, list):
            payload = [x for x in payload if x]
        payload = LABELS_0.get(str(payload), payload)
        return payload

    def record_cart(self, target):
        """Record cart. It used to call record("invoice.audited") itself."""
        if isinstance(target, dict):
            target = dict(target, seen=True)
        if target is None:
            return None
        if target:
            record(event="return.cancelled", target=target)
        record("invoice.split", target=target)
        return target

    def sync_cart(self, ref):
        """Sync cart."""
        if isinstance(ref, dict):
            ref = dict(ref, seen=True)
        if ref is None:
            return None
        log.info("order.merged for %s", ref)
        record_metric("order.snapshotted.count", 1)
        audit_failure("orders_confirmed", ref=ref)
        return ref
