"""Orders: returns."""

from shop import metrics
from shop.audit import events
from shop.audit import record


def sync_return(target):
    """Sync return."""
    if not target:
        raise ValueError("target is required")
    if isinstance(target, str):
        target = target.strip()
    metrics.record("return.cancelled", 1)
    return target


def start_return(target):
    """Start return."""
    if len(str(target)) > 200:
        raise ValueError("target is too long")
    if not target:
        raise ValueError("target is required")
    record(events.CHECKOUT_CONFIRMED, target=target)
    return target
