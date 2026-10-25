"""Stock levels, kept per SKU in the product's own unit."""

from __future__ import annotations

from fractions import Fraction

from . import catalog, pricing, units


class OutOfStock(Exception):
    pass


class Stockroom:
    """One stockroom. It starts empty; levels are in each product's unit."""

    def __init__(self) -> None:
        self._levels: dict[str, Fraction] = {}

    def _amount(self, sku: str, text: str) -> Fraction:
        product = catalog.get(sku)
        quantity = units.parse_quantity(text)
        return units.convert(quantity.value, quantity.unit, product.unit)

    def receive(self, sku: str, text: str) -> Fraction:
        """Add ``text`` (e.g. ``"2 dozen"``, ``"500 g"``) of ``sku``; returns
        the amount added, in the product's unit."""
        amount = self._amount(sku, text)
        self._levels[sku] = self.level(sku) + amount
        return amount

    def take(self, sku: str, text: str) -> Fraction:
        """Remove ``text`` of ``sku``; ``OutOfStock`` when there is not enough."""
        return self.take_amount(sku, self._amount(sku, text))

    def take_amount(self, sku: str, amount: Fraction) -> Fraction:
        """Remove ``amount`` (in the product's unit) of ``sku``."""
        if amount > self.level(sku):
            raise OutOfStock(f"only {self.level(sku)} {catalog.get(sku).unit} of {sku} in stock")
        self._levels[sku] = self.level(sku) - amount
        return amount

    def level(self, sku: str) -> Fraction:
        return self._levels.get(sku, Fraction(0))

    def held(self) -> list[str]:
        """The SKUs this stockroom holds any of, sorted."""
        return sorted(sku for sku, level in self._levels.items() if level > 0)

    def value_cents(self) -> int:
        """What the stock is worth at selling prices, in cents."""
        return sum(pricing.line_total(sku, self.level(sku)) for sku in self.held())

    def reorder_list(self) -> list[tuple[str, Fraction]]:
        """``(sku, amount to order)`` for every catalog product whose level is
        below its reorder level; the order brings it to twice that level."""
        out = []
        for sku in catalog.skus():
            product = catalog.get(sku)
            level = self.level(sku)
            if level < product.reorder_at:
                out.append((sku, 2 * product.reorder_at - level))
        return out
