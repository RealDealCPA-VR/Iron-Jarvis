"""Allocation: splitting an amount of cents (SPEC.md, section 3)."""

from decimal import Decimal


def allocate(total_cents, weights):
    """total_cents split in proportion to the weights."""
    if not isinstance(total_cents, int):
        raise TypeError("total_cents must be an int")
    weights = [Decimal(w) for w in weights]
    if not weights or sum(weights) == 0 or any(w < 0 for w in weights):
        raise ValueError("bad weights")
    whole = sum(weights)
    parts = [int(total_cents * w // whole) for w in weights]
    leftover = total_cents - sum(parts)
    i = 0
    while leftover:
        if weights[i % len(parts)]:
            parts[i % len(parts)] += 1 if leftover > 0 else -1
            leftover += -1 if leftover > 0 else 1
        i += 1
    return parts


def split_evenly(total_cents, n):
    if n < 1:
        raise ValueError("n must be at least 1")
    return allocate(total_cents, [1] * n)


def allocate_percent(total_cents, percents):
    weights = [Decimal(p) for p in percents]
    if sum(weights) != 100:
        raise ValueError("the percentages must add up to 100")
    return allocate(total_cents, weights)


def format_cents(cents):
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"
