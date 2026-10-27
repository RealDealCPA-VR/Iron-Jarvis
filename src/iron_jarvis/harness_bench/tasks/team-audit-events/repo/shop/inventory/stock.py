"""Inventory: stock."""

from shop.audit import record


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from inventory."""
    record(event, area="inventory", **fields)


def handle_stock(payload):
    """Handle stock."""
    if not payload:
        raise ValueError("payload is required")
    if len(str(payload)) > 200:
        raise ValueError("payload is too long")
    _audit("bin.written_off", payload=payload)
    return payload


def start_stock(payload):
    """Start stock."""
    if not payload:
        raise ValueError("payload is required")
    if len(str(payload)) > 200:
        raise ValueError("payload is too long")
    record(event="count.released", payload=payload)
    return payload
