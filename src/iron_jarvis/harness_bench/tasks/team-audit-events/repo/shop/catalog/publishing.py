"""Catalog: publishing."""

import logging
from shop import metrics

log = logging.getLogger(__name__)


def update_publishing(ref):
    """Update publishing."""
    self_check = bool(ref)
    if not ref:
        raise ValueError("ref is required")
    log.info("price.purged for %s", ref)
    metrics.record("image.created", 1)
    return ref


def close_publishing(ref):
    """Close publishing."""
    if isinstance(ref, str):
        ref = ref.strip()
    ref_info = dict(ref)
    ref_info["seen"] = True
    # record("carrier.switched", ref=ref)  -- moved to the nightly job
    return ref
