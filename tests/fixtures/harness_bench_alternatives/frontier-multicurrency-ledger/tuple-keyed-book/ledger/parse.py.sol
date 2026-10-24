"""Reading CSV files (SPEC.md sections 3 and 7.3)."""

from __future__ import annotations

import csv
import datetime
import io
from dataclasses import dataclass

from .errors import ParseError
from .money import Money, normalise_code


@dataclass(frozen=True)
class Transaction:
    date: datetime.date
    account: str
    amount: Money
    description: str = ""


def table_rows(lines, required, optional=()):
    """Yield (line number, {column: stripped cell}) for each data row,
    following the shared rules of section 3."""
    reader = csv.reader(lines)
    columns = None
    for row in reader:
        if not any(cell.strip() for cell in row) or row[0].lstrip().startswith("#"):
            continue
        if columns is None:
            columns = []
            for raw in row:
                name = raw.strip().lower()
                if name not in required and name not in optional:
                    raise ParseError(reader.line_num, f"unknown column {name!r}")
                if name in columns:
                    raise ParseError(reader.line_num, f"duplicate column {name!r}")
                columns.append(name)
            missing = [name for name in required if name not in columns]
            if missing:
                raise ParseError(reader.line_num, f"missing column {missing[0]!r}")
            continue
        if len(row) > len(columns):
            raise ParseError(reader.line_num, f"expected {len(columns)} cells, found {len(row)}")
        cells = {name: "" for name in columns}
        for name, cell in zip(columns, row):
            cells[name] = cell.strip()
        yield reader.line_num, cells


def code_cell(text: str, line: int, empty=None) -> str:
    if text == "" and empty is not None:
        return empty
    try:
        return normalise_code(text)
    except ValueError:
        raise ParseError(line, f"bad currency {text!r}") from None


def _date(text: str, line: int) -> datetime.date:
    digits = text[:4] + text[5:7] + text[8:]
    if len(text) == 10 and text[4] == "-" and text[7] == "-" and digits.isascii() and digits.isdigit():
        try:
            return datetime.date.fromisoformat(text)
        except ValueError:
            pass
    raise ParseError(line, f"bad date {text!r}")


def _rows_to_transactions(lines):
    out = []
    for line, cells in table_rows(lines, ("date", "account", "amount"), ("description", "currency")):
        day = _date(cells["date"], line)
        if not cells["account"]:
            raise ParseError(line, "missing account")
        raw_amount = cells["amount"]
        try:
            Money.parse(raw_amount)
        except ValueError:
            raise ParseError(line, f"bad amount {raw_amount!r}") from None
        code = code_cell(cells.get("currency", ""), line, empty="USD")
        out.append(Transaction(day, cells["account"], Money.parse(raw_amount, code), cells.get("description", "")))
    return out


def parse_text(text: str) -> list[Transaction]:
    return _rows_to_transactions(io.StringIO(text, newline=""))


def read_transactions(path) -> list[Transaction]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return _rows_to_transactions(fh)


parse_lines = _rows_to_transactions
