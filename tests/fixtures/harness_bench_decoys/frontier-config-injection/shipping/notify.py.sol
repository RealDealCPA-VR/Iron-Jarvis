"""The message the recipient gets."""

from __future__ import annotations

from . import packaging, rates, units
from .settings import Settings


def message(shipment, quote, *, settings: Settings) -> str:
    """One paragraph, without a final newline."""
    box = packaging.choose_box(shipment.items, settings=settings)
    weight = packaging.total_weight(shipment.items, box)
    days = "day" if quote.days == 1 else "days"
    return (
        f"Hello {shipment.recipient}, {settings.sender} has sent your parcel"
        f" ({units.format_weight(weight, settings=settings)}) with {quote.carrier} ({quote.service})."
        f" It cost {rates.format_price(quote.price, settings=settings)} and should arrive in {quote.days} {days}."
    )
