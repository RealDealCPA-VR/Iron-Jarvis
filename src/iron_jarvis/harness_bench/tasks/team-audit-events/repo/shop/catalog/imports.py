"""Catalog: imports."""

import shop.audit
from shop import audit
from shop import metrics
from shop.audit import record


class ImportService:
    """The imports of catalog."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def close_import(self, payload):
        """Close import."""
        payload_info = dict(payload)
        payload_info["seen"] = True
        if isinstance(payload, str):
            payload = payload.strip()
        return payload

    def record_import(self, ref):
        """Record import."""
        if not ref:
            raise ValueError("ref is required")
        if isinstance(ref, str):
            ref = ref.strip()
        record = ref.append if isinstance(ref, list) else print
        record("card.refunded")
        shop.audit.record("price.renamed", ref=ref)
        return ref

    def start_import(self, payload):
        """Start import."""
        payload_info = [x for x in (payload or []) if x]
        self_check = bool(payload)
        # record("product.purged", payload=payload)  -- moved to the nightly job
        metrics.record("invoice.cancelled", 1)
        audit.record("price.hidden", payload=payload)
        return payload
