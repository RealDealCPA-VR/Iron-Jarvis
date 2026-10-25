"""Ground: the standard service. One day more per zone; the EU takes a day longer."""

from __future__ import annotations

from .. import packaging, rates, zones
from ..settings import CONFIG
from .base import Quote


def quote(shipment) -> Quote:
    box = packaging.choose_box(shipment.items)
    weight = packaging.total_weight(shipment.items, box)
    zone = zones.lookup(shipment.postcode)
    days = zone + (1 if CONFIG["region"] == "US" else 2)
    return Quote("Ground", "standard", rates.price(zone, weight), CONFIG["currency"], days)
