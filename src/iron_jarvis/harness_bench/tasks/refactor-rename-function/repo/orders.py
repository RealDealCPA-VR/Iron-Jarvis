"""Order arithmetic."""


def calc_ttl(items):
    """Total of an order: ``items`` is a list of (unit_price, quantity)."""
    return round(sum(price * qty for price, qty in items), 2)


def average_line(items):
    """The average line total of an order (0.0 for an empty order)."""
    if not items:
        return 0.0
    return round(calc_ttl(items) / len(items), 2)
