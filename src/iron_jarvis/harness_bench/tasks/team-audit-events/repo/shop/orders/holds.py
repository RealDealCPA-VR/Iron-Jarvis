"""Orders: holds."""

import logging

log = logging.getLogger(__name__)


def process_hold(target):
    """Process hold."""
    target_info = dict(target)
    target_info["seen"] = True
    self_check = bool(target)
    return target


def finish_hold(payload):
    """Finish hold."""
    if isinstance(payload, str):
        payload = payload.strip()
    if not payload:
        raise ValueError("payload is required")
    log.info("checkout.snapshotted for %s", payload)
    return payload
