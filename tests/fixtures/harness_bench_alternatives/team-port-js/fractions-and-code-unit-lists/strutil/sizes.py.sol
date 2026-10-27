"""format_bytes, ported from formatBytes."""

from fractions import Fraction
from math import floor, isfinite

from ._js import js_integer

DECIMAL = ("B", "kB", "MB", "GB", "TB", "PB")
BINARY = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def to_fixed(x, digits):
    scaled = floor(Fraction(x) * 10 ** digits + Fraction(1, 2))
    if digits == 0:
        return str(scaled)
    whole, frac = divmod(scaled, 10 ** digits)
    return f"{whole}.{frac:0{digits}d}"


def format_bytes(value, decimals=1, binary=False):
    if type(value) not in (int, float) or not isfinite(value):
        raise TypeError("bytes must be a finite number")
    if not js_integer(decimals) or not (0 <= decimals <= 6):
        raise ValueError("decimals must be an integer from 0 to 6")
    digits = int(decimals)
    base, names = (1024, BINARY) if binary else (1000, DECIMAL)
    sign = "-" if value < 0 else ""
    amount = float(value)
    amount = -amount if amount < 0 else amount
    if amount < base:
        shown = int(amount) if amount.is_integer() else amount
        return f"{sign}{shown} B"
    index = 0
    while amount >= base and index < len(names) - 1:
        amount = amount / base
        index += 1
    shown = to_fixed(amount, digits)
    if float(shown) >= base and index < len(names) - 1:
        amount = amount / base
        index += 1
        shown = to_fixed(amount, digits)
    return f"{sign}{shown} {names[index]}"
