"""How every exporter writes values."""


def format_cell(value):
    """One value as text, the same in every format."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def is_numeric_column(rows, index):
    """True when every non-empty value in column ``index`` is a number (a
    yes/no value is not a number). A column with no values is not numeric."""
    values = [row[index] for row in rows if row[index] is not None]
    return bool(values) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
    )
