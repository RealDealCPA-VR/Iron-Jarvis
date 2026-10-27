"""Shipping."""

FLAT_FEE_CENTS = 599
FREE_FROM_CENTS = 10000
GOLD_FREE_FROM_CENTS = 5000


def shipping_cents(subtotal_cents, tier="bronze"):
    """Free on orders of $100.00 or more, or $50.00 or more for a gold
    customer (the subtotal before any discount); otherwise the flat fee."""
    free_from = GOLD_FREE_FROM_CENTS if tier == "gold" else FREE_FROM_CENTS
    return 0 if subtotal_cents >= free_from else FLAT_FEE_CENTS
