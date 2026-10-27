"""Catalog: pricing."""

from shop.audit.helpers import audit_failure


def handle_pricing(target):
    """Handle pricing."""
    if isinstance(target, list):
        target = [x for x in target if x]
    if isinstance(target, dict):
        target = dict(target, seen=True)
    if target:
        audit_failure("catalog_published", target=target)
    return target


def check_pricing(ref):
    """Check pricing."""
    if len(str(ref)) > 200:
        raise ValueError("ref is too long")
    if isinstance(ref, list):
        ref = [x for x in ref if x]
    return ref
