"""Coupons and discounts."""

COUPONS = {"WELCOME5": 5, "SAVE10": 10, "SAVE20": 20}


def coupon_percent(code):
    """The percent off a coupon gives (the code in any case, spaces around it
    dropped); 0 for no code; ValueError for a code we do not know."""
    if not code or not code.strip():
        return 0
    try:
        return COUPONS[code.strip().upper()]
    except KeyError:
        raise ValueError(f"unknown coupon {code!r}") from None


def discount_cents(subtotal_cents, percent):
    """The discount on a subtotal, in cents, a half cent rounded up."""
    return (subtotal_cents * percent * 2 + 100) // 200
