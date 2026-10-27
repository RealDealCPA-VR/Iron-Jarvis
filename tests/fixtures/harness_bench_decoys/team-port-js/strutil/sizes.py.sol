"""format_bytes: the port of formatBytes in reference/strutil.js."""

import math

_UNITS = {
    False: ["B", "kB", "MB", "GB", "TB", "PB"],
    True: ["B", "KiB", "MiB", "GiB", "TiB", "PiB"],
}


def format_bytes(value, decimals=1, binary=False):
    """A byte count in the largest unit that keeps the number at 1 or more."""
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError("bytes must be a finite number")
    if not isinstance(decimals, int) or not 0 <= decimals <= 6:
        raise ValueError("decimals must be an integer from 0 to 6")
    base = 1024 if binary else 1000
    units = _UNITS[bool(binary)]
    sign = "-" if value < 0 else ""
    number = abs(value)
    if number < base:
        return f"{sign}{number} B"
    unit = 0
    while number >= base and unit < len(units) - 1:
        number /= base
        unit += 1
    return f"{sign}{number:.{decimals}f} {units[unit]}"
