"""Helpers that reproduce JavaScript behaviour (UTF-16 strings, number text)."""

import decimal
import math


def utf16_key(text):
    """A key ordering strings the way JavaScript's `<` does (UTF-16 code units)."""
    return text.encode("utf-16-be", "surrogatepass")


def to_units(text):
    """The string with every astral character split into its two surrogates,
    so that len() and slicing count UTF-16 code units."""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            out.append(chr(0xD800 + (cp >> 10)))
            out.append(chr(0xDC00 + (cp & 0x3FF)))
        else:
            out.append(ch)
    return "".join(out)


def from_units(text):
    """Joins surrogate pairs back into single characters."""
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def is_integer(x):
    """Number.isInteger for a Python value."""
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True
    if isinstance(x, float):
        return math.isfinite(x) and x == math.floor(x)
    return False


def truthy(x):
    """JavaScript Boolean(x) for the values a caller is likely to pass."""
    if x is None or x is False:
        return False
    if isinstance(x, str):
        return x != ""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return not (x == 0 or x != x)
    return True


def number_to_string(v):
    """String(v) for a finite float/int."""
    v = float(v)
    if v == 0:
        return "0"
    sign = "-" if v < 0 else ""
    v = abs(v)
    d = decimal.Decimal(repr(v))
    _, digits, exp = d.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    k = len(digits)
    n = k + exp  # value = 0.digits * 10**n
    s = "".join(map(str, digits))
    if k <= n <= 21:
        r = s + "0" * (n - k)
    elif 0 < n <= 21:
        r = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        r = "0." + "0" * (-n) + s
    else:
        e = n - 1
        es = ("+" if e >= 0 else "-") + str(abs(e))
        r = s[0] + ("." + s[1:] if k > 1 else "") + "e" + es
    return sign + r


_CTX = decimal.Context(prec=1200)


def to_fixed(v, digits):
    """Number.prototype.toFixed for a non-negative finite float."""
    if v >= 1e21:
        return number_to_string(v)
    d = decimal.Decimal(v)
    q = decimal.Decimal(1).scaleb(-digits)
    r = d.quantize(q, rounding=decimal.ROUND_HALF_UP, context=_CTX)
    return format(r, "f")
