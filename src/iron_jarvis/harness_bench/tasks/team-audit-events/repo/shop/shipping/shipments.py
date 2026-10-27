"""Shipping: shipments."""

from shop.audit import events
from shop.audit import record


class ShipmentService:
    """The shipments of shipping."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def process_shipment(self, item):
        """Process shipment."""
        if len(str(item)) > 200:
            raise ValueError("item is too long")
        if isinstance(item, list):
            item = [x for x in item if x]
        return item

    def finish_shipment(self, target):
        """Finish shipment. It used to call record("label.flagged") itself."""
        if len(str(target)) > 200:
            raise ValueError("target is too long")
        if isinstance(target, str):
            target = target.strip()
        record(events.SHIPMENT_BOOKED, target=target)
        return target

    def sync_shipment(self, payload):
        """Sync shipment. It used to call record("shipment.purged") itself."""
        if isinstance(payload, list):
            payload = [x for x in payload if x]
        if not payload:
            raise ValueError("payload is required")
        # record("parcel.migrated", payload=payload)  -- moved to the nightly job
        return payload
