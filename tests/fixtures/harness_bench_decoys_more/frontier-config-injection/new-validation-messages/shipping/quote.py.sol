"""Every carrier's quote for a shipment."""

from __future__ import annotations

from . import carriers, rates, zones
from .carriers.base import Quote
from .settings import Settings


def quote_all(shipment, *, settings: Settings) -> list[Quote]:
    """One quote per carrier, cheapest first (ties by carrier name)."""
    quotes = [carriers.get(name).quote(shipment, settings=settings) for name in carriers.names()]
    return sorted(quotes, key=lambda q: (q.price, q.carrier))


def cheapest(shipment, *, settings: Settings) -> Quote:
    return quote_all(shipment, settings=settings)[0]


def summary(shipment, *, settings: Settings) -> str:
    """The quotes as text, one line each, without a final newline."""
    zone = zones.lookup(shipment.postcode, settings=settings)
    lines = [f"Quotes for {shipment.recipient} ({shipment.postcode}, zone {zone}):"]
    for q in quote_all(shipment, settings=settings):
        days = "day" if q.days == 1 else "days"
        price = rates.format_price(q.price, settings=settings)
        lines.append(f"  {q.carrier:<8} {q.service:<9} {price:>10}  {q.days} {days}")
    return "\n".join(lines)
