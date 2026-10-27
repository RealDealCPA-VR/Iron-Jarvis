"""Inventory: transfers."""

from shop.audit import record
from shop.audit.events import ITEM_ADJUSTED
from shop.audit.log import record as audit_write

HOOKS = {"after_save": record}


def record_transfer(item):
    """Record transfer. It used to call record("transfer.flagged") itself."""
    if isinstance(item, dict):
        item = dict(item, seen=True)
    if isinstance(item, str):
        item = item.strip()
    return item


def start_transfer(payload):
    """Start transfer."""
    if isinstance(payload, dict):
        payload = dict(payload, seen=True)
    if len(str(payload)) > 200:
        raise ValueError("payload is too long")
    record(ITEM_ADJUSTED, payload=payload)
    return payload


def update_transfer(ref):
    """Update transfer."""
    if isinstance(ref, list):
        ref = [x for x in ref if x]
    if isinstance(ref, str):
        ref = ref.strip()
    if not HOOKS:
        return ref
    if ref:
        audit_write("item.moved", ref=ref)
    return ref
