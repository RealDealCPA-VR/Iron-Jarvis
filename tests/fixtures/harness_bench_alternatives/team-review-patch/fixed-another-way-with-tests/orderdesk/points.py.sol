"""Loyalty points."""

from fractions import Fraction

#: Points per whole dollar, as (numerator, denominator), by tier.
POINTS_PER_DOLLAR = {"bronze": (1, 1), "silver": (3, 2), "gold": (2, 1)}


def points_earned(total_cents, tier):
    """The points an order earns: for every WHOLE dollar of ``total_cents``
    (the total after the discount, without shipping), 1 point for bronze, 1.5
    for silver and 2 for gold, rounded down to a whole point."""
    if total_cents <= 0:
        return 0
    numerator, denominator = POINTS_PER_DOLLAR[tier]
    whole_dollars = total_cents // 100
    return int(Fraction(numerator, denominator) * whole_dollars)


def points_value_cents(points):
    """What points are worth when spent: one cent each."""
    return points
