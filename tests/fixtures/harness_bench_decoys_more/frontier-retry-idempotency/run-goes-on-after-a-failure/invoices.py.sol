"""The monthly invoice run: charge a batch of invoices.

``run_invoices(client, invoices)`` charges every OPEN invoice, in order, and
records what happened ON the invoice itself (the list of invoices is our
record — the caller saves it):

* charged: ``status = "paid"`` and ``charge_id`` = the id of the server's
  charge;
* the card was declined: ``status = "declined"``, and the run goes on with the
  next invoice. A declined card is never tried again;
* any other failure (for example the server still cannot be reached after the
  client's retries) STOPS the run: the exception is raised, this invoice stays
  ``"open"``, and the invoices after it are not touched.

Runs are RESUMABLE. After a run stopped, fix the cause and call
``run_invoices`` again with the same invoices: paid and declined invoices are
skipped, and no invoice is ever charged twice — not even the one that stopped
the run, whose charge may have reached the server before the connection
failed (``billing/server_api.md``, "Idempotency").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing import PaymentDeclined, PaymentError, PaymentsClient


@dataclass
class Invoice:
    number: str               # unique, e.g. "INV-1001"
    customer: str             # the payments server's customer id
    amount_cents: int
    status: str = "open"      # "open", "paid" or "declined"
    charge_id: str = ""       # the server's charge id, once paid


@dataclass
class RunSummary:
    paid: list[str] = field(default_factory=list)       # invoice numbers paid in this run
    declined: list[str] = field(default_factory=list)   # invoice numbers declined in this run


def memo_for(invoice: Invoice) -> str:
    return f"Invoice {invoice.number}"


def charge_key_for(invoice: Invoice) -> str:
    """The Idempotency-Key of an invoice's charge. It is made from the invoice
    number, so a resumed run sends the same key as the run that stopped, and
    the server answers with the charge it already made instead of charging
    again."""
    return f"invoice-{invoice.number}"


def run_invoices(client: PaymentsClient, invoices: list[Invoice]) -> RunSummary:
    summary = RunSummary()
    for invoice in invoices:
        if invoice.status != "open":
            continue
        try:
            charge = client.charge(
                invoice.customer, invoice.amount_cents, memo_for(invoice),
                idempotency_key=charge_key_for(invoice),
            )
        except PaymentDeclined:
            invoice.status = "declined"
            summary.declined.append(invoice.number)
            continue
        except PaymentError:
            # the server could not be reached even after the client's retries:
            # leave this invoice open for the next run and go on with the batch
            continue
        invoice.status = "paid"
        invoice.charge_id = charge.id
        summary.paid.append(invoice.number)
    return summary
