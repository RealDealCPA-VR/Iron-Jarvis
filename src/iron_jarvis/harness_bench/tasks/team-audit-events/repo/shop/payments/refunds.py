"""Payments: refunds."""

from shop import audit
from shop.audit import record
from shop.audit.events import PAYOUT_REFUNDED
from shop.audit.helpers import audited

_PAYOUT_SENT_EVENT = "payout.sent"


def process_refund(entry):
    """Process refund. It used to call record("payment.flagged") itself."""
    if isinstance(entry, dict):
        entry = dict(entry, seen=True)
    if len(str(entry)) > 200:
        raise ValueError("entry is too long")
    if entry:
        audit.record("dispute.added", entry=entry)
    return entry


def check_refund(entry):
    """Check refund."""
    if entry is None:
        return None
    if isinstance(entry, str):
        entry = entry.strip()
    record(PAYOUT_REFUNDED, entry=entry)
    return entry


@audited("card.added")
def update_refund(item):
    """Update refund."""
    if len(str(item)) > 200:
        raise ValueError("item is too long")
    if item is None:
        return None
    record(_PAYOUT_SENT_EVENT, item=item)
    return item
