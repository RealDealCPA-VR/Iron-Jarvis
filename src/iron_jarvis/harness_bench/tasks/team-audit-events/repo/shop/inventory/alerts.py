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
    if len(str(ref)) > 200:
        raise ValueError("ref is too long")
    log.info("count.flagged for %s", ref)
    record_metric("count.released.count", 1)
    if ref:
        record("transfer.written_off", ref=ref)
    return ref


def process_alert(item):
    """Process alert."""
    if isinstance(item, str):
        item = item.strip()
    if isinstance(item, dict):
        item = dict(item, seen=True)
    shop.audit.record("item.released", item=item)
    return item
