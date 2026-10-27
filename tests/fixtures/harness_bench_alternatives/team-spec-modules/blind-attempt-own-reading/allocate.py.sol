"""Allocation: splitting an amount of cents (SPEC.md, section 3)."""

from decimal import Decimal, InvalidOperation
from fractions import Fraction


class _BadCount(ValueError, TypeError):
    """n of the wrong type: both a ValueError and a TypeError."""


def _check_cents(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("cents must be an int")


def _fraction(w):
    if isinstance(w, Decimal) and not w.is_finite():
        raise ValueError("weight must be finite")
    return Fraction(w)


def allocate(total_cents, weights):
    """total_cents split in proportion to the weights (SPEC.md 3.1)."""
    _check_cents(total_cents)
    weights = list(weights)
    for w in weights:
        if isinstance(w, bool) or not isinstance(w, (int, Decimal)):
            raise TypeError("weights must be int or Decimal")
    if not weights:
        raise ValueError("at least one weight is needed")
    fr = [_fraction(w) for w in weights]
    if any(f < 0 for f in fr):
        raise ValueError("weights must not be negative")
    whole = sum(fr, Fraction(0))
    if whole == 0:
        raise ValueError("weights must not all be zero")
    absolute = abs(total_cents)
    exact = [absolute * f / whole for f in fr]
    parts = [e.numerator // e.denominator for e in exact]
    rems = [e - p for e, p in zip(exact, parts)]
    missing = absolute - sum(parts)
    order = sorted(range(len(parts)), key=lambda i: (-rems[i], i))
    for i in order[:missing]:
        parts[i] += 1
    if total_cents < 0:
        parts = [-p for p in parts]
    return parts


def split_evenly(total_cents, n):
    """total_cents split into n parts (SPEC.md 3.2)."""
    _check_cents(total_cents)
    if isinstance(n, bool) or not isinstance(n, int):
        raise _BadCount("n must be an int of at least 1")
    if n < 1:
        raise ValueError("n must be at least 1")
    return allocate(total_cents, [1] * n)


def allocate_percent(total_cents, percents):
    """total_cents split by percentages that add up to 100 (SPEC.md 3.3)."""
    _check_cents(total_cents)
    values = []
    for p in list(percents):
        if isinstance(p, bool):
            raise TypeError("percent must be int, Decimal or str")
        if isinstance(p, str):
            try:
                p = Decimal(p)
            except InvalidOperation:
                raise ValueError("not a number: %r" % (p,)) from None
        elif not isinstance(p, (int, Decimal)):
            raise TypeError("percent must be int, Decimal or str")
        values.append(p)
    if any(isinstance(v, Decimal) and not v.is_finite() for v in values):
        raise ValueError("percent must be finite")
    if sum((Fraction(v) for v in values), Fraction(0)) != 100:
        raise ValueError("percentages must add up to 100")
    return allocate(total_cents, values)


def format_cents(cents):
    """"$1,234.56" (SPEC.md 3.4)."""
    _check_cents(cents)
    dollars, rest = divmod(abs(cents), 100)
    text = "$%s.%02d" % (format(dollars, ","), rest)
    return "-" + text if cents < 0 else text
