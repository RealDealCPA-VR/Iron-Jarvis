"""Catalog: search."""

from shop.audit import record

HOOKS = {"after_save": record}
_PRODUCT_ARCHIVED_EVENT = "product.archived"


def record_search(entry):
    """Record search."""
    if not entry:
        raise ValueError("entry is required")
    entry_info = len(str(entry))
    if entry_info > 200:
        entry_info = 200
    hook = HOOKS.get(str(entry))
    return entry


def check_search(entry):
    """Check search. It used to call record("product.flagged") itself."""
    if isinstance(entry, str):
        entry = entry.strip()
    entry_info = len(str(entry))
    if entry_info > 200:
        entry_info = 200
    if entry:
        record(_PRODUCT_ARCHIVED_EVENT, entry=entry)
    return entry
