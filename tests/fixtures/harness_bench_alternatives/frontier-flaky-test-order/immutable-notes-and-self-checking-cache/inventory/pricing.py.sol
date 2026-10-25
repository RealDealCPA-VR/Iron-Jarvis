"""Selling prices.

``unit_price(sku)`` is the product's base price plus the shop-wide markup
(25% unless changed with ``set_markup``), rounded half up to whole cents. It
is called for every line of every order and report, so prices are kept in a
cache, one entry per SKU.
"""

from __future__ import annotations

from fractions import Fraction

from . import catalog

DEFAULT_MARKUP = Fraction(1, 4)

_markup = DEFAULT_MARKUP
#: sku -> (base_cents, markup, selling price) — the inputs are kept with the
#: price, so a price made from an old base price or markup is never served.
_price_cache: dict[str, tuple[int, Fraction, int]] = {}


def _round_half_up(value: Fraction) -> int:
    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))


def unit_price(sku: str) -> int:
    """The selling price of one unit of ``sku``, in cents."""
    product = catalog.get(sku)
    entry = _price_cache.get(sku)
    if entry is None or entry[0] != product.base_cents or entry[1] != _markup:
        entry = (product.base_cents, _markup, _round_half_up(product.base_cents * (1 + _markup)))
        _price_cache[sku] = entry
    return entry[2]


def set_price(sku: str, base_cents: int) -> None:
    """Change the base price of ``sku`` (in cents)."""
    if isinstance(base_cents, bool) or not isinstance(base_cents, int) or base_cents < 0:
        raise ValueError("base_cents must be a whole number of cents, 0 or more")
    catalog.get(sku).base_cents = base_cents


def get_markup() -> Fraction:
    return _markup


def set_markup(markup: Fraction | int | str) -> None:
    """Change the shop-wide markup (e.g. ``Fraction(1, 4)`` or ``"0.25"`` for 25%)."""
    global _markup
    value = Fraction(markup)
    if value < 0:
        raise ValueError("the markup must not be negative")
    _markup = value


def line_total(sku: str, quantity: Fraction | int) -> int:
    """The price of ``quantity`` units of ``sku``, rounded half up to whole cents."""
    return _round_half_up(Fraction(quantity) * unit_price(sku))
