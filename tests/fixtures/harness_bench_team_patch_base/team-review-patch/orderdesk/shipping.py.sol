"""Shipping."""

FLAT_FEE_CENTS = 599
FREE_FROM_CENTS = 10000


def shipping_cents(subtotal_cents):
    """Free on orders of $100.00 or more (the subtotal before any discount);
    otherwise the flat fee."""
    return 0 if subtotal_cents >= FREE_FROM_CENTS else FLAT_FEE_CENTS
