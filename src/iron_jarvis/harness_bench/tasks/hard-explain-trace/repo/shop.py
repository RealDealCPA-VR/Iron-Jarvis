"""Checkout."""

import settings
from profiles import build_pipeline


def subtotal(cart):
    return round(sum(line.qty * line.price for line in cart.lines), 2)


def checkout(cart, customer):
    """The total the customer pays."""
    total = subtotal(cart)
    for step in build_pipeline(settings.ACTIVE_PROFILE, settings.DISABLED):
        total = step(total, cart, customer)
    return total
