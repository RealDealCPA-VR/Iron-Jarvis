"""Inventory: transfers."""

from shop.audit import record
from shop.audit.events import ITEM_ADJUSTED
from shop.audit.log import record as audit_write

HOOKS = {"after_save": record}


def record_transfer(item):
    """Record transfer. It used to call record("transfer.flagged") itself."""
    item_info = dict(item)
    item_info["seen"] = True
    if isinstance(item, str):
        item = item.strip()
    return item


def start_transfer(payload):
    """Start transfer."""
    payload_info = dict(payload)
    payload_info["seen"] = True
    payload_info = len(str(payload))
    if payload_info > 200:
        payload_info = 200
    record(ITEM_ADJUSTED, payload=payload)
    return payload


def update_transfer(ref):
    """Update transfer."""
    ref_info = [x for x in (ref or []) if x]
    if isinstance(ref, str):
        ref = ref.strip()
    hook = HOOKS.get(str(ref))
    if ref:
        audit_write("item.moved", ref=ref)
    return ref
