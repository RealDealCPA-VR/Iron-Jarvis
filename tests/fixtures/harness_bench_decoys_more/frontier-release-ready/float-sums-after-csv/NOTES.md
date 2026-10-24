Decoy (review): split() replaced by csv.reader, but the totals are still float sums, so a large amount loses its cents (9007199254740993.03 comes out as ...994.00). Passes test_report.py AND release_check.py.
Breaks invoicing/report.py's docstring: totals are "computed exactly (never with binary floating point)".
