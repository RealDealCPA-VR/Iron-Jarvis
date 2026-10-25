"""Consolidate the branch offices' order exports (README.md says how).

    python consolidate.py                # exports/ -> orders.csv + summary.json here
    python consolidate.py EXPORTS OUT

An alternative hand-in: an Office table, a hand-written amount reader (no
regular expressions), and a small accumulator object.
"""
import csv
import io
import json
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class Office:
    delimiter: str = ","
    id_col: str = "order_id"
    customer_col: str = "customer"
    amount_col: str = "amount"
    thousands: str = ","
    point: str = "."


OFFICES = {"berlin": Office(";", "Bestellnummer", "Kunde", "Betrag", ".", ",")}
PLAIN = Office()

BOMS = ((b"\xff\xfe", "utf-16-le"), (b"\xef\xbb\xbf", "utf-8"))


def text_of(raw: bytes) -> str:
    for bom, encoding in BOMS:
        if raw[: len(bom)] == bom:
            return raw[len(bom):].decode(encoding)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _all_digits(s: str) -> bool:
    return bool(s) and all("0" <= ch <= "9" for ch in s)


def read_amount(cell: str, office: Office):
    """Decimal, or None when the cell is not an amount."""
    s = cell.strip()
    sign = ""
    if s.startswith("-"):
        sign, s = "-", s[1:]
    whole, sep, frac = s.partition(office.point)
    if sep and not (1 <= len(frac) <= 2 and _all_digits(frac)):
        return None
    groups = whole.split(office.thousands)
    if len(groups) == 1:
        if not _all_digits(whole):
            return None
    else:
        if not (1 <= len(groups[0]) <= 3 and _all_digits(groups[0])):
            return None
        if not all(len(g) == 3 and _all_digits(g) for g in groups[1:]):
            return None
    return Decimal(sign + "".join(groups) + ("." + frac if sep else ""))


def one_line(name: str) -> str:
    return name.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip(" ")


@dataclass
class Result:
    orders: dict = field(default_factory=dict)
    skipped: int = 0
    duplicates: int = 0

    def take(self, office_name: str, office: Office, header, row) -> None:
        if len(row) != len(header):
            self.skipped += 1
            return
        cell = dict(zip(header, row))
        order_id = cell[office.id_col].strip(" ")
        amount = read_amount(cell[office.amount_col], office)
        if order_id == "" or amount is None:
            self.skipped += 1
            return
        if order_id in self.orders:
            self.duplicates += 1
            return
        self.orders[order_id] = (office_name, one_line(cell[office.customer_col]), amount)


def consolidate(exports_dir=os.path.join(HERE, "exports"), out_dir=HERE):
    result = Result()
    names = sorted(n for n in os.listdir(exports_dir) if n.endswith(".csv"))
    for name in names:
        office_name = name.split("_", 1)[0].lower()
        office = OFFICES.get(office_name, PLAIN)
        with open(os.path.join(exports_dir, name), "rb") as fh:
            text = text_of(fh.read())
        rows = csv.reader(io.StringIO(text, newline=""), delimiter=office.delimiter)
        header = next(rows, None)
        if header is None:
            continue
        for row in rows:
            if row:
                result.take(office_name, office, header, row)

    os.makedirs(out_dir, exist_ok=True)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(("order_id", "office", "customer", "amount"))
    per_office = {}
    for order_id, (office_name, customer, amount) in sorted(result.orders.items()):
        writer.writerow((order_id, office_name, customer, "%.2f" % amount))
        n, total = per_office.get(office_name, (0, Decimal("0")))
        per_office[office_name] = (n + 1, total + amount)
    with open(os.path.join(out_dir, "orders.csv"), "wb") as fh:
        fh.write(buf.getvalue().encode("utf-8"))
    summary = {
        "skipped_rows": result.skipped,
        "duplicates": result.duplicates,
        "offices": {k: {"orders": n, "total": "%.2f" % t} for k, (n, t) in per_office.items()},
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh)
    return summary


def main(argv):
    if len(argv) not in (1, 3):
        print("usage: python consolidate.py [EXPORTS OUT]", file=sys.stderr)
        return 2
    summary = consolidate(argv[1], argv[2]) if len(argv) == 3 else consolidate()
    count = sum(v["orders"] for v in summary["offices"].values())
    print(f"{count} orders, {summary['skipped_rows']} bad rows skipped, {summary['duplicates']} duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
