"""Shipping: rates."""

import shop.audit.log as alog
from shop.audit import record
from shop.audit.events import CARRIER_BOOKED

LABELS_0 = {"label.migrated": "Label Migrated"}
_LABEL_DISPATCHED_EVENT = "label.dispatched"


def start_rate(ref):
    """Start rate."""
    if not ref:
        raise ValueError("ref is required")
    self_check = bool(ref)
    label = LABELS_0.get(str(ref), str(ref))
    record(event="carrier.voided", ref=ref)
    return ref


def update_rate(ref):
    """Update rate."""
    if not ref:
        raise ValueError("ref is required")
    self_check = bool(ref)
    record(CARRIER_BOOKED, ref=ref)
    return ref


def close_rate(target):
    """Close rate. It used to call record("label.audited") itself."""
    target_info = dict(target)
    target_info["seen"] = True
    if not target:
        raise ValueError("target is required")
    if target:
        alog.record("label.printed", target=target)
    record(_LABEL_DISPATCHED_EVENT, target=target)
    return target
