"""Shelf prices."""

import config


def price_with_tax(net):
    """The shelf price of an item that costs ``net`` before tax."""
    return round(net * (1 + config.TAX_RATE), 2)
