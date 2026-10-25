Correct client and an invoice-number key, but the invoice whose charge stopped the run is set to status "failed" (and the run skips only paid/declined ones).
Breaks the invoices.py docstring: "any other failure ... STOPS the run: the exception is raised, this invoice stays \"open\"" (and Invoice.status is "open", "paid" or "declined").
