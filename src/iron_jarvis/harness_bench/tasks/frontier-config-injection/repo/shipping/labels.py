"""Shipping labels."""

from __future__ import annotations

from . import packaging, zones
from . import units as unit_text
from .settings import CONFIG, UNIT_SYSTEMS


class LabelError(ValueError):
    """A label cannot be made."""


def render(shipment, units=None) -> str:
    """The label for ``shipment``, without a final newline.

    ``units`` ("metric" or "imperial") overrides CONFIG["units"] for this one
    label; None keeps the configured unit system.
    """
    previous = CONFIG["units"]
    if units is not None:
        if units not in UNIT_SYSTEMS:
            raise LabelError(f"unknown unit system {units!r}")
        CONFIG["units"] = units
    if not shipment.recipient.strip():
        raise LabelError("a label needs a recipient")
    box = packaging.choose_box(shipment.items)
    zone = zones.lookup(shipment.postcode)
    weight = packaging.total_weight(shipment.items, box)
    lines = [
        f"FROM:   {CONFIG['sender']}",
        f"TO:     {shipment.recipient}",
        f"ZONE:   {zone} ({shipment.postcode}, {CONFIG['region']})",
        f"BOX:    {box.name}, {unit_text.format_dims(box.inner_cm)}",
        f"WEIGHT: {unit_text.format_weight(weight)}",
    ]
    CONFIG["units"] = previous
    return "\n".join(lines)
