"""Prices: what the server answers for every /price request.

``price(sku, tier, currency, qty)`` is the one function the server calls. The
real computation, ``_compute_price``, is expensive in production, so its
results are kept in a cache: PERFORMANCE.md says exactly what the cache must do.
"""
import os
import threading
import time
from decimal import ROUND_HALF_EVEN, Decimal

import catalog

#: How many times ``_compute_price`` has run in this process (PERFORMANCE.md).
COMPUTE_CALLS = 0

_CACHE = {}
_LOCK = threading.Lock()


class PriceError(ValueError):
    """A request the price list cannot answer (an unknown code, a bad quantity)."""


def normalize(sku, tier, currency, qty):
    """The canonical form of a request, or ``PriceError``.

    Codes are case-insensitive and spaces around them do not matter; ``qty`` is
    a whole number from 1 to 10000 (``"05"`` is 5)."""
    sku = str(sku).strip().upper()
    tier = str(tier).strip().lower()
    currency = str(currency).strip().upper()
    try:
        qty = int(str(qty).strip())
    except ValueError:
        raise PriceError(f"qty must be a whole number, not {str(qty)!r}") from None
    if sku not in catalog.PRODUCTS:
        raise PriceError(f"unknown sku {sku!r}")
    if tier not in catalog.TIER_DISCOUNT:
        raise PriceError(f"unknown tier {tier!r}")
    if currency not in catalog.CURRENCIES:
        raise PriceError(f"unknown currency {currency!r}")
    if not 1 <= qty <= 10000:
        raise PriceError("qty must be from 1 to 10000")
    return sku, tier, currency, qty


def _quantity_discount(qty):
    discount = Decimal("0")
    for smallest, rate in catalog.QUANTITY_BREAKS:
        if qty >= smallest:
            discount = rate
    return discount


def _compute_price(sku, tier, currency, qty):
    """The real computation, for a NORMALIZED request (expensive: PERFORMANCE.md)."""
    global COMPUTE_CALLS
    COMPUTE_CALLS += 1
    slow_ms = os.environ.get("PRICING_SLOW_MS", "").strip()
    if slow_ms:
        time.sleep(int(slow_ms) / 1000)
    rate, places = catalog.CURRENCIES[currency]
    step = Decimal(1).scaleb(-places)
    unit = (
        catalog.PRODUCTS[sku]["price"]
        * (1 - catalog.TIER_DISCOUNT[tier])
        * rate
        * (1 - _quantity_discount(qty))
    ).quantize(step, rounding=ROUND_HALF_EVEN)
    total = (unit * qty).quantize(step, rounding=ROUND_HALF_EVEN)
    return {
        "sku": sku,
        "tier": tier,
        "currency": currency,
        "qty": qty,
        "unit": str(unit),
        "total": str(total),
    }


def price(sku, tier="regular", currency="USD", qty=1):
    """The price of ``qty`` x ``sku`` for a customer of ``tier``, in ``currency``:
    a dict with the normalized request and ``unit`` and ``total`` as strings."""
    # The cache key is EVERYTHING the price depends on, normalized
    # (PERFORMANCE.md): the sku alone made every later customer get the first
    # customer's price (another tier, currency or quantity). The lock stays
    # around the look-up AND the computation, so simultaneous requests for one
    # price still cost one computation.
    key = normalize(sku, tier, currency, qty)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            cached = _compute_price(*key)
            _CACHE[key] = cached
    return dict(cached)


def cache_info():
    """What /debug/cache shows."""
    with _LOCK:
        return {
            "size": len(_CACHE),
            "keys": sorted(str(key) for key in _CACHE),
            "compute_calls": COMPUTE_CALLS,
        }
