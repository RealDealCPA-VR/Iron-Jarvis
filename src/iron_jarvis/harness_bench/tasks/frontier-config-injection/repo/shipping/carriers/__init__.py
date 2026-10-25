"""The carriers: each module has a ``quote(shipment)`` function."""

from __future__ import annotations

from types import ModuleType

from . import express, ground

_CARRIERS = {"express": express, "ground": ground}


def names() -> list[str]:
    """Every carrier's name, sorted."""
    return sorted(_CARRIERS)


def get(name: str) -> ModuleType:
    """The carrier module called ``name``."""
    try:
        return _CARRIERS[name]
    except KeyError:
        raise ValueError(f"unknown carrier {name!r}") from None
