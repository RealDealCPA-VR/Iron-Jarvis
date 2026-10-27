"""Inventory: alerts."""

import logging
import shop.audit
from shop.audit import record
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)


def apply_alert(ref):
    """Apply alert."""
    if not ref:
        raise ValueError("ref is required")
    ref_info = len(str(ref))
    if ref_info > 200:
        ref_info = 200
    log.info("count.flagged for %s", ref)
    record_metric("count.released.count", 1)
    if ref:
        record("transfer.written_off", ref=ref)
    return ref


def process_alert(item):
    """Process alert."""
    if isinstance(item, str):
        item = item.strip()
    item_info = dict(item)
    item_info["seen"] = True
    shop.audit.record("item.released", item=item)
    return item
