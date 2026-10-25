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
        def need(ok: bool, what: str) -> None:
            if not ok:
                raise SettingsError(what)

        need(self.region in REGIONS, f"region must be one of {', '.join(REGIONS)}, not {self.region!r}")
        need(self.units in UNIT_SYSTEMS, f"units must be one of {', '.join(UNIT_SYSTEMS)}, not {self.units!r}")
        need(self.currency in CURRENCIES, f"currency must be one of {', '.join(CURRENCIES)}, not {self.currency!r}")
        need(_whole(self.fuel_surcharge_pct) and 0 <= self.fuel_surcharge_pct <= 50,
             f"fuel_surcharge_pct must be a whole number from 0 to 50, not {self.fuel_surcharge_pct!r}")
        need(_number(self.max_box_kg) and 1 <= self.max_box_kg <= 70,
             f"max_box_kg must be a number from 1 to 70, not {self.max_box_kg!r}")
        need(_whole(self.padding_cm) and 0 <= self.padding_cm <= 10,
             f"padding_cm must be a whole number from 0 to 10, not {self.padding_cm!r}")
        need(isinstance(self.sender, str) and 1 <= len(self.sender.strip()) <= 40,
             f"sender must be 1 to 40 characters, not {self.sender!r}")


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
