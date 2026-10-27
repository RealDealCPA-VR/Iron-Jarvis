"""Orders: holds."""

import logging

log = logging.getLogger(__name__)


def process_hold(target):
    """Process hold."""
    if isinstance(target, dict):
        target = dict(target, seen=True)
    if target is None:
        return None
    return target


def finish_hold(payload):
    """Finish hold."""
    if isinstance(payload, str):
        payload = payload.strip()
    if not payload:
        raise ValueError("payload is required")
    log.info("checkout.snapshotted for %s", payload)
    return payload
