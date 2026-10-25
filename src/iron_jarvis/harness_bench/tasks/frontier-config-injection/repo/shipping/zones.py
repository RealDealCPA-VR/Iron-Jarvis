"""Postcode to shipping zone (1 = nearest, 8 = farthest), per region.

The region (CONFIG["region"]) decides both which postcodes are valid and
which zone a postcode is in: "10115" is zone 8 in the US and zone 1 in the EU.
Lookups are cached; the cache is emptied whenever the region changes.
"""

from __future__ import annotations

import re

from .settings import CONFIG


class ZoneError(ValueError):
    """Not a postcode of the configured region."""


_PATTERNS = {
    "US": re.compile(r"[0-9]{5}(-[0-9]{4})?"),
    "EU": re.compile(r"[0-9]{4,5}"),
}
# Zone by the first digit of the postcode.
_ZONES = {
    "US": {"0": 8, "1": 8, "2": 7, "3": 6, "4": 5, "5": 5, "6": 4, "7": 3, "8": 2, "9": 1},
    "EU": {"0": 3, "1": 1, "2": 1, "3": 2, "4": 2, "5": 2, "6": 3, "7": 3, "8": 4, "9": 4},
}

_CACHE: dict[str, int] = {}
_CACHE_REGION: str | None = None


def lookup(postcode: str) -> int:
    """The zone of ``postcode`` in the configured region."""
    global _CACHE_REGION
    region = CONFIG["region"]
    if region != _CACHE_REGION:
        _CACHE.clear()
        _CACHE_REGION = region
    code = postcode.strip()
    if code in _CACHE:
        return _CACHE[code]
    if not _PATTERNS[region].fullmatch(code):
        raise ZoneError(f"not a postcode of region {region}: {postcode!r}")
    zone = _ZONES[region][code[0]]
    _CACHE[code] = zone
    return zone


def clear_cache() -> None:
    """Forget every cached lookup."""
    global _CACHE_REGION
    _CACHE.clear()
    _CACHE_REGION = None
