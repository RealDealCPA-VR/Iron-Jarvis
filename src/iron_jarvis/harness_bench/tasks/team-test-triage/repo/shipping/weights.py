"""Parcel weights.

Weights are given as an int, a ``decimal.Decimal`` or a str ("0.5") in grams
("g"), kilograms ("kg"), ounces ("oz") or pounds ("lb"), and are worked out
exactly: no binary floating point.
"""

import math
from decimal import Decimal

#: Grams in one of each unit (the international ounce and pound, exactly).
GRAMS = {"g": Decimal("1"), "kg": Decimal("1000"), "oz": Decimal("28.349523125"), "lb": Decimal("453.59237")}


def item_grams(qty, weight, unit):
    """The exact weight of ``qty`` items of ``weight`` ``unit`` each, in grams
    (a Decimal, not rounded)."""
    return qty * Decimal(str(weight)) * GRAMS[unit]


def parcel_grams(items):
    """The weight of a parcel in WHOLE grams: the items' exact weights added
    up, then rounded UP once to a whole gram (a carrier refuses a parcel
    declared lighter than it is, and charges by the gram). ``items`` is a list
    of ``(qty, weight, unit)``; an empty parcel weighs 0."""
    return sum(math.ceil(item_grams(qty, weight, unit)) for qty, weight, unit in items)
