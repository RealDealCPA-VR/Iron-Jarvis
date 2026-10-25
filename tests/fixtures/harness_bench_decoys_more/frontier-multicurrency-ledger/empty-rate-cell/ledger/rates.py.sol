"""Exchange rates (SPEC.md, section 7.5)."""

from __future__ import annotations

import io
import re
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Iterable

from .errors import MissingRate, ParseError
from .money import CENT, Money, currency_code
from .parse import cell_currency, rows

REQUIRED_COLUMNS = ("base", "currency", "rate")
_RATE = re.compile(r"[0-9]+(\.[0-9]+)?")


class RateTable:
    """``rate(base, currency)``: how many units of ``base`` one unit of ``currency`` is worth."""

    def __init__(self, rates: dict[tuple[str, str], Decimal] | None = None) -> None:
        self._rates: dict[tuple[str, str], Decimal] = {}
        for (base, currency), rate in (rates or {}).items():
            self._rates[(currency_code(base), currency_code(currency))] = Decimal(rate)

    def rate(self, base: str, currency: str) -> Decimal:
        base_code = currency_code(base)
        code = currency_code(currency)
        if base_code == code:
            return Decimal(1)
        try:
            return self._rates[(base_code, code)]
        except KeyError:
            raise MissingRate(code, base_code) from None


def parse_rates(text: str) -> RateTable:
    return _parse(io.StringIO(text, newline=""))


def load_rates(path) -> RateTable:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return _parse(fh)


def _parse(lines: Iterable[str]) -> RateTable:
    table: dict[tuple[str, str], Decimal] = {}
    for cells, index, line in rows(lines, REQUIRED_COLUMNS, ()):
        base = cell_currency(cells[index["base"]], line, default="USD")
        currency = cell_currency(cells[index["currency"]], line, default="USD")
        raw_rate = cells[index["rate"]]
        if not _RATE.fullmatch(raw_rate) or Decimal(raw_rate) == 0:
            raise ParseError(line, f"bad rate {raw_rate!r}")
        if (base, currency) in table:
            raise ParseError(line, f"duplicate rate for {currency} (base {base})")
        table[(base, currency)] = Decimal(raw_rate)
    return RateTable(table)


def convert(money: Money, base: str, table: RateTable) -> Money:
    """``money`` in ``base``, rounded to cents (ROUND_HALF_EVEN)."""
    code = currency_code(base)
    if money.currency == code:
        return money
    rate = table.rate(code, money.currency)
    return Money((money.amount * rate).quantize(CENT, rounding=ROUND_HALF_EVEN), code)
