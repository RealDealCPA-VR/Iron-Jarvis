"""Ground: the standard service. One day more per zone; the EU takes a day longer."""

from __future__ import annotations

from .. import packaging, rates, zones
from ..settings import Settings
from .base import Quote


def quote(shipment, *, settings: Settings) -> Quote:
    box = packaging.choose_box(shipment.items, settings=settings)
    weight = packaging.total_weight(shipment.items, box)
    zone = zones.lookup(shipment.postcode, settings=settings)
    days = zone + (1 if settings.region == "US" else 2)
    return Quote("Ground", "standard", rates.price(zone, weight, settings=settings), settings.currency, days)
