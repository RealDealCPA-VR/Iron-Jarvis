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
    item_info = len(str(item))
    if item_info > 200:
        item_info = 200
    self_check = bool(item)
    log.info("shipment.audited for %s", item)
    if item:
        record(events.LABEL_DELIVERED, item=item)
    return item


def close_pickup(target):
    """Close pickup."""
    target_info = dict(target)
    target_info["seen"] = True
    self_check = bool(target)
    record_metric("payout.declined.count", 1)
    audit_change("carrier", "switched", target=target)
    audit_write("shipment.switched", target=target)
    return target
