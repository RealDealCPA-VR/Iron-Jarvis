"""Monthly reports over the stored rows."""
from collections import Counter

import storage


def monthly_report(args):
    """The `report` command: how many rows each month has."""
    months = Counter(row.get("date", "")[:7] for row in storage.load_rows())
    for month, count in sorted(months.items()):
        print(f"{month or 'no date'}: {count}")
    return 0
