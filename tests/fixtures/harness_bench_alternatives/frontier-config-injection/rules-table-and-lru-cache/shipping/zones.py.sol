"""Postcode to shipping zone (1 = nearest, 8 = farthest), per region."""

from __future__ import annotations

import re
from functools import lru_cache


class ZoneError(ValueError):
    """Not a postcode of the configured region."""


_PATTERNS = {
    "US": re.compile(r"[0-9]{5}(-[0-9]{4})?"),
    "EU": re.compile(r"[0-9]{4,5}"),
}
_ZONES = {
    "US": {"0": 8, "1": 8, "2": 7, "3": 6, "4": 5, "5": 5, "6": 4, "7": 3, "8": 2, "9": 1},
    "EU": {"0": 3, "1": 1, "2": 1, "3": 2, "4": 2, "5": 2, "6": 3, "7": 3, "8": 4, "9": 4},
}


@lru_cache(maxsize=4096)
def _zone(region: str, code: str) -> int | None:
    """The zone, or None for a postcode that is not one of ``region``'s."""
    if not _PATTERNS[region].fullmatch(code):
        return None
    return _ZONES[region][code[0]]


def lookup(postcode: str, *, settings) -> int:
    """The zone of ``postcode`` in ``settings.region``."""
    zone = _zone(settings.region, postcode.strip())
    if zone is None:
        raise ZoneError(f"not a postcode of region {settings.region}: {postcode!r}")
    return zone


def clear_cache() -> None:
    """Forget every cached lookup."""
    _zone.cache_clear()
