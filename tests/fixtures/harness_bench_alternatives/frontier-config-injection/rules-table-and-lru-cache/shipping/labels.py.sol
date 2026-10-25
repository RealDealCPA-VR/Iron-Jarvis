"""Shipping labels."""

from __future__ import annotations

from . import packaging, zones
from . import units as unit_text
from .settings import UNIT_SYSTEMS


class LabelError(ValueError):
    """A label cannot be made."""


def render(shipment, units=None, *, settings) -> str:
    """The label for ``shipment``, without a final newline; ``units`` overrides
    the unit system for this one label."""
    if units is not None and units not in UNIT_SYSTEMS:
        raise LabelError(f"unknown unit system {units!r}")
    label_settings = settings if units is None else settings.with_units(units)
    return _render(shipment, label_settings)


def _render(shipment, s) -> str:
    if not shipment.recipient.strip():
        raise LabelError("a label needs a recipient")
    box = packaging.choose_box(shipment.items, settings=s)
    zone = zones.lookup(shipment.postcode, settings=s)
    weight = packaging.total_weight(shipment.items, box)
    return "\n".join((
        f"FROM:   {s.sender}",
        f"TO:     {shipment.recipient}",
        f"ZONE:   {zone} ({shipment.postcode}, {s.region})",
        f"BOX:    {box.name}, {unit_text.format_dims(box.inner_cm, settings=s)}",
        f"WEIGHT: {unit_text.format_weight(weight, settings=s)}",
    ))
