"""Choosing a box for a shipment's items."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import units
from .decimals import to_decimal
from .settings import CONFIG


@dataclass(frozen=True)
class Box:
    name: str
    inner_cm: tuple[Decimal, Decimal, Decimal]
    tare_kg: Decimal


def _box(name: str, inner_cm: tuple[int, int, int], tare_kg: str) -> Box:
    return Box(name, tuple(Decimal(d) for d in inner_cm), Decimal(tare_kg))


# Smallest first.
BOXES = (
    _box("S", (20, 15, 10), "0.20"),
    _box("M", (35, 25, 15), "0.40"),
    _box("L", (50, 40, 30), "0.80"),
    _box("XL", (70, 50, 40), "1.20"),
)


class PackagingError(ValueError):
    """The items fit no box, or are too heavy for one."""


def needed_space(items) -> tuple[Decimal, Decimal, Decimal]:
    """The inner size a box needs for ``items``, largest side first.

    The items lie flat on top of each other; CONFIG["padding_cm"] of padding
    goes on every side.
    """
    pad = 2 * CONFIG["padding_cm"]
    sides = [sorted(item.dims_cm, reverse=True) for item in items]
    length = max(s[0] for s in sides) + pad
    width = max(s[1] for s in sides) + pad
    height = sum(s[2] for s in sides) + pad
    return tuple(sorted((length, width, height), reverse=True))


def fits(box: Box, space) -> bool:
    """Whether ``box`` holds a space of that size (largest side first)."""
    return all(b >= s for b, s in zip(sorted(box.inner_cm, reverse=True), space))


def total_weight(items, box: Box | None = None) -> Decimal:
    """The items' weight in kilograms, plus the box's own weight when given."""
    weight = sum((item.weight_kg for item in items), Decimal(0))
    return weight + (box.tare_kg if box is not None else 0)


def choose_box(items) -> Box:
    """The smallest box the items fit in, within CONFIG["max_box_kg"]."""
    if not items:
        raise PackagingError("nothing to pack")
    space = needed_space(items)
    for box in BOXES:
        if fits(box, space):
            weight = total_weight(items, box)
            limit = to_decimal(CONFIG["max_box_kg"])
            if weight > limit:
                raise PackagingError(
                    f"too heavy for one box: {units.format_weight(weight)}"
                    f" (the limit is {units.format_weight(limit)})"
                )
            return box
    raise PackagingError("no box is big enough for these items")
