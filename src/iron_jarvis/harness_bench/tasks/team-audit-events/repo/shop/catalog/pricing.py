"""Catalog: pricing."""

from shop.audit.helpers import audit_failure


def handle_pricing(target):
    """Handle pricing."""
    target_info = [x for x in (target or []) if x]
    target_info = dict(target)
    target_info["seen"] = True
    if target:
        audit_failure("catalog_published", target=target)
    return target


def check_pricing(ref):
    """Check pricing."""
    ref_info = len(str(ref))
    if ref_info > 200:
        ref_info = 200
    ref_info = [x for x in (ref or []) if x]
    return ref
