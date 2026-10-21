"""Fees and tax."""

from . import rule


@rule("small_order_fee", order=40)
def small_order_fee(total, cart, customer):
    """2.50 on orders under 20."""
    if total < 20:
        return round(total + 2.50, 2)
    return total


@rule("shipping", order=50)
def shipping(total, cart, customer):
    """Free shipping at 50 or more; otherwise 4.99."""
    if total >= 50:
        return total
    return round(total + 4.99, 2)


@rule("tax", order=60)
def tax(total, cart, customer):
    """The customer's sales tax, last of all."""
    return round(total * (1 + customer.tax_rate), 2)
