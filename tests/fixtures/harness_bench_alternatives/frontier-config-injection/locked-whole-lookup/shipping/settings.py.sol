"""Configuration for the shipping package.

A ``Settings`` value is passed explicitly, as ``settings=``, to every function
that needs configuration. ``load(path)`` reads one from a JSON file (keys the
file does not have take their defaults).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

DEFAULTS = {
    "region": "US",                # "US" or "EU": which postcode table zones.py uses
    "units": "metric",             # "metric" or "imperial": how weights and sizes are shown
    "currency": "USD",             # "USD", "EUR" or "GBP": the currency of every price
    "fuel_surcharge_pct": 0,       # whole number 0..50, added to every price
    "max_box_kg": 20,              # number 1..70: the heaviest box a carrier takes
    "padding_cm": 2,               # whole number 0..10: padding on each side of a box
    "sender": "Iron Shipping Co.", # 1..40 characters: who ships
}
REGIONS = ("US", "EU")
UNIT_SYSTEMS = ("metric", "imperial")
CURRENCIES = ("USD", "EUR", "GBP")


class SettingsError(ValueError):
    """A setting is unknown, or has a value it cannot have."""


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value) -> bool:
    return _whole(value) or isinstance(value, float)


def validate(values: dict) -> None:
    """Raise SettingsError unless ``values`` is a complete, valid set of settings."""
    for key in values:
        if key not in DEFAULTS:
            raise SettingsError(f"unknown setting {key!r}")
    for key in DEFAULTS:
        if key not in values:
            raise SettingsError(f"missing setting {key!r}")
    checks = {
        "region": lambda v: v in REGIONS,
        "units": lambda v: v in UNIT_SYSTEMS,
        "currency": lambda v: v in CURRENCIES,
        "fuel_surcharge_pct": lambda v: _whole(v) and 0 <= v <= 50,
        "max_box_kg": lambda v: _number(v) and 1 <= v <= 70,
        "padding_cm": lambda v: _whole(v) and 0 <= v <= 10,
        "sender": lambda v: isinstance(v, str) and 1 <= len(v.strip()) <= 40,
    }
    for key, ok in checks.items():
        if not ok(values[key]):
            raise SettingsError(f"bad value for {key}: {values[key]!r}")


@dataclass(frozen=True)
class Settings:
    """One complete, valid set of settings. Immutable."""

    region: str = DEFAULTS["region"]
    units: str = DEFAULTS["units"]
    currency: str = DEFAULTS["currency"]
    fuel_surcharge_pct: int = DEFAULTS["fuel_surcharge_pct"]
    max_box_kg: int | float = DEFAULTS["max_box_kg"]
    padding_cm: int = DEFAULTS["padding_cm"]
    sender: str = DEFAULTS["sender"]

    def __post_init__(self) -> None:
        validate({field.name: getattr(self, field.name) for field in dataclasses.fields(self)})


def load(path) -> Settings:
    """The Settings of the JSON file at ``path``."""
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise SettingsError(f"{path} is not valid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object")
    for key in data:
        if key not in DEFAULTS:
            raise SettingsError(f"unknown setting {key!r}")
    return Settings(**data)
