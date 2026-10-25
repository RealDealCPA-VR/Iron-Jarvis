"""Configuration for the shipping package: an immutable ``Settings`` value,
passed to every function that needs it; ``load(path)`` reads one from JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

DEFAULTS = {
    "region": "US",
    "units": "metric",
    "currency": "USD",
    "fuel_surcharge_pct": 0,
    "max_box_kg": 20,
    "padding_cm": 2,
    "sender": "Iron Shipping Co.",
}
REGIONS = ("US", "EU")
UNIT_SYSTEMS = ("metric", "imperial")
CURRENCIES = ("USD", "EUR", "GBP")


class SettingsError(ValueError):
    """A setting is unknown, or has a value it cannot have."""


def _whole(value) -> bool:
    return type(value) is int


def _number(value) -> bool:
    return type(value) in (int, float)


_RULES = (
    ("region", lambda v: v in REGIONS),
    ("units", lambda v: v in UNIT_SYSTEMS),
    ("currency", lambda v: v in CURRENCIES),
    ("fuel_surcharge_pct", lambda v: _whole(v) and 0 <= v <= 50),
    ("max_box_kg", lambda v: _number(v) and 1 <= v <= 70),
    ("padding_cm", lambda v: _whole(v) and 0 <= v <= 10),
    ("sender", lambda v: isinstance(v, str) and 1 <= len(v.strip()) <= 40),
)


def _check(values: dict) -> None:
    unknown = [k for k in values if k not in DEFAULTS]
    if unknown:
        raise SettingsError(f"unknown setting {unknown[0]!r}")
    for key, ok in _RULES:
        if not ok(values[key]):
            raise SettingsError(f"bad value for {key}: {values[key]!r}")


@dataclass(frozen=True)
class Settings:
    region: str = "US"
    units: str = "metric"
    currency: str = "USD"
    fuel_surcharge_pct: int = 0
    max_box_kg: int | float = 20
    padding_cm: int = 2
    sender: str = "Iron Shipping Co."

    def __post_init__(self) -> None:
        _check(asdict(self))

    def with_units(self, units: str) -> "Settings":
        return Settings(**{**asdict(self), "units": units})


def load(path) -> Settings:
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise SettingsError(f"{path} is not valid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object")
    merged = {**DEFAULTS, **data}
    _check(merged)
    return Settings(**merged)
