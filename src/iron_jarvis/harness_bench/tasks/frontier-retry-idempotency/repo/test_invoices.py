import unittest

from billing import FakeTransport, PaymentsClient
from invoices import Invoice, run_invoices


def _batch():
    return [
        Invoice("INV-1001", "cus_a", 1500),
        Invoice("INV-1002", "cus_b", 2500),
        Invoice("INV-1003", "cus_c", 990),
    ]


class InvoiceRunTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeTransport()
        self.client = PaymentsClient(self.server)

    def test_run_pays_open_invoices(self):
        invoices = _batch()
        summary = run_invoices(self.client, invoices)
        self.assertEqual(summary.paid, ["INV-1001", "INV-1002", "INV-1003"])
        self.assertEqual([i.status for i in invoices], ["paid", "paid", "paid"])
        self.assertEqual([i.charge_id for i in invoices], [c["id"] for c in self.server.ledger])

    def test_declined_invoice_is_marked_and_the_run_goes_on(self):
        self.server.decline("cus_b")
        invoices = _batch()
        summary = run_invoices(self.client, invoices)
        self.assertEqual(summary.declined, ["INV-1002"])
        self.assertEqual([i.status for i in invoices], ["paid", "declined", "paid"])
        self.assertEqual(invoices[1].charge_id, "")

    def test_paid_invoices_are_skipped(self):
        invoices = _batch()
        invoices[0].status = "paid"
        invoices[0].charge_id = "ch_from_last_month"
        summary = run_invoices(self.client, invoices)
        self.assertEqual(summary.paid, ["INV-1002", "INV-1003"])
        self.assertEqual(len(self.server.ledger), 2)
        self.assertEqual(invoices[0].charge_id, "ch_from_last_month")

    def test_memo_names_the_invoice(self):
        run_invoices(self.client, _batch()[:1])
        self.assertEqual(self.server.ledger[0]["memo"], "Invoice INV-1001")


if __name__ == "__main__":
    unittest.main()
