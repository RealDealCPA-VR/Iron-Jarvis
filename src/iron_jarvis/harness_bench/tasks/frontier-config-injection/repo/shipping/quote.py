"""Every carrier's quote for a shipment."""

from __future__ import annotations

from . import carriers, rates, zones
from .carriers.base import Quote


def quote_all(shipment) -> list[Quote]:
    """One quote per carrier, cheapest first (ties by carrier name)."""
    quotes = [carriers.get(name).quote(shipment) for name in carriers.names()]
    return sorted(quotes, key=lambda q: (q.price, q.carrier))


def cheapest(shipment) -> Quote:
    return quote_all(shipment)[0]


def summary(shipment) -> str:
    """The quotes as text, one line each, without a final newline."""
    zone = zones.lookup(shipment.postcode)
    lines = [f"Quotes for {shipment.recipient} ({shipment.postcode}, zone {zone}):"]
    for q in quote_all(shipment):
        days = "day" if q.days == 1 else "days"
        lines.append(f"  {q.carrier:<8} {q.service:<9} {rates.format_price(q.price):>10}  {q.days} {days}")
    return "\n".join(lines)
