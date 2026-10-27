"""Importing a stock count from a spreadsheet."""

import csv

from .stock import sku_key


def read_stock_csv(path):
    """The counts in a stock count file, as ``{sku: qty}`` (SKUs as
    ``sku_key`` gives them, quantities as int).

    The file is CSV with a header row naming the columns "sku" and "qty", in
    either order and in any case ("SKU,Qty"). It may start with a UTF-8 byte
    order mark (Excel writes one), use Windows line endings, and hold blank
    lines (skipped). A SKU counted twice is a ValueError.
    """
    counts = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if all((v or "").strip() == "" for v in row.values() if not isinstance(v, list)):
                continue
            row = {name.strip().lower(): value for name, value in row.items() if name is not None}
            key = sku_key(row["sku"])
            if key in counts:
                raise ValueError(f"{key} is counted twice")
            counts[key] = int(row["qty"])
    return counts
