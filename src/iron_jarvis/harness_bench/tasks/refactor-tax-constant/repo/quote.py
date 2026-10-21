"""Quotes for customers."""


def quote_total(lines):
    """``lines`` is a list of net amounts. Returns net, tax and total."""
    net = round(sum(lines), 2)
    tax = round(net * 0.0825, 2)
    return {"net": net, "tax": tax, "total": round(net + tax, 2)}
