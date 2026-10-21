"""Seasonal promotions. A promotion registered under the same name as a
standing rule replaces it while this module is imported."""

from . import rule


@rule("member_discount", order=15)
def autumn_member_discount(total, cart, customer):
    """Autumn: members get 15% off, before any other discount."""
    if customer.member:
        return round(total * 0.85, 2)
    return total
