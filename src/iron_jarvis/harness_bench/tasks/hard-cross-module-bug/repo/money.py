"""Money helpers. See NOTES.md for the rules."""


def to_cents(amount):
    """``amount`` (a float, int or a decimal string such as "4.35") in whole cents."""
    return int(float(amount) * 100)


def format_cents(cents):
    """12345 -> "123.45", -5 -> "-0.05"."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
