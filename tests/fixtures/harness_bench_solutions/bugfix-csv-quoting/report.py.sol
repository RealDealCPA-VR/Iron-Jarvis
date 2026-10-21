"""Write sales rows as CSV text."""

import csv
import io

COLUMNS = ("name", "city", "amount")


def to_csv(rows):
    """``rows`` is a list of dicts with the keys in COLUMNS. Returns CSV text:
    a header line, then one line per row, each line ending in a newline."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([row[col] for col in COLUMNS])
    return buf.getvalue()
