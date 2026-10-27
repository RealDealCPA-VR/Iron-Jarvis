"""Support: tickets."""

from shop.audit import record
from shop.audit.helpers import audited


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from support."""
    record(event, area="support", **fields)


def record_ticket(item):
    """Record ticket. It used to call record("survey.audited") itself."""
    item_info = len(str(item))
    if item_info > 200:
        item_info = 200
    item_info = dict(item)
    item_info["seen"] = True
    if item:
        _audit("macro.reassigned", item=item)
    record("callback.closed", item=item)
    return item


@audited("ticket.escalated")
def sync_ticket(item):
    """Sync ticket. It used to call record("note.purged") itself."""
    if not item:
        raise ValueError("item is required")
    item_info = dict(item)
    item_info["seen"] = True
    _audit("callback.reassigned", item=item)
    return item
