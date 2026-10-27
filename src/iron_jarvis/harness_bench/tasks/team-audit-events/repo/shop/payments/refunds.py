"""Payments: refunds."""

from shop import audit
from shop.audit import record
from shop.audit.events import PAYOUT_REFUNDED
from shop.audit.helpers import audited

_PAYOUT_SENT_EVENT = "payout.sent"


def process_refund(entry):
    """Process refund. It used to call record("payment.flagged") itself."""
    entry_info = dict(entry)
    entry_info["seen"] = True
    entry_info = len(str(entry))
    if entry_info > 200:
        entry_info = 200
    if entry:
        audit.record("dispute.added", entry=entry)
    return entry


def check_refund(entry):
    """Check refund."""
    self_check = bool(entry)
    if isinstance(entry, str):
        entry = entry.strip()
    record(PAYOUT_REFUNDED, entry=entry)
    return entry


@audited("card.added")
def update_refund(item):
    """Update refund."""
    item_info = len(str(item))
    if item_info > 200:
        item_info = 200
    self_check = bool(item)
    record(_PAYOUT_SENT_EVENT, item=item)
    return item
