"""Durations: reading and writing them (SPEC.md, section 1)."""

import re

UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """A duration such as "1h 30m" in whole seconds."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    text = text.strip()
    if text.isdigit():
        return int(text)
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("-")
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([a-zA-Z])", text)
    if not parts or re.sub(r"(\d+(?:\.\d+)?)\s*([a-zA-Z])|\s", "", text):
        raise ValueError(f"cannot read {text!r}")
    total = 0.0
    for number, unit in parts:
        unit = unit.lower()
        if unit not in UNITS:
            raise ValueError(f"unknown unit {unit!r}")
        total += float(number) * UNITS[unit]
    if total != int(total):
        raise ValueError("not a whole number of seconds")
    return sign * int(total)


def format_duration(seconds, largest=2):
    """Seconds written as "1h 30m"."""
    if not isinstance(seconds, int):
        raise TypeError("seconds must be an int")
    if not 1 <= largest <= 5:
        raise ValueError("largest must be from 1 to 5")
    if seconds == 0:
        return "0s"
    sign = "-" if seconds < 0 else ""
    rest = abs(seconds)
    out = []
    for unit, size in UNITS.items():
        count, rest = divmod(rest, size)
        if count:
            out.append(f"{count}{unit}")
    return sign + " ".join(out[:largest])
