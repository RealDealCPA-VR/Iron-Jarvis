"""Write sales rows as CSV text."""

COLUMNS = ("name", "city", "amount")


def to_csv(rows):
    """``rows`` is a list of dicts with the keys in COLUMNS. Returns CSV text:
    a header line, then one line per row, each line ending in a newline."""
    lines = [",".join(COLUMNS)]
    for row in rows:
        lines.append(",".join(str(row[col]) for col in COLUMNS))
    return "\n".join(lines) + "\n"
