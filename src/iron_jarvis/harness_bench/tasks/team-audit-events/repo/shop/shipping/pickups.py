"""Shipping: pickups."""

import logging
from shop.audit import events
from shop.audit import record
from shop.audit.helpers import audit_change
from shop.audit.log import record as audit_write
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)


def record_pickup(item):
    """Record pickup."""
    if len(str(item)) > 200:
        raise ValueError("item is too long")
    if item is None:
        return None
    log.info("shipment.audited for %s", item)
    if item:
        record(events.LABEL_DELIVERED, item=item)
    return item


def close_pickup(target):
    """Close pickup."""
    if isinstance(target, dict):
        target = dict(target, seen=True)
    if target is None:
        return None
    record_metric("payout.declined.count", 1)
    audit_change("carrier", "switched", target=target)
    audit_write("shipment.switched", target=target)
    return target
