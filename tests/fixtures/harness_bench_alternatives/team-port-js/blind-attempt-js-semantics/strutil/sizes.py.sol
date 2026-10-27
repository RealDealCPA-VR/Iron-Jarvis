"""format_bytes: the port of formatBytes in reference/strutil.js."""

import math

from ._js import is_integer, number_to_string, to_fixed, truthy

_DECIMAL = ["B", "kB", "MB", "GB", "TB", "PB"]
_BINARY = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]


def format_bytes(value, decimals=1, binary=False):
    """A byte count in the largest unit that keeps the number at 1 or more:
    format_bytes(1500) -> "1.5 kB", format_bytes(1536, binary=True) -> "1.5 KiB"."""
    binary = truthy(binary)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("bytes must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise TypeError("bytes must be a finite number")
    if not math.isfinite(number):
        raise TypeError("bytes must be a finite number")
    if not is_integer(decimals) or decimals < 0 or decimals > 6:
        raise ValueError("decimals must be an integer from 0 to 6")
    decimals = int(decimals)
    base = 1024 if binary else 1000
    units = _BINARY if binary else _DECIMAL
    sign = "-" if number < 0 else ""
    v = abs(number)
    if v < base:
        return sign + number_to_string(v) + " B"
    unit = 0
    while v >= base and unit < len(units) - 1:
        v /= base
        unit += 1
    text = to_fixed(v, decimals)
    if float(text) >= base and unit < len(units) - 1:
        v /= base
        unit += 1
        text = to_fixed(v, decimals)
    return sign + text + " " + units[unit]
