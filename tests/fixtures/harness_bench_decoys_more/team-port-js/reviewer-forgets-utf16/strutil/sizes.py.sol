"""format_bytes: the port of formatBytes in reference/strutil.js.

Reviewer's alternative: JavaScript's ``toFixed`` done with exact integer
maths on the float's own ratio (``as_integer_ratio``), never ``round`` or
``format``: toFixed takes the exact value of the double and rounds a half
UP (away from zero for the positive values it is given here)."""

_DECIMAL = ("B", "kB", "MB", "GB", "TB", "PB")
_BINARY = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value):
    return value == value and value not in (float("inf"), float("-inf"))


def _to_fixed(value, digits):
    """Number.prototype.toFixed for a finite value of 0 or more (below 1e21)."""
    numerator, denominator = (value.as_integer_ratio() if isinstance(value, float) else (int(value), 1))
    scaled, rest = divmod(numerator * 10 ** digits, denominator)
    if 2 * rest >= denominator:
        scaled += 1
    text = str(scaled)
    if digits == 0:
        return text
    text = text.rjust(digits + 1, "0")
    return text[:-digits] + "." + text[-digits:]


def _js_string(value):
    """String(number) for the small values written in bytes."""
    if isinstance(value, int) or value == int(value):
        return str(int(value))
    return repr(value)


def format_bytes(value, decimals=1, binary=False):
    """A byte count in the largest unit that keeps the number at 1 or more:
    format_bytes(1500) -> "1.5 kB", format_bytes(1536, binary=True) -> "1.5 KiB"."""
    if not _is_number(value) or not _finite(value):
        raise TypeError("bytes must be a finite number")
    if not _is_number(decimals) or not _finite(decimals) or decimals != int(decimals) or not 0 <= decimals <= 6:
        raise ValueError("decimals must be an integer from 0 to 6")
    decimals = int(decimals)
    base = 1024 if binary else 1000
    units = _BINARY if binary else _DECIMAL
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount < base:
        return sign + _js_string(amount) + " B"
    unit = 0
    while amount >= base and unit < len(units) - 1:
        amount /= base
        unit += 1
    text = _to_fixed(amount, decimals)
    if float(text) >= base and unit < len(units) - 1:
        amount /= base
        unit += 1
        text = _to_fixed(amount, decimals)
    return sign + text + " " + units[unit]
