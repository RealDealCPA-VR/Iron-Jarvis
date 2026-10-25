"""Customer orders."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from . import catalog, pricing, units
from .stock import OutOfStock, Stockroom


@dataclass(frozen=True)
class Line:
    sku: str
    quantity: Fraction   # in the product's unit
    unit_cents: int
    total_cents: int


class Order:
    def __init__(self, customer: str) -> None:
        self.customer = customer
        self._items: list[tuple[str, Fraction]] = []

    def add(self, sku: str, text: str) -> None:
        """Add ``text`` (e.g. ``"3 each"``, ``"1.5 m"``) of ``sku`` to the order."""
        product = catalog.get(sku)
        quantity = units.parse_quantity(text)
        self._items.append((sku, units.convert(quantity.value, quantity.unit, product.unit)))

    def _merged(self) -> dict[str, Fraction]:
        merged: dict[str, Fraction] = {}
        for sku, amount in self._items:
            merged[sku] = merged.get(sku, Fraction(0)) + amount
        return merged

    def lines(self) -> list[Line]:
        """One line per SKU (items of the same SKU are added up), in the order
        each SKU was first added."""
        return [
            Line(sku, amount, pricing.unit_price(sku), pricing.line_total(sku, amount))
            for sku, amount in self._merged().items()
        ]

    def total_cents(self) -> int:
        return sum(line.total_cents for line in self.lines())

    def ship_from(self, stockroom: Stockroom) -> None:
        """Take every line out of ``stockroom``. When any line is short,
        ``OutOfStock`` is raised and nothing is taken."""
        merged = self._merged()
        short = sorted(sku for sku, amount in merged.items() if amount > stockroom.level(sku))
        if short:
            raise OutOfStock(f"not enough stock of {', '.join(short)}")
        for sku, amount in merged.items():
            stockroom.take_amount(sku, amount)
