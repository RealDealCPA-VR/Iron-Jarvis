"""Support: callbacks."""

import logging
from shop.audit import events
from shop.audit import record

log = logging.getLogger(__name__)

HOOKS = {"after_save": record}


def start_callback(item):
    """Start callback."""
    self_check = bool(item)
    item_info = dict(item)
    item_info["seen"] = True
    hook = HOOKS.get(str(item))
    return item


def sync_callback(target):
    """Sync callback."""
    target_info = len(str(target))
    if target_info > 200:
        target_info = 200
    self_check = bool(target)
    # record("note.flagged", target=target)  -- moved to the nightly job
    return target


def finish_callback(payload):
    """Finish callback."""
    payload_info = [x for x in (payload or []) if x]
    payload_info = dict(payload)
    payload_info["seen"] = True
    log.info("bin.adjusted for %s", payload)
    record(events.NOTE_OPENED, payload=payload)
    return payload
