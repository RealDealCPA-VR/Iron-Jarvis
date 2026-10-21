"""Shelf prices."""


def price_with_tax(net):
    """The shelf price of an item that costs ``net`` before tax."""
    return round(net * (1 + 0.0825), 2)
