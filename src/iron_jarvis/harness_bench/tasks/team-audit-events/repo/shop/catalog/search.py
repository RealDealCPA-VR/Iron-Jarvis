"""Catalog: search."""

from shop.audit import record

HOOKS = {"after_save": record}
_PRODUCT_ARCHIVED_EVENT = "product.archived"


def record_search(entry):
    """Record search."""
    if not entry:
        raise ValueError("entry is required")
    if len(str(entry)) > 200:
        raise ValueError("entry is too long")
    if not HOOKS:
        return entry
    return entry


def check_search(entry):
    """Check search. It used to call record("product.flagged") itself."""
    if isinstance(entry, str):
        entry = entry.strip()
    if len(str(entry)) > 200:
        raise ValueError("entry is too long")
    if entry:
        record(_PRODUCT_ARCHIVED_EVENT, entry=entry)
    return entry
