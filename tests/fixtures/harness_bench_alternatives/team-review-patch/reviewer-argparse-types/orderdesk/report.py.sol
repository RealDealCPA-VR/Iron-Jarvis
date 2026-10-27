"""Sales reports."""

from .customers import TIERS, normalize_tier
from .pricing import subtotal


def sales_total(orders):
    """The sum of the orders' subtotals, in cents."""
    return sum(subtotal(order["lines"]) for order in orders)


def sales_by_tier(orders):
    """The sum of the orders' subtotals per loyalty tier, for every tier (0
    for a tier with no orders); an order whose customer has no tier is bronze."""
    totals = dict.fromkeys(TIERS, 0)
    for order in orders:
        tier = normalize_tier(order.get("customer", {}).get("tier"))
        totals[tier] += subtotal(order["lines"])
    return totals


def best_sellers(orders, top=3):
    """The ``top`` product names by units sold (most first; ties by name)."""
    units = {}
    for order in orders:
        for line in order["lines"]:
            units[line["name"]] = units.get(line["name"], 0) + line["qty"]
    return [name for name, _ in sorted(units.items(), key=lambda item: (-item[1], item[0]))[:top]]
