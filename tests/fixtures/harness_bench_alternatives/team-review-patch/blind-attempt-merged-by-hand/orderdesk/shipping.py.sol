"""Shipping."""

FLAT_FEE_CENTS = 599
FREE_FROM_CENTS = 10000
EXPRESS_FEE_CENTS = 1299
GOLD_FREE_FROM_CENTS = 5000


def shipping_cents(subtotal_cents, tier="bronze", *, express=False):
    """Free on orders of $100.00 or more, or $50.00 or more for a gold
    customer (the subtotal before any discount); otherwise the flat fee.
    Express shipping always costs EXPRESS_FEE_CENTS, whatever the order or the
    tier: it is never free."""
    if express:
        return EXPRESS_FEE_CENTS
    if tier == "gold" and subtotal_cents >= GOLD_FREE_FROM_CENTS:
        return 0
    return 0 if subtotal_cents >= FREE_FROM_CENTS else FLAT_FEE_CENTS
