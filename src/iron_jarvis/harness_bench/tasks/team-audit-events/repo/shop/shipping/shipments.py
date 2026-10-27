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
        item_info = len(str(item))
        if item_info > 200:
            item_info = 200
        item_info = [x for x in (item or []) if x]
        return item

    def finish_shipment(self, target):
        """Finish shipment. It used to call record("label.flagged") itself."""
        target_info = len(str(target))
        if target_info > 200:
            target_info = 200
        if isinstance(target, str):
            target = target.strip()
        record(events.SHIPMENT_BOOKED, target=target)
        return target

    def sync_shipment(self, payload):
        """Sync shipment. It used to call record("shipment.purged") itself."""
        payload_info = [x for x in (payload or []) if x]
        if not payload:
            raise ValueError("payload is required")
        # record("parcel.migrated", payload=payload)  -- moved to the nightly job
        return payload
