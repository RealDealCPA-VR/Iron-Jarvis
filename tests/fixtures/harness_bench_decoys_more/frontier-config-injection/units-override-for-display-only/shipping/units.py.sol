"""Weights and sizes in the unit system of the settings (``settings.units``).

Inside the package every weight is in kilograms and every size in
centimetres; this module turns them into what the user reads, and reads
weights the user writes.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .decimals import rounded, to_decimal
from .settings import Settings

KG_PER_LB = Decimal("0.45359237")
CM_PER_IN = Decimal("2.54")

_WEIGHT = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(kg|lb)?\s*", re.IGNORECASE)


class UnitError(ValueError):
    """A weight could not be read."""


def weight_unit(*, settings: Settings) -> str:
    """``"kg"`` (metric) or ``"lb"`` (imperial)."""
    return "lb" if settings.units == "imperial" else "kg"


def weight_value(kg, *, settings: Settings) -> Decimal:
    """``kg`` in the configured unit, rounded half up to 0.01."""
    kg = to_decimal(kg)
    if settings.units == "imperial":
        return rounded(kg / KG_PER_LB, "0.01")
    return rounded(kg, "0.01")


def format_weight(kg, *, settings: Settings) -> str:
    """``"2.50 kg"`` or ``"5.51 lb"``."""
    return f"{weight_value(kg, settings=settings)} {weight_unit(settings=settings)}"


def format_dims(dims_cm, *, settings: Settings) -> str:
    """``"30.0 x 20.0 x 10.0 cm"`` or ``"11.8 x 7.9 x 3.9 in"``."""
    if settings.units == "imperial":
        values = [rounded(to_decimal(d) / CM_PER_IN, "0.1") for d in dims_cm]
        unit = "in"
    else:
        values = [rounded(to_decimal(d), "0.1") for d in dims_cm]
        unit = "cm"
    return " x ".join(str(v) for v in values) + f" {unit}"


def parse_weight(value, *, settings: Settings) -> Decimal:
    """A weight the user wrote, in kilograms (exact, not rounded).

    Text may carry its unit (``"2.5 kg"``, ``"5 LB"``); a bare number (text or
    a number) is in the configured unit: kilograms for metric, pounds for
    imperial.
    """
    if isinstance(value, str):
        match = _WEIGHT.fullmatch(value)
        if not match:
            raise UnitError(f"not a weight: {value!r}")
        amount = Decimal(match.group(1))
        unit = (match.group(2) or weight_unit(settings=settings)).lower()
    else:
        amount = to_decimal(value)
        unit = weight_unit(settings=settings)
    if amount <= 0:
        raise UnitError(f"a weight must be more than zero: {value!r}")
    return amount * KG_PER_LB if unit == "lb" else amount
