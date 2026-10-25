Decoy (review): keeps the line-by-line loop and parses each line with csv.reader([line]), so a quoted field holding a line break is cut in two.
Breaks invoicing/report.py's docstring: the export follows "the standard CSV rules: a field that holds a comma, a quote or a line break is wrapped in double quotes".
