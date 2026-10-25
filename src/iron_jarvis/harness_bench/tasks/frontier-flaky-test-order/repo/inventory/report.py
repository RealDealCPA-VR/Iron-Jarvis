"""Plain-text reports: the price list and a stockroom's stock report."""

from __future__ import annotations

from fractions import Fraction

from . import catalog, pricing
from .stock import Stockroom


def money(cents: int) -> str:
    """``1234`` -> ``"12.34"``."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def _number(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):g}"


def _table(rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows)


def price_list() -> str:
    """One row per catalog product, sorted by SKU: SKU, name, unit, selling price."""
    rows = [("SKU", "Product", "Unit", "Price")]
    for sku in catalog.skus():
        product = catalog.get(sku)
        rows.append((sku, product.name, product.unit, money(pricing.unit_price(sku))))
    return _table(rows)


def stock_report(stockroom: Stockroom) -> str:
    """One row per SKU the stockroom holds (level, unit, value at selling
    prices), then a ``Total value`` line."""
    rows = [("SKU", "Level", "Unit", "Value")]
    for sku in stockroom.held():
        level = stockroom.level(sku)
        rows.append((sku, _number(level), catalog.get(sku).unit, money(pricing.line_total(sku, level))))
    return _table(rows) + f"\nTotal value: {money(stockroom.value_cents())}"
