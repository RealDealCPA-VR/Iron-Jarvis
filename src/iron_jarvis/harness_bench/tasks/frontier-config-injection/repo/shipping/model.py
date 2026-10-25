"""Items and shipments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import units
from .decimals import to_decimal


class OrderError(ValueError):
    """An order could not be read."""


@dataclass(frozen=True)
class Item:
    name: str
    dims_cm: tuple[Decimal, Decimal, Decimal]
    weight_kg: Decimal


@dataclass(frozen=True)
class Shipment:
    recipient: str
    postcode: str
    items: tuple[Item, ...]


def make_item(name: str, dims_cm, weight_kg) -> Item:
    """An Item from three sizes in centimetres and a weight in kilograms."""
    dims = tuple(to_decimal(d) for d in dims_cm)
    if len(dims) != 3 or any(d <= 0 for d in dims):
        raise OrderError(f"{name}: dims_cm must be three sizes above zero")
    weight = to_decimal(weight_kg)
    if weight <= 0:
        raise OrderError(f"{name}: the weight must be above zero")
    return Item(name, dims, weight)


def shipment_from_dict(data: dict) -> Shipment:
    """An order (parsed JSON) as a Shipment.

    ``{"recipient": "...", "postcode": "...", "items": [{"name": "...",
    "dims_cm": [30, 20, 10], "weight": "2.5 kg"}]}``. An item's ``weight`` is
    read with ``units.parse_weight``: a bare number is in the configured unit
    system.
    """
    try:
        recipient = str(data["recipient"])
        postcode = str(data["postcode"])
        raw_items = list(data["items"])
    except (KeyError, TypeError) as exc:
        raise OrderError(f"an order needs recipient, postcode and items ({exc})") from None
    items = []
    for raw in raw_items:
        try:
            name, dims, weight = raw["name"], raw["dims_cm"], raw["weight"]
        except (KeyError, TypeError):
            raise OrderError("every item needs name, dims_cm and weight") from None
        items.append(make_item(name, dims, units.parse_weight(weight)))
    return Shipment(recipient, postcode, tuple(items))
