"""Consolidate the branch offices' order exports (README.md says how).

    python consolidate.py                # exports/ -> orders.csv + summary.json here
    python consolidate.py EXPORTS OUT
"""
import codecs
import csv
import io
import json
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))

#: office -> (order id column, customer column, amount column)
COLUMNS = {"berlin": ("Bestellnummer", "Kunde", "Betrag")}
DEFAULT_COLUMNS = ("order_id", "customer", "amount")
#: office -> delimiter (every other office: ",")
DELIMITERS = {"berlin": ";"}
#: Offices that write amounts the German way (1.234,50).
GERMAN = {"berlin"}

_DOT_AMOUNT = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?")
_COMMA_AMOUNT = re.compile(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def decode(data):
    """The text of one export, by README's rules (decided per file)."""
    if data.startswith(codecs.BOM_UTF16_LE):
        return data[len(codecs.BOM_UTF16_LE):].decode("utf-16-le")
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def parse_amount(text, german):
    """The amount as a Decimal, or None when ``text`` is not an amount."""
    text = text.strip()
    if german:
        if not _COMMA_AMOUNT.fullmatch(text):
            return None
        return Decimal(text.replace(".", "").replace(",", "."))
    if not _DOT_AMOUNT.fullmatch(text):
        return None
    return Decimal(text.replace(",", ""))


def consolidate(exports_dir=os.path.join(HERE, "exports"), out_dir=HERE):
    """Read every export in ``exports_dir``; write orders.csv and summary.json
    into ``out_dir``; return the summary."""
    orders = {}
    seen = set()
    skipped = 0
    duplicates = 0
    for name in sorted(os.listdir(exports_dir)):
        if not name.endswith(".csv"):
            continue
        office = name.split("_", 1)[0].lower()
        id_col, customer_col, amount_col = COLUMNS.get(office, DEFAULT_COLUMNS)
        german = office in GERMAN
        with open(os.path.join(exports_dir, name), "rb") as fh:
            text = decode(fh.read())
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITERS.get(office, ","))
        header = next(reader, None)
        if not header:
            continue
        where = {column.strip(): i for i, column in enumerate(header)}
        missing = [c for c in (id_col, customer_col, amount_col) if c not in where]
        if missing:
            raise ValueError(f"{name}: the header has no {', '.join(missing)} column")
        for row in reader:
            if not row:
                continue  # a blank line is not a row
            if len(row) != len(header):
                skipped += 1
                continue
            order_id = row[where[id_col]].strip()
            if order_id and order_id in seen:
                duplicates += 1
                continue
            seen.add(order_id)
            amount = parse_amount(row[where[amount_col]], german)
            if not order_id or amount is None:
                skipped += 1
                continue
            customer = _LINE_BREAK.sub(" ", row[where[customer_col]]).strip()
            orders[order_id] = (office, customer, amount)

    os.makedirs(out_dir, exist_ok=True)
    totals = {}
    with open(os.path.join(out_dir, "orders.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["order_id", "office", "customer", "amount"])
        for order_id in sorted(orders):
            office, customer, amount = orders[order_id]
            writer.writerow([order_id, office, customer, f"{amount:.2f}"])
            count, total = totals.get(office, (0, Decimal(0)))
            totals[office] = (count + 1, total + amount)
    summary = {
        "offices": {
            office: {"orders": count, "total": f"{total:.2f}"} for office, (count, total) in sorted(totals.items())
        },
        "skipped_rows": skipped,
        "duplicates": duplicates,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    return summary


def main(argv):
    if len(argv) not in (1, 3):
        print("usage: python consolidate.py [EXPORTS OUT]", file=sys.stderr)
        return 2
    if len(argv) == 3:
        summary = consolidate(argv[1], argv[2])
    else:
        summary = consolidate()
    orders = sum(office["orders"] for office in summary["offices"].values())
    print(f"{orders} orders, {summary['skipped_rows']} bad rows skipped, {summary['duplicates']} duplicates")
    for office, entry in summary["offices"].items():
        print(f"  {office}: {entry['orders']} orders, total {entry['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
