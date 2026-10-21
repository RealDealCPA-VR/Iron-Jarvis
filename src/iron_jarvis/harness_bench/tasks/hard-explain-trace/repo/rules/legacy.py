"""The 2023 rules, kept for reference. Nothing imports this module any more."""

from . import rule


@rule("shipping", order=5)
def flat_shipping(total, cart, customer):
    """A flat 9.99 on every order."""
    return round(total + 9.99, 2)


@rule("tax", order=10)
def flat_tax(total, cart, customer):
    """A flat 10% tax."""
    return round(total * 1.10, 2)
