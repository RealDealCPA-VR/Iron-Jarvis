"""Configuration for the shipping package.

Every module reads the module-level ``CONFIG`` dict. ``load_config(path)``
replaces it with the settings of a JSON file (keys the file does not have take
their defaults); ``reset()`` puts the defaults back.
"""

from __future__ import annotations

import json

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


CONFIG: dict = dict(DEFAULTS)


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


def load_config(path) -> None:
    """Replace CONFIG with the settings of the JSON file at ``path``."""
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise SettingsError(f"{path} is not valid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object")
    merged = {**DEFAULTS, **data}
    validate(merged)
    CONFIG.clear()
    CONFIG.update(merged)


def reset() -> None:
    """Put the default settings back."""
    CONFIG.clear()
    CONFIG.update(DEFAULTS)
