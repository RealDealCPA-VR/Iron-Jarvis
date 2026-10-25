Correct client and invoice-number key, but run_invoices catches every other PaymentError and goes on with the next invoice.
Breaks the invoices.py docstring: "any other failure ... STOPS the run: the exception is raised, ... and the invoices after it are not touched".
