"""Coupons and discounts."""

COUPONS = {"WELCOME5": 5, "SAVE10": 10, "SAVE20": 20}

#: What a loyalty tier takes off, in percent.
TIER_PERCENT = {"bronze": 0, "silver": 5, "gold": 10}

#: The most a coupon and a tier together may take off, in percent.
MAX_DISCOUNT_PERCENT = 25


def coupon_percent(code):
    """The percent off a coupon gives (the code in any case, spaces around it
    dropped); 0 for no code; ValueError for a code we do not know."""
    if not code or not code.strip():
        return 0
    try:
        return COUPONS[code.strip().upper()]
    except KeyError:
        raise ValueError(f"unknown coupon {code!r}") from None


def tier_percent(tier):
    """The percent off a loyalty tier gives."""
    return TIER_PERCENT[tier]


def order_discount_percent(coupon_code, tier):
    """The percent off an order: the coupon's plus the tier's, never more than
    MAX_DISCOUNT_PERCENT in total."""
    total = coupon_percent(coupon_code) + tier_percent(tier)
    return MAX_DISCOUNT_PERCENT if total > MAX_DISCOUNT_PERCENT else total


def discount_cents(subtotal_cents, percent):
    """The discount on a subtotal, in cents, a half cent rounded up."""
    return (subtotal_cents * percent * 2 + 100) // 200
