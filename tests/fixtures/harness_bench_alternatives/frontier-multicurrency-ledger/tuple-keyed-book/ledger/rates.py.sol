"""Exchange rates (SPEC.md section 7.5)."""

from __future__ import annotations

import io
from decimal import ROUND_HALF_EVEN, Decimal

from .errors import MissingRate, ParseError
from .money import CENT, Money, normalise_code
from .parse import code_cell, table_rows


def _rate_value(text: str, line: int) -> Decimal:
    whole, dot, frac = text.partition(".")
    good = bool(whole) and whole.isascii() and whole.isdigit() and (
        not dot or (bool(frac) and frac.isascii() and frac.isdigit())
    )
    if good:
        value = Decimal(text)
        if value > 0:
            return value
    raise ParseError(line, f"bad rate {text!r}")


class RateTable:
    def __init__(self) -> None:
        self._by_pair: dict[str, Decimal] = {}

    def _add(self, base: str, currency: str, rate: Decimal, line: int) -> None:
        key = f"{base}/{currency}"
        if key in self._by_pair:
            raise ParseError(line, f"duplicate rate for {currency} (base {base})")
        self._by_pair[key] = rate

    def rate(self, base: str, currency: str) -> Decimal:
        b, c = normalise_code(base), normalise_code(currency)
        if b == c:
            return Decimal(1)
        if f"{b}/{c}" not in self._by_pair:
            raise MissingRate(c, b)
        return self._by_pair[f"{b}/{c}"]


def _table(lines) -> RateTable:
    table = RateTable()
    for line, cells in table_rows(lines, ("base", "currency", "rate")):
        base = code_cell(cells["base"], line)
        currency = code_cell(cells["currency"], line)
        table._add(base, currency, _rate_value(cells["rate"], line), line)
    return table


def parse_rates(text: str) -> RateTable:
    return _table(io.StringIO(text, newline=""))


def load_rates(path) -> RateTable:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return _table(fh)


def convert(money: Money, base: str, table: RateTable) -> Money:
    target = normalise_code(base)
    if target == money.currency:
        return money
    exact = money.amount * table.rate(target, money.currency)
    return Money(exact.quantize(CENT, rounding=ROUND_HALF_EVEN), target)
