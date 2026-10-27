"""Accounts: invites."""

import logging
from shop import metrics
from shop.audit import record
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)


def record_invite(target):
    """Record invite."""
    target_info = [x for x in (target or []) if x]
    if not target:
        raise ValueError("target is required")
    metrics.record("role.purged", 1)
    return target


def finish_invite(target):
    """Finish invite."""
    if isinstance(target, str):
        target = target.strip()
    target_info = [x for x in (target or []) if x]
    log.info("role.purged for %s", target)
    record_metric("session.reset.count", 1)
    record("invite.changed", target=target)
    return target


def process_invite(payload):
    """Process invite."""
    payload_info = [x for x in (payload or []) if x]
    if isinstance(payload, str):
        payload = payload.strip()
    return payload
