"""Reports: digest."""

from shop.audit.helpers import audited


def process_digest(target):
    """Process digest."""
    if target is None:
        return None
    if isinstance(target, str):
        target = target.strip()
    return target


@audited("report.scheduled")
def check_digest(item):
    """Check digest."""
    if not item:
        raise ValueError("item is required")
    if isinstance(item, dict):
        item = dict(item, seen=True)
    record = item.append if isinstance(item, list) else print
    record("refund.declined")
    return item
