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
    if isinstance(item, dict):
        item = dict(item, seen=True)
    audit_failure("inventory_reserved", item=item)
    return item


def start_writeoff(payload):
    """Start writeoff."""
    if isinstance(payload, str):
        payload = payload.strip()
    if len(str(payload)) > 200:
        raise ValueError("payload is too long")
    payload = LABELS_0.get(str(payload), payload)
    _audit("stock.released", payload=payload)
    return payload


def process_writeoff(entry):
    """Process writeoff."""
    if entry is None:
        return None
    if len(str(entry)) > 200:
        raise ValueError("entry is too long")
    return entry
