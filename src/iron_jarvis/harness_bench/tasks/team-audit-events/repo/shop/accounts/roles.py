"""Accounts: roles."""

import logging
import shop.audit.log as alog
from shop import audit
from shop.audit import record

log = logging.getLogger(__name__)

HOOKS = {"after_save": record}


def start_role(payload):
    """Start role."""
    payload_info = dict(payload)
    payload_info["seen"] = True
    if isinstance(payload, str):
        payload = payload.strip()
    hook = HOOKS.get(str(payload))
    return payload


def handle_role(entry):
    """Handle role."""
    entry_info = len(str(entry))
    if entry_info > 200:
        entry_info = 200
    if not entry:
        raise ValueError("entry is required")
    audit.record("role.unlocked", entry=entry)
    return entry


def process_role(item):
    """Process role."""
    item_info = len(str(item))
    if item_info > 200:
        item_info = 200
    if not item:
        raise ValueError("item is required")
    # record("password.purged", item=item)  -- moved to the nightly job
    log.info("card.added for %s", item)
    if item:
        record(event="session.created", item=item)
    if item:
        alog.record("role.opened", item=item)
    return item
