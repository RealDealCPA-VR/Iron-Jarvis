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
_price_cache: dict[str, int] = {}


def _round_half_up(value: Fraction) -> int:
    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))


def unit_price(sku: str) -> int:
    """The selling price of one unit of ``sku``, in cents."""
    if sku not in _price_cache:
        product = catalog.get(sku)
        _price_cache[sku] = _round_half_up(product.base_cents * (1 + _markup))
    return _price_cache[sku]


def set_price(sku: str, base_cents: int) -> None:
    """Change the base price of ``sku`` (in cents)."""
    if isinstance(base_cents, bool) or not isinstance(base_cents, int) or base_cents < 0:
        raise ValueError("base_cents must be a whole number of cents, 0 or more")
    catalog.get(sku).base_cents = base_cents
    # The cached selling price was made from the old base price.
    _price_cache.pop(sku, None)


def get_markup() -> Fraction:
    return _markup


def set_markup(markup: Fraction | int | str) -> None:
    """Change the shop-wide markup (e.g. ``Fraction(1, 4)`` or ``"0.25"`` for 25%)."""
    global _markup
    value = Fraction(markup)
    if value < 0:
        raise ValueError("the markup must not be negative")
    _markup = value
    # Every cached selling price was made with the old markup.
    _price_cache.clear()


def line_total(sku: str, quantity: Fraction | int) -> int:
    """The price of ``quantity`` units of ``sku``, rounded half up to whole cents."""
    return _round_half_up(Fraction(quantity) * unit_price(sku))
