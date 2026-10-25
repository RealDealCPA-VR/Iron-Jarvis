"""Reading a transactions file (SPEC.md, sections 3 and 7.3)."""

from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass
from typing import Iterable

from .errors import ParseError
from .money import DEFAULT_CURRENCY, Money, currency_code

REQUIRED_COLUMNS = ("date", "account", "amount")
OPTIONAL_COLUMNS = ("description", "currency")

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Transaction:
    date: datetime.date
    account: str
    amount: Money
    description: str = ""


def read_transactions(path) -> list[Transaction]:
    """Read a transactions file (UTF-8, a byte-order mark is allowed)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return parse_lines(fh)


def parse_text(text: str) -> list[Transaction]:
    """Parse the text of a transactions file."""
    return parse_lines(io.StringIO(text, newline=""))


def parse_lines(lines: Iterable[str]) -> list[Transaction]:
    return [_transaction(cells, header, line) for cells, header, line in rows(lines, REQUIRED_COLUMNS, OPTIONAL_COLUMNS)]


def rows(lines: Iterable[str], required: tuple[str, ...], optional: tuple[str, ...]):
    """Yield ``(cells, header, line)`` for every data row of a CSV file.

    The shared rules of SPEC.md section 3: skipped rows, the header (any case,
    any order), stripped cells, short rows padded, long rows refused.
    """
    reader = csv.reader(lines)
    header: dict[str, int] | None = None
    width = 0
    for row in reader:
        line = reader.line_num
        if _skipped(row):
            continue
        if header is None:
            header = _header(row, line, required, optional)
            width = len(row)
            continue
        if len(row) > width:
            raise ParseError(line, f"expected {width} cells, found {len(row)}")
        yield [cell.strip() for cell in row] + [""] * (width - len(row)), header, line


def _skipped(row: list[str]) -> bool:
    if not row or all(not cell.strip() for cell in row):
        return True
    return row[0].lstrip().startswith("#")


def _header(row: list[str], line: int, required: tuple[str, ...], optional: tuple[str, ...]) -> dict[str, int]:
    known = required + optional
    index: dict[str, int] = {}
    for position, raw in enumerate(row):
        name = raw.strip().lower()
        if name not in known:
            raise ParseError(line, f"unknown column {name!r}")
        if name in index:
            raise ParseError(line, f"duplicate column {name!r}")
        index[name] = position
    for name in required:
        if name not in index:
            raise ParseError(line, f"missing column {name!r}")
    return index


def cell_currency(raw: str, line: int, *, default: str | None = None) -> str:
    """A code cell normalised to upper case (empty -> ``default``), else ParseError."""
    if not raw and default is not None:
        return default
    try:
        return currency_code(raw)
    except ValueError:
        raise ParseError(line, f"bad currency {raw!r}") from None


def _transaction(cells: list[str], index: dict[str, int], line: int) -> Transaction:
    raw_date = cells[index["date"]]
    try:
        if not _DATE.fullmatch(raw_date):
            raise ValueError(raw_date)
        date = datetime.date.fromisoformat(raw_date)
    except ValueError:
        raise ParseError(line, f"bad date {raw_date!r}") from None

    account = cells[index["account"]]
    if not account:
        raise ParseError(line, "missing account")

    raw_amount = cells[index["amount"]]
    try:
        Money.parse(raw_amount)
    except ValueError:
        raise ParseError(line, f"bad amount {raw_amount!r}") from None

    currency = DEFAULT_CURRENCY
    if "currency" in index:
        currency = cell_currency(cells[index["currency"]], line, default=DEFAULT_CURRENCY)

    description = cells[index["description"]] if "description" in index else ""
    return Transaction(date, account, Money.parse(raw_amount, currency), description)
