"""Numbers as Iron Jarvis compares them (v1.547.0, idea 30-a).

Lifted out of ``workmap/recap.py`` (the recap check, v1.492.0) so the recap
check and the figure links in a reply (``documents/sources``) read a number
the same way:

* ``"1,234.50"`` and ``"1234.5"`` are the same number; anything written
  another way (``"12,50"``, ``"9,99,999"``, ``"1.491.0"``) is only itself.
* A scale is part of the number: ``"25k"`` is not ``"25"``, ``"2 million"``
  is not ``"2"``.

:func:`figures` adds what the figure links need on top: WHERE each number
sits in the text, and whether it is worth a record at all. A figure under
three digits and a bare year are left alone unless they carry a currency
sign, a percent sign or decimals, and a number glued to letters (``K1``,
``2nd``) or to other digits by a dash, a slash or a colon (a date, a phone
number, an id) is not a figure. The same rule decides what is recorded from
a file and what is linked in a reply, so the two always agree.

Pure: no I/O. Every pattern here is linear (one pass, no overlapping starts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A number, with the scale written after it: a short one glued on ("25k",
#: "15B"; "2 K-1 forms" is two K-1s, not two thousand), a word after a
#: space or hyphen ("2 million", "15-thousand", "15 grand"). A dot that starts a number after a space or a
#: sign (".15", "$.15") makes it a decimal; after a letter ("no.4471") it is
#: only punctuation. Linear: one pass, no overlapping starts.
NUMBER_RX = re.compile(
    r"(?:(?<![\w.,])\.(\d+)|(\d+(?:[.,]\d+)*))"
    r"(?:(k|mm|m|bn|b)\b|[\s\-]?(mil|mill|grand|thousands?|millions?|billions?|trillions?"
    r"|hundreds?|lakhs?|crores?)\b)?",
    re.IGNORECASE,
)
THOUSANDS_RX = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
PLAIN_RX = re.compile(r"\d+(?:\.\d+)?")
SCALES = {
    "k": "k", "thousand": "k", "grand": "k", "m": "m", "mm": "m", "mil": "m", "mill": "m",
    "million": "m", "b": "b", "bn": "b", "billion": "b", "trillion": "t", "hundred": "h",
    "lakh": "lakh", "crore": "crore",
}

#: Signs that make any number a money figure.
CURRENCY = "$€£¥₹"
_CURRENCY_WORD_RX = re.compile(r"(?:USD|EUR|GBP|CAD|AUD|CHF|JPY|INR)\s?$")


def number_key(digits: str, scale: str = "") -> str:
    """One number as it is compared: "1,234.50" and "1234.5" are the same;
    anything written another way ("12,50", "9,99,999", "1.491.0") is only
    itself. A scale ("k", "million") is part of the number."""
    if THOUSANDS_RX.fullmatch(digits):
        digits = digits.replace(",", "")
    if PLAIN_RX.fullmatch(digits) and "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    word = scale.lower() if scale else ""
    tail = SCALES.get(word) or SCALES.get(word.rstrip("s"), "")
    return f"{digits}~{tail}" if tail else digits


def numbers(text: str):
    """``(key, as written)`` for every number of ``text``."""
    for m in NUMBER_RX.finditer(text):
        digits = "0." + m.group(1) if m.group(1) else m.group(2)
        yield number_key(digits, m.group(3) or m.group(4) or ""), m.group(0)


def figures_in(text: str) -> set[str]:
    """Every number of ``text``, as it is compared (:func:`number_key`)."""
    return {key for key, _raw in numbers(text)}


@dataclass(frozen=True)
class Figure:
    """One figure worth a record: where it sits in the text it was found in
    (``start``/``end``, the number with its scale, no sign), how it is
    compared (``key``) and how it is written (``raw``)."""

    start: int
    end: int
    key: str
    raw: str


def _glued(text: str, start: int, end: int, scaled: bool) -> bool:
    """True when the number at ``text[start:end]`` is part of something
    else: a word (``K1``, ``v2``, ``2nd``), or digits joined by a dash, a
    slash or a colon (``2026-11-20``, ``555-1234``, ``10:30``, ``1/2``)."""
    before = text[start - 1] if start > 0 else ""
    if before and (before.isalnum() or before == "_"):
        return True
    if before in "-/:" and start > 1 and text[start - 2].isdigit():
        return True
    after = text[end] if end < len(text) else ""
    if not scaled and after and (after.isalnum() or after == "_"):
        return True
    if after in ("-", "/", ":") and end + 1 < len(text) and text[end + 1].isdigit():
        return True
    return False


def _money(text: str, start: int) -> bool:
    """A currency sign (or code) right before the number: ``$48,210``,
    ``-$12``, ``($ 1,200)``, ``USD 40``."""
    i = start - 1
    steps = 0
    while i >= 0 and steps < 3 and text[i] in " -(":
        i -= 1
        steps += 1
    if i < 0:
        return False
    if text[i] in CURRENCY:
        return True
    return bool(_CURRENCY_WORD_RX.search(text[max(0, i - 3):i + 1]))


def _percent(text: str, end: int) -> bool:
    j = end
    if j < len(text) and text[j] == " ":
        j += 1
    return j < len(text) and text[j] == "%"


def worth_a_record(digits: str, *, scaled: bool, money: bool, percent: bool) -> bool:
    """Whether a number is a FIGURE (30-a's rule): anything with a currency
    sign, a percent sign, decimals or a scale; otherwise a whole number of
    at least 100 that is not a bare year (1900-2100, written without a
    thousands comma). A number written another way ("1.2.3", "12,50") is
    left alone."""
    if money or percent or scaled:
        return True
    flat = digits.replace(",", "") if THOUSANDS_RX.fullmatch(digits) else digits
    if not PLAIN_RX.fullmatch(flat):
        return False
    if "." in flat:
        return True
    value = int(flat)
    if value < 100:
        return False
    if 1900 <= value <= 2100 and "," not in digits:
        return False
    return True


def figures(text: str) -> "list[Figure]":
    """Every figure worth a record in ``text``, in order (see the module
    docstring for the rule). Never raises."""
    out: list[Figure] = []
    text = str(text or "")
    for m in NUMBER_RX.finditer(text):
        scale = m.group(3) or m.group(4) or ""
        digits = "0." + m.group(1) if m.group(1) else m.group(2)
        start = m.start()
        end = m.end()
        # The number's own end (before a scale word), for the glue check.
        num_end = m.end(1) if m.group(1) else m.end(2)
        if _glued(text, start, num_end if not scale else end, bool(scale)):
            continue
        money = _money(text, start)
        percent = _percent(text, end)
        if not worth_a_record(digits, scaled=bool(scale), money=money, percent=percent):
            continue
        out.append(Figure(start, end, number_key(digits, scale), m.group(0)))
    return out


__all__ = [
    "CURRENCY", "Figure", "NUMBER_RX", "PLAIN_RX", "SCALES", "THOUSANDS_RX",
    "figures", "figures_in", "number_key", "numbers", "worth_a_record",
]
