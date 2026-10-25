"""Reading a transactions file (SPEC.md, section 3)."""

from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass
from typing import Iterable

from .errors import ParseError
from .money import Money

REQUIRED_COLUMNS = ("date", "account", "amount")
OPTIONAL_COLUMNS = ("description",)

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
    reader = csv.reader(lines)
    header: dict[str, int] | None = None
    width = 0
    out: list[Transaction] = []
    for row in reader:
        line = reader.line_num
        if _skipped(row):
            continue
        if header is None:
            header = _header(row, line)
            width = len(row)
            continue
        if len(row) > width:
            raise ParseError(line, f"expected {width} cells, found {len(row)}")
        cells = [cell.strip() for cell in row] + [""] * (width - len(row))
        out.append(_transaction(cells, header, line))
    return out


def _skipped(row: list[str]) -> bool:
    if not row or all(not cell.strip() for cell in row):
        return True
    return row[0].lstrip().startswith("#")


def _header(row: list[str], line: int) -> dict[str, int]:
    known = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
    index: dict[str, int] = {}
    for position, raw in enumerate(row):
        name = raw.strip().lower()
        if name not in known:
            raise ParseError(line, f"unknown column {name!r}")
        if name in index:
            raise ParseError(line, f"duplicate column {name!r}")
        index[name] = position
    for name in REQUIRED_COLUMNS:
        if name not in index:
            raise ParseError(line, f"missing column {name!r}")
    return index


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
        amount = Money.parse(raw_amount)
    except ValueError:
        raise ParseError(line, f"bad amount {raw_amount!r}") from None

    description = cells[index["description"]] if "description" in index else ""
    return Transaction(date, account, amount, description)
