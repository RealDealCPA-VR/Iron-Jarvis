"""Line and order amounts, in cents."""


def line_total(price_cents, qty):
    """One line: price times quantity. ValueError for a quantity below 1."""
    if qty < 1:
        raise ValueError(f"a line needs a quantity of at least 1, not {qty}")
    return price_cents * qty


def subtotal(lines):
    """The sum of the lines (each a dict with "price_cents" and "qty")."""
    return sum(line_total(line["price_cents"], line["qty"]) for line in lines)


def money(cents):
    """Cents as dollars: 123456 -> "$1,234.56"."""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rest:02d}"
