"""Allocation (SPEC.md section 3), with integers scaled by the weights' common denominator."""

from decimal import Decimal, InvalidOperation
from math import lcm


def _int_cents(x):
    if type(x) is not int:
        raise TypeError("cents are an int")
    return x


def _ratio(w):
    """(numerator, denominator) of an int or Decimal weight."""
    if type(w) is int:
        return w, 1
    if isinstance(w, Decimal):
        if not w.is_finite():
            raise ValueError("weight is not finite")
        return w.as_integer_ratio()
    raise TypeError("weights are int or Decimal")


def allocate(total_cents, weights):
    total = _int_cents(total_cents)
    ratios = [_ratio(w) for w in weights]
    if not ratios:
        raise ValueError("no weights")
    if any(num < 0 for num, _ in ratios):
        raise ValueError("negative weight")
    common = lcm(*(den for _, den in ratios))
    scaled = [num * (common // den) for num, den in ratios]
    whole = sum(scaled)
    if whole == 0:
        raise ValueError("all weights are zero")
    amount = -total if total < 0 else total
    floors, remainders = [], []
    for s in scaled:
        q, r = divmod(amount * s, whole)
        floors.append(q)
        remainders.append(r)
    leftover = amount - sum(floors)
    for i in sorted(range(len(floors)), key=lambda k: (-remainders[k], k))[:leftover]:
        floors[i] += 1
    return [-f for f in floors] if total < 0 else floors


def split_evenly(total_cents, n):
    _int_cents(total_cents)
    if type(n) is not int or n < 1:
        raise ValueError("n must be at least 1")
    return allocate(total_cents, [1] * n)


def allocate_percent(total_cents, percents):
    _int_cents(total_cents)
    weights = []
    for p in percents:
        if isinstance(p, str):
            try:
                p = Decimal(p)
            except InvalidOperation:
                raise ValueError("not a number") from None
            if not p.is_finite():
                raise ValueError("not a number")
        elif type(p) is not int and not isinstance(p, Decimal):
            raise TypeError("percent must be int, Decimal or str")
        weights.append(p)
    if sum(Decimal(w) for w in weights) != Decimal(100):
        raise ValueError("percentages must add up to 100")
    return allocate(total_cents, weights)


def format_cents(cents):
    c = _int_cents(cents)
    sign, c = ("-", -c) if c < 0 else ("", c)
    dollars = str(c // 100)
    groups = []
    while len(dollars) > 3:
        groups.insert(0, dollars[-3:])
        dollars = dollars[:-3]
    groups.insert(0, dollars)
    return f"{sign}${','.join(groups)}.{c % 100:02d}"
