Decoy (review): the obvious csv.reader fix with exact decimals, skipping only the rows csv reports as empty, so a blank line that holds a space or a tab stops the report with "not enough values to unpack".
Breaks the export format in invoicing/report.py's docstring: "Blank lines (empty, or holding only spaces or tabs) are ignored" (and CHANGELOG 2.2.0).
