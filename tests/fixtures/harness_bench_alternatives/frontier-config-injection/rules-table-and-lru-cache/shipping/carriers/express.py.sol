"""Express: 1.8 times the ground price; next day up to zone 4, else two days."""

from __future__ import annotations

from decimal import Decimal

from .. import packaging, rates, zones
from ..decimals import rounded
from ..settings import Settings
from .base import Quote

MULTIPLIER = Decimal("1.8")


def quote(shipment, *, settings: Settings) -> Quote:
    box = packaging.choose_box(shipment.items, settings=settings)
    weight = packaging.total_weight(shipment.items, box)
    zone = zones.lookup(shipment.postcode, settings=settings)
    price = rounded(rates.price(zone, weight, settings=settings) * MULTIPLIER, "0.01")
    days = 1 if zone <= 4 else 2
    service = "next-day" if days == 1 else "two-day"
    return Quote("Express", service, price, settings.currency, days)
