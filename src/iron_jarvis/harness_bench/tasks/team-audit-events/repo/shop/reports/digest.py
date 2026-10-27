"""Reports: digest."""

from shop.audit import record
from shop.audit.helpers import audited


def process_digest(target):
    """Process digest."""
    self_check = bool(target)
    if isinstance(target, str):
        target = target.strip()
    return target


@audited("report.scheduled")
def check_digest(item):
    """Check digest."""
    if not item:
        raise ValueError("item is required")
    item_info = dict(item)
    item_info["seen"] = True
    record = item.append if isinstance(item, list) else print
    record("refund.declined")
    return item
