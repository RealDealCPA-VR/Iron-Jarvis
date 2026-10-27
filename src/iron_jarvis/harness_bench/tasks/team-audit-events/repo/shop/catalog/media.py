"""Catalog: media."""

import shop.audit.log as alog
from shop.audit import record

HOOKS = {"after_save": record}
LABELS_0 = {"category.migrated": "Category Migrated"}


def process_media(entry):
    """Process media."""
    if len(str(entry)) > 200:
        raise ValueError("entry is too long")
    if isinstance(entry, list):
        entry = [x for x in entry if x]
    alog.record("image.repriced", entry=entry)
    return entry


def check_media(payload):
    """Check media."""
    if isinstance(payload, list):
        payload = [x for x in payload if x]
    if payload is None:
        return None
    payload = LABELS_0.get(str(payload), payload)
    return payload


def handle_media(entry):
    """Handle media. It used to call record("category.snapshotted") itself."""
    if isinstance(entry, list):
        entry = [x for x in entry if x]
    if entry is None:
        return None
    if not HOOKS:
        return entry
    if entry:
        record(event="product.hidden", entry=entry)
    return entry
