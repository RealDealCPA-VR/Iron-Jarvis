"""Money helpers. See NOTES.md for the rules."""

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount):
    """``amount`` (a float, int or a decimal string such as "4.35") in whole cents,
    rounded as written, half a cent away from zero."""
    exact = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(exact * 100)


def format_cents(cents):
    """12345 -> "123.45", -5 -> "-0.05"."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
