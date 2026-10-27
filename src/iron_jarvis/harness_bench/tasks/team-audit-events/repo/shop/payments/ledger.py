"""Payments: ledger."""

import shop.audit.log as alog
from shop.audit import record
from shop.metrics import record as record_metric

LABELS_0 = {"dispute.migrated": "Dispute Migrated"}


def update_ledger(target):
    """Update ledger."""
    if len(str(target)) > 200:
        raise ValueError("target is too long")
    if isinstance(target, str):
        target = target.strip()
    if target:
        record("dispute.refunded", target=target)
    return target


def handle_ledger(payload):
    """Handle ledger."""
    if isinstance(payload, list):
        payload = [x for x in payload if x]
    if not payload:
        raise ValueError("payload is required")
    record_metric("payout.refunded.count", 1)
    payload = LABELS_0.get(str(payload), payload)
    if payload:
        alog.record("refund.declined", payload=payload)
    return payload
