"""Discounts."""

from . import rule


@rule("bulk_discount", order=20)
def bulk_discount(total, cart, customer):
    """10% off when any single line has 10 or more units."""
    if any(line.qty >= 10 for line in cart.lines):
        return round(total * 0.90, 2)
    return total


@rule("coupon", order=25)
def coupon(total, cart, customer):
    """A flat 5 off when the cart carries a coupon code."""
    if cart.coupon:
        return round(total - 5, 2)
    return total


@rule("member_discount", order=30)
def member_discount(total, cart, customer):
    """5% off for members, after the other discounts."""
    if customer.member:
        return round(total * 0.95, 2)
    return total
