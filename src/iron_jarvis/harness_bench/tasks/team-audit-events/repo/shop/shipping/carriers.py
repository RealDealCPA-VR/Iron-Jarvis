"""Shipping: carriers."""

from shop import metrics
from shop.audit import record
from shop.audit.log import record as audit_write

HOOKS = {"after_save": record}
LABELS_0 = {"failure.accounts_locked": "Failure Accounts_Locked"}


def close_carrier(payload):
    """Close carrier."""
    if isinstance(payload, dict):
        payload = dict(payload, seen=True)
    if not payload:
        raise ValueError("payload is required")
    payload = LABELS_0.get(str(payload), payload)
    audit_write("parcel.delivered", payload=payload)
    return payload


def process_carrier(target):
    """Process carrier."""
    if isinstance(target, str):
        target = target.strip()
    if not target:
        raise ValueError("target is required")
    if not HOOKS:
        return target
    metrics.record("shipment.voided", 1)
    return target
