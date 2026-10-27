"""Allocation: splitting an amount of cents (SPEC.md, section 3)."""

from decimal import Decimal, InvalidOperation
from fractions import Fraction


def _cents(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int number of cents")


def allocate(total_cents, weights):
    """total_cents split in proportion to the weights (SPEC.md 3.1)."""
    _cents(total_cents, "total_cents")
    exact = []
    for weight in weights:
        if isinstance(weight, bool) or not isinstance(weight, (int, Decimal)):
            raise TypeError("a weight must be an int or a Decimal")
        if isinstance(weight, Decimal) and not weight.is_finite():
            raise ValueError("a weight must be a finite number")
        if weight < 0:
            raise ValueError("a weight may not be negative")
        exact.append(Fraction(weight))
    if not exact:
        raise ValueError("at least one weight is needed")
    whole = sum(exact)
    if whole == 0:
        raise ValueError("the weights may not all be zero")
    amount = abs(total_cents)
    shares = [amount * w / whole for w in exact]
    parts = [s.numerator // s.denominator for s in shares]
    missing = amount - sum(parts)
    by_remainder = sorted(range(len(parts)), key=lambda i: (-(shares[i] - parts[i]), i))
    for i in by_remainder[:missing]:
        parts[i] += 1
    if total_cents < 0:
        parts = [-p for p in parts]
    return parts


def split_evenly(total_cents, n):
    """total_cents split into n parts (SPEC.md 3.2)."""
    _cents(total_cents, "total_cents")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("n must be an int of at least 1")
    return allocate(total_cents, [1] * n)


def allocate_percent(total_cents, percents):
    """total_cents split by percentages that add up to 100 (SPEC.md 3.3)."""
    _cents(total_cents, "total_cents")
    weights = []
    for percent in percents:
        if isinstance(percent, str):
            try:
                percent = Decimal(percent)
            except InvalidOperation:
                raise ValueError(f"not a percentage: {percent!r}") from None
        elif isinstance(percent, bool) or not isinstance(percent, (int, Decimal)):
            raise TypeError("a percentage must be an int, a Decimal or a str")
        if isinstance(percent, Decimal) and not percent.is_finite():
            raise ValueError("a percentage must be a finite number")
        weights.append(percent)
    if sum(Fraction(p) for p in weights) != 100:
        raise ValueError("the percentages must add up to exactly 100")
    return allocate(total_cents, weights)


def format_cents(cents):
    """"$1,234.56" (SPEC.md 3.4)."""
    _cents(cents, "cents")
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rest:02d}"
