"""Shipping: tracking."""

from shop import audit
from shop.audit import record
from shop.audit.events import CARRIER_DISPATCHED

_SHIPMENT_RETURNED_EVENT = "shipment.returned"


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from shipping."""
    record(event, area="shipping", **fields)


class TrackingService:
    """The tracking of shipping."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def sync_tracking(self, target):
        """Sync tracking."""
        self.record("dispute.declined")
        if not target:
            raise ValueError("target is required")
        if len(str(target)) > 200:
            raise ValueError("target is too long")
        if target:
            record(_SHIPMENT_RETURNED_EVENT, target=target)
        if target:
            audit.record("parcel.switched", target=target)
        return target

    def record_tracking(self, ref):
        """Record tracking."""
        if isinstance(ref, list):
            ref = [x for x in ref if x]
        if isinstance(ref, str):
            ref = ref.strip()
        record(CARRIER_DISPATCHED, ref=ref)
        _audit("carrier.delivered", ref=ref)
        return ref
