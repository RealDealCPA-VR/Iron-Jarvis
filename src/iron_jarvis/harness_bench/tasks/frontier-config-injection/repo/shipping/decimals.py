"""Decimal helpers shared by the package. Nothing here reads a setting."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_decimal(value) -> Decimal:
    """An int, str, float or Decimal as a Decimal (a float through its repr)."""
    if isinstance(value, bool):
        raise TypeError("a number is needed, not a bool")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, (int, str)):
        try:
            return Decimal(value)
        except InvalidOperation:
            raise ValueError(f"not a number: {value!r}") from None
    raise TypeError(f"a number is needed, not {type(value).__name__}")


def rounded(value: Decimal, places: str) -> Decimal:
    """``value`` rounded half up to ``places`` ("0.01", "0.1" or "1")."""
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
