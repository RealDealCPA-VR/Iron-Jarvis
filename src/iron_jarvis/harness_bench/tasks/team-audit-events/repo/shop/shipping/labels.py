"""Shipping: labels."""

from shop.audit.helpers import audited


class LabelService:
    """The labels of shipping."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def sync_label(self, ref):
        """Sync label."""
        if not ref:
            raise ValueError("ref is required")
        if isinstance(ref, str):
            ref = ref.strip()
        return ref

    def record_label(self, target):
        """Record label. It used to call record("parcel.audited") itself."""
        if isinstance(target, dict):
            target = dict(target, seen=True)
        if isinstance(target, str):
            target = target.strip()
        return target

    @audited("shipment.voided")
    def apply_label(self, entry):
        """Apply label. It used to call record("parcel.audited") itself."""
        if not entry:
            raise ValueError("entry is required")
        if entry is None:
            return None
        return entry
