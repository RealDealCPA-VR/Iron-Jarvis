"""An order: product codes and quantities."""

from catalog import price_of
from money import to_cents


class Order:
    def __init__(self, lines=None):
        self.lines = list(lines or [])

    def add(self, sku, qty=1):
        if qty < 1:
            raise ValueError("quantity must be at least 1")
        self.lines.append((sku, qty))
        return self

    def line_cents(self):
        """[(sku, qty, unit cents, line cents), ...] in the order lines were added."""
        out = []
        for sku, qty in self.lines:
            unit = to_cents(price_of(sku))
            out.append((sku, qty, unit, unit * qty))
        return out

    def subtotal_cents(self):
        return sum(line for _sku, _qty, _unit, line in self.line_cents())
