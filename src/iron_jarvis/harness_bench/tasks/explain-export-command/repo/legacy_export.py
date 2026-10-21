"""The first version of the export command, kept for reference."""
import csv
import sys

import storage


def run_export(args):
    """Handle `python main.py export`: write every stored row as CSV."""
    writer = csv.writer(sys.stdout)
    rows = storage.load_rows()
    if rows:
        writer.writerow(sorted(rows[0]))
        for row in rows:
            writer.writerow([row[key] for key in sorted(row)])
    return 0
