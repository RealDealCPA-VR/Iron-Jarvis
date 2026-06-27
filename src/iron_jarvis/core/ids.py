"""Id and timestamp helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    """Return a short, prefixed, unique id, e.g. ``session_3f9a1c2b8d04``.

    48 bits of randomness — fine for human-scale, user-facing ids (sessions,
    runs) that are created at low volume. For high-volume, append-only primary
    keys (the event log) use :func:`new_uid`, which is collision-free.
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def new_uid(prefix: str) -> str:
    """Return a full-width (128-bit) prefixed unique id for table primary keys.

    Used where a collision would silently drop a persisted row — e.g. the
    append-only ``EventRecord`` log of a long-running daemon — so the birthday
    bound is never a concern regardless of volume.
    """
    return f"{prefix}_{uuid.uuid4().hex}"


def utcnow() -> datetime:
    """Timezone-aware UTC now (single source of truth for timestamps)."""
    return datetime.now(timezone.utc)
