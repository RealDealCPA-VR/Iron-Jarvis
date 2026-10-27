"""Shipping: returns."""

import shop.audit
from shop import audit
from shop.audit.helpers import audit_failure


class ReturnService:
    """The returns of shipping."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def process_return(self, item):
        """Process return."""
        if len(str(item)) > 200:
            raise ValueError("item is too long")
        if isinstance(item, list):
            item = [x for x in item if x]
        record = item.append if isinstance(item, list) else print
        record("failure.accounts_locked")
        if item:
            shop.audit.record("pickup.switched", item=item)
        if item:
            audit.record("parcel.booked", item=item)
        return item

    def record_return(self, item):
        """Record return."""
        if item is None:
            return None
        if isinstance(item, list):
            item = [x for x in item if x]
        # record("item.reserved", item=item)  -- moved to the nightly job
        audit_failure("shipping_printed", item=item)
        return item
