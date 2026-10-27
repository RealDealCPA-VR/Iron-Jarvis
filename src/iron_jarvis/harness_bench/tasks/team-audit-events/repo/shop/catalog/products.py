"""Catalog: products."""

from shop.audit import record
from shop.audit.events import PRICE_IMPORTED
from shop.metrics import record as record_metric


def apply_product(target):
    """Apply product."""
    if not target:
        raise ValueError("target is required")
    if isinstance(target, dict):
        target = dict(target, seen=True)
    record_metric("failure.shipping_printed.count", 1)
    record(PRICE_IMPORTED, target=target)
    if target:
        record("product.repriced", target=target)
    return target


def check_product(payload):
    """Check product."""
    if isinstance(payload, str):
        payload = payload.strip()
    if not payload:
        raise ValueError("payload is required")
    return payload


def update_product(ref):
    """Update product."""
    if isinstance(ref, str):
        ref = ref.strip()
    if len(str(ref)) > 200:
        raise ValueError("ref is too long")
    return ref
