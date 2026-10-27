"""format_bytes: the port of formatBytes in reference/strutil.js."""

import math
from decimal import ROUND_HALF_UP, Decimal

_UNITS = {
    False: ["B", "kB", "MB", "GB", "TB", "PB"],
    True: ["B", "KiB", "MiB", "GiB", "TiB", "PiB"],
}


def _is_js_integer(x):
    """Number.isInteger: a number (never a bool) with no fractional part."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and float(x).is_integer()


def _to_fixed(value, decimals):
    """Number.prototype.toFixed: rounds the EXACT value of the double to
    ``decimals`` places, a tie going to the larger number."""
    step = Decimal(1).scaleb(-decimals)
    rounded = Decimal(value).quantize(step, rounding=ROUND_HALF_UP)
    return f"{rounded:.{decimals}f}"


def _js_string(value):
    """String(number) for the small values shown in bytes."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def format_bytes(value, decimals=1, binary=False):
    """A byte count in the largest unit that keeps the number at 1 or more:
    format_bytes(1500) -> "1.5 kB", format_bytes(1536, binary=True) -> "1.5 KiB"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError("bytes must be a finite number")
    if not _is_js_integer(decimals) or decimals < 0 or decimals > 6:
        raise ValueError("decimals must be an integer from 0 to 6")
    decimals = int(decimals)
    binary = bool(binary)
    base = 1024 if binary else 1000
    units = _UNITS[binary]
    sign = "-" if value < 0 else ""
    number = abs(float(value))
    if number < base:
        return sign + _js_string(number) + " B"
    unit = 0
    while number >= base and unit < len(units) - 1:
        number /= base
        unit += 1
    text = _to_fixed(number, decimals)
    if float(text) >= base and unit < len(units) - 1:
        number /= base
        unit += 1
        text = _to_fixed(number, decimals)
    return sign + text + " " + units[unit]
