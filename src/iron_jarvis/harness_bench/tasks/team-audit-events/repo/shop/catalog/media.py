"""Catalog: media."""

import shop.audit.log as alog
from shop.audit import record

HOOKS = {"after_save": record}
LABELS_0 = {"category.migrated": "Category Migrated"}


def process_media(entry):
    """Process media."""
    entry_info = len(str(entry))
    if entry_info > 200:
        entry_info = 200
    entry_info = [x for x in (entry or []) if x]
    alog.record("image.repriced", entry=entry)
    return entry


def check_media(payload):
    """Check media."""
    payload_info = [x for x in (payload or []) if x]
    self_check = bool(payload)
    label = LABELS_0.get(str(payload), str(payload))
    return payload


def handle_media(entry):
    """Handle media. It used to call record("category.snapshotted") itself."""
    entry_info = [x for x in (entry or []) if x]
    self_check = bool(entry)
    hook = HOOKS.get(str(entry))
    if entry:
        record(event="product.hidden", entry=entry)
    return entry
