"""Durations: reading and writing them (SPEC.md, section 1)."""

import re
from fractions import Fraction

_UNITS = (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
_SECONDS = {u: n for u, n in _UNITS}
_ORDER = {u: i for i, (u, _) in enumerate(_UNITS)}
_PART = re.compile(r"([0-9]+)(?:\.([0-9]+))?([wdhmsWDHMS])")


def parse_duration(text):
    """A duration such as "1h 30m" in whole seconds (SPEC.md 1.1)."""
    if not isinstance(text, str):
        raise TypeError("duration text must be a str")
    s = text.strip(" ")
    if not s:
        raise ValueError("empty duration")
    negative = False
    if s.startswith("-"):
        negative = True
        s = s[1:]
    if not s:
        raise ValueError("malformed duration")
    if re.fullmatch(r"[0-9]+", s):
        value = int(s)
        return -value if negative else value
    pos = 0
    n = len(s)
    total = Fraction(0)
    last_rank = -1
    first = True
    while pos < n:
        if not first:
            while pos < n and s[pos] == " ":
                pos += 1
            if pos >= n:
                break
        first = False
        m = _PART.match(s, pos)
        if not m:
            raise ValueError("malformed duration")
        pos = m.end()
        unit = m.group(3).lower()
        rank = _ORDER[unit]
        if rank <= last_rank:
            raise ValueError("units repeated or out of order")
        last_rank = rank
        value = Fraction(int(m.group(1)))
        if m.group(2) is not None:
            if s[pos:].strip(" "):
                raise ValueError("only the last part may have a fraction")
            value += Fraction(int(m.group(2)), 10 ** len(m.group(2)))
        total += value * _SECONDS[unit]
    if total.denominator != 1:
        raise ValueError("not a whole number of seconds")
    result = int(total)
    return -result if negative else result


def _split(value):
    parts = []
    for _, size in _UNITS:
        parts.append(value // size)
        value %= size
    return parts


def format_duration(seconds, largest=2):
    """Seconds written as "1h 30m" (SPEC.md 1.2)."""
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise TypeError("seconds must be an int")
    if isinstance(largest, bool) or not isinstance(largest, int):
        raise ValueError("largest must be an int from 1 to 5")
    if not 1 <= largest <= 5:
        raise ValueError("largest must be an int from 1 to 5")
    value = abs(seconds)
    parts = _split(value)
    nonzero = [i for i, p in enumerate(parts) if p]
    if len(nonzero) > largest:
        size = _UNITS[nonzero[largest - 1]][1]
        value = (value + size // 2) // size * size
        parts = _split(value)
    out = " ".join("%d%s" % (p, _UNITS[i][0]) for i, p in enumerate(parts) if p)
    if not out:
        return "0s"
    return "-" + out if seconds < 0 else out
