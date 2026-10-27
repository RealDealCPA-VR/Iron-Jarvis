"""Invoice totals."""

from .tax import line_tax


def invoice_totals(lines, rate_percent):
    """Totals of an invoice.

    ``lines`` is a list of ``(description, amount_cents)``. Returns
    ``{"subtotal": ..., "tax": ..., "total": ...}`` in cents, where the tax is
    the sum of each line's own tax (``line_tax``), never the tax of the subtotal.
    """
    subtotal = sum(amount for _, amount in lines)
    tax = sum(line_tax(amount, rate_percent) for _, amount in lines)
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}
