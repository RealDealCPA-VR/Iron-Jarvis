'''Category totals for an invoices export.

The export is a CSV file with a header row ``customer,category,amount``. It is
written by our billing system with the standard CSV rules: a field that holds
a comma, a quote or a line break is wrapped in double quotes, and a quote
inside such a field is written twice (``"Paint ""Pro"""`` is ``Paint "Pro"``).
Amounts are exact decimal numbers such as ``19.99``, ``-5.00`` or ``5``. Blank
lines (empty, or holding only spaces or tabs) are ignored.

``summarize(path)`` returns ``{category: total}``, the totals as strings with
exactly two decimal places (``"123.40"``), computed exactly (never with
binary floating point), categories in the order they first appear.
'''

import csv
import sys
from decimal import Decimal

CENT = Decimal("0.01")


def summarize(path):
    totals = {}
    with open(path, encoding="utf-8", newline="") as fh:
        rows = csv.reader(fh)
        next(rows, None)  # the header
        for row in rows:
            if not row or not any(field.strip() for field in row):
                continue
            _customer, category, amount = row
            totals[category] = totals.get(category, Decimal("0")) + Decimal(amount.strip())
    return {category: str(total.quantize(CENT)) for category, total in totals.items()}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for category, total in summarize(argv[0]).items():
        print(f"{category}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
