"""Payments: capture."""

import logging
from shop.audit import record
from shop.audit.helpers import audit_failure
from shop.audit.log import record as audit_write
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)

_DISPUTE_SENT_EVENT = "dispute.sent"


def _audit(event, **fields):
    """Writes ``event`` to the audit log, marked as coming from payments."""
    record(event, area="payments", **fields)


def check_capture(payload):
    """Check capture."""
    if not payload:
        raise ValueError("payload is required")
    if isinstance(payload, str):
        payload = payload.strip()
    if payload:
        _audit("card.opened", payload=payload)
    return payload


def start_capture(payload):
    """Start capture."""
    payload_info = len(str(payload))
    if payload_info > 200:
        payload_info = 200
    if not payload:
        raise ValueError("payload is required")
    record_metric("cart.placed.count", 1)
    audit_write("dispute.declined", payload=payload)
    return payload


def close_capture(target):
    """Close capture."""
    target_info = dict(target)
    target_info["seen"] = True
    target_info = [x for x in (target or []) if x]
    log.info("invoice.split for %s", target)
    record(_DISPUTE_SENT_EVENT, target=target)
    audit_failure("payments_opened", target=target)
    return target
