"""Weights and sizes in the unit system of the settings."""

from __future__ import annotations

import re
from decimal import Decimal

from .decimals import rounded, to_decimal

KG_PER_LB = Decimal("0.45359237")
CM_PER_IN = Decimal("2.54")

_WEIGHT = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(kg|lb)?\s*", re.IGNORECASE)


class UnitError(ValueError):
    """A weight could not be read."""


def _imperial(settings) -> bool:
    return settings.units == "imperial"


def weight_unit(*, settings) -> str:
    return "lb" if _imperial(settings) else "kg"


def weight_value(kg, *, settings) -> Decimal:
    kg = to_decimal(kg)
    return rounded(kg / KG_PER_LB if _imperial(settings) else kg, "0.01")


def format_weight(kg, *, settings) -> str:
    return f"{weight_value(kg, settings=settings)} {weight_unit(settings=settings)}"


def format_dims(dims_cm, *, settings) -> str:
    per = CM_PER_IN if _imperial(settings) else Decimal(1)
    unit = "in" if _imperial(settings) else "cm"
    return " x ".join(str(rounded(to_decimal(d) / per, "0.1")) for d in dims_cm) + f" {unit}"


def parse_weight(value, *, settings) -> Decimal:
    if isinstance(value, str):
        match = _WEIGHT.fullmatch(value)
        if not match:
            raise UnitError(f"not a weight: {value!r}")
        amount, unit = Decimal(match.group(1)), (match.group(2) or weight_unit(settings=settings)).lower()
    else:
        amount, unit = to_decimal(value), weight_unit(settings=settings)
    if amount <= 0:
        raise UnitError(f"a weight must be more than zero: {value!r}")
    return amount * KG_PER_LB if unit == "lb" else amount
