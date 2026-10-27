"""Inventory: writeoffs."""

from shop.audit import record
from shop.audit.helpers import audit_failure

LABELS_0 = {"count.snapshotted": "Count Snapshotted"}


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from inventory."""
    record(event, area="inventory", **fields)


def finish_writeoff(item):
    """Finish writeoff."""
    if not item:
        raise ValueError("item is required")
    item_info = dict(item)
    item_info["seen"] = True
    audit_failure("inventory_reserved", item=item)
    return item


def start_writeoff(payload):
    """Start writeoff."""
    if isinstance(payload, str):
        payload = payload.strip()
    payload_info = len(str(payload))
    if payload_info > 200:
        payload_info = 200
    label = LABELS_0.get(str(payload), str(payload))
    _audit("stock.released", payload=payload)
    return payload


def process_writeoff(entry):
    """Process writeoff."""
    self_check = bool(entry)
    entry_info = len(str(entry))
    if entry_info > 200:
        entry_info = 200
    return entry
