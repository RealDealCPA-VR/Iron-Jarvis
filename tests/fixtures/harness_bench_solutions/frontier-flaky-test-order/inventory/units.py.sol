"""Units of measure: read quantities like "2 dz" and convert between units.

Every unit belongs to a dimension (count, mass or length) and is a whole
multiple of that dimension's base unit (each, g, mm). Unit names are read
case-insensitively, and a few common aliases are accepted ("pcs" for "each",
"dz" for "dozen", ...). When an alias had to be resolved, the quantity says so
in its ``notes`` — the notes of THAT reading only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction


class UnitError(ValueError):
    """An unknown unit, a malformed quantity, or units that cannot be converted."""


#: name -> (dimension, size in the dimension's base unit)
_BUILTIN_UNITS = {
    "each": ("count", 1),
    "dozen": ("count", 12),
    "box": ("count", 100),
    "g": ("mass", 1),
    "kg": ("mass", 1000),
    "mm": ("length", 1),
    "cm": ("length", 10),
    "m": ("length", 1000),
}

#: alias -> unit name
_ALIASES = {
    "pc": "each",
    "pcs": "each",
    "ea": "each",
    "dz": "dozen",
    "doz": "dozen",
    "gram": "g",
    "grams": "g",
    "kilo": "kg",
    "meter": "m",
    "metre": "m",
}

#: The unit table, filled on first use (see ``_units``).
_table: dict[str, tuple[str, int]] | None = None

_QUANTITY = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$")


def _units() -> dict[str, tuple[str, int]]:
    global _table
    if _table is None:
        _table = dict(_BUILTIN_UNITS)
    return _table


@dataclass(frozen=True)
class Quantity:
    value: Fraction
    unit: str
    notes: tuple[str, ...] = ()


def resolve(name: str, notes: list[str] | None = None) -> tuple[str, list[str]]:
    """The unit called ``name`` (case-insensitive; aliases resolved), and the
    notes of this reading: a note is added when an alias was resolved.

    Raises ``UnitError`` for an unknown unit.
    """
    # A fresh list for every call: a ``[]`` default is ONE list shared by all
    # calls, so every alias ever read piled up in it.
    if notes is None:
        notes = []
    key = name.strip().lower()
    if key in _ALIASES:
        target = _ALIASES[key]
        notes.append(f"read '{name}' as '{target}'")
        key = target
    if key not in _units():
        raise UnitError(f"unknown unit {name!r}")
    return key, notes


def parse_quantity(text: str) -> Quantity:
    """Read ``"<number> <unit>"`` (e.g. ``"2 dz"``, ``"1.5 kg"``) into a Quantity."""
    match = _QUANTITY.match(text)
    if not match:
        raise UnitError(f"not a quantity: {text!r}")
    unit, notes = resolve(match.group(2))
    return Quantity(Fraction(match.group(1)), unit, tuple(notes))


def dimension(unit: str) -> str:
    return _units()[resolve(unit)[0]][0]


def convert(value: Fraction | int, from_unit: str, to_unit: str) -> Fraction:
    """``value`` ``from_unit`` expressed in ``to_unit`` (same dimension only)."""
    src, _ = resolve(from_unit)
    dst, _ = resolve(to_unit)
    src_dim, src_size = _units()[src]
    dst_dim, dst_size = _units()[dst]
    if src_dim != dst_dim:
        raise UnitError(f"cannot convert {src} ({src_dim}) to {dst} ({dst_dim})")
    return Fraction(value) * src_size / dst_size


def unit_names() -> list[str]:
    return sorted(_units())
