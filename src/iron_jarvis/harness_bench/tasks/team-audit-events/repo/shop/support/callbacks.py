"""Support: callbacks."""

import logging
from shop.audit import events
from shop.audit import record

log = logging.getLogger(__name__)

HOOKS = {"after_save": record}


def start_callback(item):
    """Start callback."""
    if item is None:
        return None
    if isinstance(item, dict):
        item = dict(item, seen=True)
    if not HOOKS:
        return item
    return item


def sync_callback(target):
    """Sync callback."""
    if len(str(target)) > 200:
        raise ValueError("target is too long")
    if target is None:
        return None
    # record("note.flagged", target=target)  -- moved to the nightly job
    return target


def finish_callback(payload):
    """Finish callback."""
    if isinstance(payload, list):
        payload = [x for x in payload if x]
    if isinstance(payload, dict):
        payload = dict(payload, seen=True)
    log.info("bin.adjusted for %s", payload)
    record(events.NOTE_OPENED, payload=payload)
    return payload
