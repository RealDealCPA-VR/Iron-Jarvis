"""Durations: reading and writing them (SPEC.md, section 1)."""

import re
from decimal import Decimal

UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
ORDER = "wdhms"
_PART = re.compile(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z])")


def parse_duration(text):
    """A duration such as "1h 30m" in whole seconds (SPEC.md 1.1)."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    body = text.strip(" ")
    if not body:
        raise ValueError("empty duration")
    negative = body.startswith("-")
    if negative:
        body = body[1:]
    if not body:
        raise ValueError("a sign with no duration")
    if re.fullmatch(r"[0-9]+", body):
        total = int(body)
        return -total if negative else total
    parts = []
    pos = 0
    last_rank = -1
    while pos < len(body):
        match = _PART.match(body, pos)
        if not match:
            raise ValueError(f"cannot read {text!r}")
        number, unit = match.group(1), match.group(2).lower()
        if unit not in UNITS:
            raise ValueError(f"unknown unit {match.group(2)!r}")
        rank = ORDER.index(unit)
        if rank <= last_rank:
            raise ValueError("units must go from the largest to the smallest, each once")
        last_rank = rank
        parts.append((number, unit))
        pos = match.end()
        while pos < len(body) and body[pos] == " ":
            pos += 1
    if any("." in number for number, _ in parts[:-1]):
        raise ValueError("only the last part may have a fraction")
    total = sum(Decimal(number) * UNITS[unit] for number, unit in parts)
    if total != total.to_integral_value():
        raise ValueError("not a whole number of seconds")
    total = int(total)
    return -total if negative else total


def _split(total):
    parts = []
    for unit in ORDER:
        count, total = divmod(total, UNITS[unit])
        parts.append(count)
    return parts


def format_duration(seconds, largest=2):
    """Seconds written as "1h 30m" (SPEC.md 1.2)."""
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise TypeError("seconds must be an int")
    if isinstance(largest, bool) or not isinstance(largest, int) or not 1 <= largest <= 5:
        raise ValueError("largest must be an int from 1 to 5")
    sign = "-" if seconds < 0 else ""
    total = abs(seconds)
    if total == 0:
        return "0s"
    parts = _split(total)
    nonzero = [i for i, count in enumerate(parts) if count]
    if len(nonzero) > largest:
        unit = UNITS[ORDER[nonzero[largest - 1]]]
        count, rest = divmod(total, unit)
        if 2 * rest >= unit:
            count += 1
        parts = _split(count * unit)
    return sign + " ".join(f"{count}{unit}" for count, unit in zip(parts, ORDER) if count)
