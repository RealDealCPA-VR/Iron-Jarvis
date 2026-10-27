"""Durations (SPEC.md section 1), written as a character scanner with Fraction."""

from fractions import Fraction

SECONDS = (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
RANK = {unit: i for i, (unit, _) in enumerate(SECONDS)}
SIZE = dict(SECONDS)
DIGITS = set("0123456789")


def parse_duration(text):
    if type(text) is not str:
        raise TypeError("parse_duration takes a str")
    i, n = 0, len(text)
    while i < n and text[i] == " ":
        i += 1
    while n > i and text[n - 1] == " ":
        n -= 1
    if i == n:
        raise ValueError("no duration given")
    sign = 1
    if text[i] == "-":
        sign, i = -1, i + 1
    if i == n:
        raise ValueError("a sign with no duration")
    if all(c in DIGITS for c in text[i:n]):
        return sign * int(text[i:n])
    total = Fraction(0)
    previous = -1
    fraction_seen = False
    while i < n:
        start = i
        while i < n and text[i] in DIGITS:
            i += 1
        if i == start:
            raise ValueError(f"expected a number at position {start}")
        has_fraction = False
        if i < n and text[i] == ".":
            i += 1
            frac_start = i
            while i < n and text[i] in DIGITS:
                i += 1
            if i == frac_start:
                raise ValueError("a fraction needs digits after the dot")
            has_fraction = True
        number = Fraction(text[start:i])
        if i >= n:
            raise ValueError("a number needs a unit")
        unit = text[i].lower()
        if unit not in RANK:
            raise ValueError(f"unknown unit {text[i]!r}")
        if RANK[unit] <= previous:
            raise ValueError("units out of order or repeated")
        if fraction_seen:
            raise ValueError("only the last part may have a fraction")
        fraction_seen = has_fraction
        previous = RANK[unit]
        total += number * SIZE[unit]
        i += 1
        while i < n and text[i] == " ":
            i += 1
    if total.denominator != 1:
        raise ValueError("the duration is not a whole number of seconds")
    return sign * int(total)


def format_duration(seconds, largest=2):
    if type(seconds) is not int:
        raise TypeError("seconds must be an int")
    if type(largest) is not int or largest < 1 or largest > 5:
        raise ValueError("largest must be 1 to 5")
    if seconds == 0:
        return "0s"
    value = -seconds if seconds < 0 else seconds

    def pieces(v):
        out = []
        for unit, size in SECONDS:
            if v >= size:
                out.append((v // size, unit, size))
                v %= size
        return out

    shown = pieces(value)
    if len(shown) > largest:
        size = shown[largest - 1][2]
        whole, rest = divmod(value, size)
        value = (whole + (1 if rest * 2 >= size else 0)) * size
        shown = pieces(value)
    text = " ".join(f"{count}{unit}" for count, unit, _ in shown)
    return ("-" if seconds < 0 else "") + text
