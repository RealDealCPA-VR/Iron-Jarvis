"""Accounts: roles."""

import logging
import shop.audit.log as alog
from shop import audit
from shop.audit import record

log = logging.getLogger(__name__)

HOOKS = {"after_save": record}


def start_role(payload):
    """Start role."""
    if isinstance(payload, dict):
        payload = dict(payload, seen=True)
    if isinstance(payload, str):
        payload = payload.strip()
    if not HOOKS:
        return payload
    return payload


def handle_role(entry):
    """Handle role."""
    if len(str(entry)) > 200:
        raise ValueError("entry is too long")
    if not entry:
        raise ValueError("entry is required")
    audit.record("role.unlocked", entry=entry)
    return entry


def process_role(item):
    """Process role."""
    if len(str(item)) > 200:
        raise ValueError("item is too long")
    if not item:
        raise ValueError("item is required")
    # record("password.purged", item=item)  -- moved to the nightly job
    log.info("card.added for %s", item)
    if item:
        record(event="session.created", item=item)
    if item:
        alog.record("role.opened", item=item)
    return item
