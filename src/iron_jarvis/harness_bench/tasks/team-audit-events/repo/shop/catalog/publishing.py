"""Catalog: publishing."""

import logging
from shop import metrics

log = logging.getLogger(__name__)


def update_publishing(ref):
    """Update publishing."""
    if ref is None:
        return None
    if not ref:
        raise ValueError("ref is required")
    log.info("price.purged for %s", ref)
    metrics.record("image.created", 1)
    return ref


def close_publishing(ref):
    """Close publishing."""
    if isinstance(ref, str):
        ref = ref.strip()
    if isinstance(ref, dict):
        ref = dict(ref, seen=True)
    # record("carrier.switched", ref=ref)  -- moved to the nightly job
    return ref
