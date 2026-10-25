"""The message the recipient gets."""

from __future__ import annotations

from . import packaging, rates, units
from .settings import CONFIG


def message(shipment, quote) -> str:
    """One paragraph, without a final newline."""
    box = packaging.choose_box(shipment.items)
    weight = packaging.total_weight(shipment.items, box)
    days = "day" if quote.days == 1 else "days"
    return (
        f"Hello {shipment.recipient}, {CONFIG['sender']} has sent your parcel"
        f" ({units.format_weight(weight)}) with {quote.carrier} ({quote.service})."
        f" It cost {rates.format_price(quote.price)} and should arrive in {quote.days} {days}."
    )
