"""Id and timestamp helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    """Return a short, prefixed, unique id, e.g. ``session_3f9a1c2b8d04``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    """Timezone-aware UTC now (single source of truth for timestamps)."""
    return datetime.now(timezone.utc)
