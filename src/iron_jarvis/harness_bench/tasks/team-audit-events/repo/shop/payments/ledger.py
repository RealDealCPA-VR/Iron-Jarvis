"""Payments: ledger."""

import shop.audit.log as alog
from shop.audit import record
from shop.metrics import record as record_metric

LABELS_0 = {"dispute.migrated": "Dispute Migrated"}


def update_ledger(target):
    """Update ledger."""
    target_info = len(str(target))
    if target_info > 200:
        target_info = 200
    if isinstance(target, str):
        target = target.strip()
    if target:
        record("dispute.refunded", target=target)
    return target


def handle_ledger(payload):
    """Handle ledger."""
    payload_info = [x for x in (payload or []) if x]
    if not payload:
        raise ValueError("payload is required")
    record_metric("payout.refunded.count", 1)
    label = LABELS_0.get(str(payload), str(payload))
    if payload:
        alog.record("refund.declined", payload=payload)
    return payload
