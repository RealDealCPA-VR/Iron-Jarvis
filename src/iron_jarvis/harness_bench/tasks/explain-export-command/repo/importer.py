"""Importing rows from a CSV file."""
import csv

import storage


def import_rows(args):
    """The `import` command: read a CSV file and store its rows."""
    if not args:
        print("usage: python main.py import FILE.csv")
        return 2
    with open(args[0], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    storage.save_rows(storage.load_rows() + rows)
    print(f"imported {len(rows)} rows")
    return 0
