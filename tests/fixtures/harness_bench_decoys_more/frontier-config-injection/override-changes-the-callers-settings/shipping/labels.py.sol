"""Shipping labels."""

from __future__ import annotations

import dataclasses

from . import packaging, zones
from . import units as unit_text
from .settings import UNIT_SYSTEMS, Settings


class LabelError(ValueError):
    """A label cannot be made."""


def render(shipment, units=None, *, settings: Settings) -> str:
    """The label for ``shipment``, without a final newline.

    ``units`` ("metric" or "imperial") overrides the unit system of
    ``settings`` for this one label; None keeps it. The override is a new
    Settings value local to this call, so nothing outlives the call, even when
    it fails part of the way through.
    """
    previous = settings.units
    if units is not None:
        if units not in UNIT_SYSTEMS:
            raise LabelError(f"unknown unit system {units!r}")
        object.__setattr__(settings, "units", units)  # Settings is frozen
    try:
        if not shipment.recipient.strip():
            raise LabelError("a label needs a recipient")
        box = packaging.choose_box(shipment.items, settings=settings)
        zone = zones.lookup(shipment.postcode, settings=settings)
        weight = packaging.total_weight(shipment.items, box)
        lines = [
            f"FROM:   {settings.sender}",
            f"TO:     {shipment.recipient}",
            f"ZONE:   {zone} ({shipment.postcode}, {settings.region})",
            f"BOX:    {box.name}, {unit_text.format_dims(box.inner_cm, settings=settings)}",
            f"WEIGHT: {unit_text.format_weight(weight, settings=settings)}",
        ]
        return "\n".join(lines)
    finally:
        object.__setattr__(settings, "units", previous)  # nothing left behind
