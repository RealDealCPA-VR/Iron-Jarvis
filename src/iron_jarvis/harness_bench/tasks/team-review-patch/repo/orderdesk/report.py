"""Sales reports."""

from .pricing import subtotal


def sales_total(orders):
    """The sum of the orders' subtotals, in cents."""
    return sum(subtotal(order["lines"]) for order in orders)


def best_sellers(orders, top=3):
    """The ``top`` product names by units sold (most first; ties by name)."""
    units = {}
    for order in orders:
        for line in order["lines"]:
            units[line["name"]] = units.get(line["name"], 0) + line["qty"]
    return [name for name, _ in sorted(units.items(), key=lambda item: (-item[1], item[0]))[:top]]
