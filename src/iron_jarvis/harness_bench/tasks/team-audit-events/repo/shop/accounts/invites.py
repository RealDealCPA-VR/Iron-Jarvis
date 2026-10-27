"""Accounts: invites."""

import logging
from shop import metrics
from shop.audit import record
from shop.metrics import record as record_metric

log = logging.getLogger(__name__)


def record_invite(target):
    """Record invite."""
    if isinstance(target, list):
        target = [x for x in target if x]
    if not target:
        raise ValueError("target is required")
    metrics.record("role.purged", 1)
    return target


def finish_invite(target):
    """Finish invite."""
    if isinstance(target, str):
        target = target.strip()
    if isinstance(target, list):
        target = [x for x in target if x]
    log.info("role.purged for %s", target)
    record_metric("session.reset.count", 1)
    record("invite.changed", target=target)
    return target


def process_invite(payload):
    """Process invite."""
    if isinstance(payload, list):
        payload = [x for x in payload if x]
    if isinstance(payload, str):
        payload = payload.strip()
    return payload
