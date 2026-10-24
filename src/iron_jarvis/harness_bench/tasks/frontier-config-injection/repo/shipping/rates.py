"""Prices by zone and weight, in the configured currency (CONFIG["currency"])."""

from __future__ import annotations

from decimal import Decimal

from .decimals import rounded, to_decimal
from .settings import CONFIG

# The upper weight limit (kg) of each band.
BANDS_KG = tuple(Decimal(v) for v in ("1", "3", "5", "10", "20", "70"))
# The US dollar price of each band, per zone.
_BASE_USD = {
    1: ("5.20", "6.90", "8.40", "11.40", "16.90", "29.00"),
    2: ("5.60", "7.40", "9.10", "12.30", "18.20", "31.50"),
    3: ("6.10", "8.00", "9.90", "13.40", "19.80", "34.20"),
    4: ("6.70", "8.70", "10.80", "14.60", "21.60", "37.30"),
    5: ("7.40", "9.50", "11.80", "15.90", "23.60", "40.70"),
    6: ("8.20", "10.40", "12.90", "17.40", "25.80", "44.40"),
    7: ("9.10", "11.40", "14.10", "19.00", "28.20", "48.50"),
    8: ("10.10", "12.50", "15.40", "20.80", "30.80", "53.00"),
}
# What one US dollar is worth in each currency.
FX = {"USD": Decimal("1"), "EUR": Decimal("0.92"), "GBP": Decimal("0.79")}


class RateError(ValueError):
    """No price for that zone or weight."""


def band(weight_kg) -> int:
    """The index of the weight band ``weight_kg`` falls in."""
    weight = to_decimal(weight_kg)
    for index, limit in enumerate(BANDS_KG):
        if weight <= limit:
            return index
    raise RateError(f"too heavy to ship: {weight} kg")


def base_price(zone: int, weight_kg) -> Decimal:
    """The US dollar price for ``zone`` and ``weight_kg``, before surcharge."""
    if zone not in _BASE_USD:
        raise RateError(f"no such zone: {zone!r}")
    return Decimal(_BASE_USD[zone][band(weight_kg)])


def price(zone: int, weight_kg) -> Decimal:
    """The price in the configured currency, fuel surcharge included, to 0.01."""
    surcharged = base_price(zone, weight_kg) * (100 + CONFIG["fuel_surcharge_pct"]) / 100
    return rounded(surcharged * FX[CONFIG["currency"]], "0.01")


def format_price(amount) -> str:
    """``"EUR 12.34"``."""
    return f"{CONFIG['currency']} {rounded(to_decimal(amount), '0.01')}"
