"""Shipping."""

FLAT_FEE_CENTS = 599
FREE_FROM_CENTS = 10000
EXPRESS_FEE_CENTS = 1299


def shipping_cents(subtotal_cents, *, express=False):
    """Free on orders of $100.00 or more (the subtotal before any discount);
    otherwise the flat fee. Express shipping always costs EXPRESS_FEE_CENTS,
    whatever the order: it is never free."""
    if express:
        return EXPRESS_FEE_CENTS
    return 0 if subtotal_cents >= FREE_FROM_CENTS else FLAT_FEE_CENTS
