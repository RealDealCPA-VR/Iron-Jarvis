"""Sales tax."""

from money import to_cents

#: Percent, by region.
RATES = {"TX": 8.25, "OR": 0.0, "NY": 8.875}


def tax_cents(subtotal_cents, region):
    """The tax on a subtotal (in cents), in cents."""
    rate = RATES[region]
    return to_cents(subtotal_cents / 100 * rate / 100)
