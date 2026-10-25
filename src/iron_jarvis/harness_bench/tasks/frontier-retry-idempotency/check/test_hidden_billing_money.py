# IJ-BENCH-HIDDEN-CHECK frontier-retry-idempotency
import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from billing import (  # noqa: E402
    BadRequest,
    FakeTransport,
    IdempotencyConflict,
    PaymentDeclined,
    PaymentsClient,
    ServerBusy,
    TransportTimeout,
)
from invoices import Invoice, run_invoices  # noqa: E402

CRLF = bytes([13, 10])
LF = bytes([10])
UNCHANGED = {
    "billing/transport.py": "5e5ddc6941cbe48ae447d95a507c89f60e754f1be230c22a0d6ddbda4bd1ad03",
    "billing/server_api.md": "e32c2d8a88ae6d99c110905d1bd8df4025b84eb425d508183963898238b19c6c",
    "test_client.py": "968ad2f225e9a29b65a9d1521a6727e8f3f4fb394a324b89e9b80f19c7a307c7",
    "test_invoices.py": "2c7168f4b522f570146ec19cfe1dbcbd1fbbcef31d5b02e1014244c0e7e9e691",
}


def _keys(server, customer=None):
    out = []
    for request in server.requests:
        if customer is None or (request.get("body") or {}).get("customer") == customer:
            out.append((request.get("headers") or {}).get("Idempotency-Key"))
    return out


class ProtectedFiles(unittest.TestCase):
    def test_the_server_model_the_api_doc_and_the_tests_are_unchanged(self):
        for name, digest in UNCHANGED.items():
            with self.subTest(name=name):
                with open(os.path.join(HERE, *name.split("/")), "rb") as fh:
                    data = fh.read().replace(CRLF, LF)
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{name} was changed")


class OneChargeIsChargedOnce(unittest.TestCase):
    def setUp(self):
        self.server = FakeTransport()
        self.client = PaymentsClient(self.server)

    def test_a_lost_response_is_retried_and_charged_once(self):
        self.server.fail_next("drop_response")
        charge = self.client.charge("cus_1", 2000, "April rent")
        self.assertEqual(len(self.server.ledger), 1,
                         "the server made more than one charge for ONE call of charge()")
        self.assertEqual(charge.id, self.server.ledger[0]["id"])
        keys = _keys(self.server)
        self.assertEqual(len(keys), 2)
        self.assertTrue(keys[0], "a charge must carry an Idempotency-Key")
        self.assertEqual(keys[0], keys[1], "every attempt of one charge must carry the same key")

    def test_two_lost_responses_take_three_attempts_and_one_charge(self):
        self.server.fail_next("drop_response", "drop_response")
        charge = self.client.charge("cus_1", 1234, "x")
        self.assertEqual([c["id"] for c in self.server.ledger], [charge.id])
        keys = _keys(self.server)
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 1)

    def test_a_lost_request_is_retried_with_the_same_key(self):
        self.server.fail_next("drop_request")
        charge = self.client.charge("cus_2", 500, "y")
        self.assertEqual([c["id"] for c in self.server.ledger], [charge.id])
        keys = _keys(self.server)
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    def test_a_busy_server_is_retried(self):
        self.server.fail_next("busy", "busy")
        charge = self.client.charge("cus_3", 700, "z")
        self.assertEqual([c["id"] for c in self.server.ledger], [charge.id])
        keys = _keys(self.server)
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 1)

    def test_a_mix_of_faults_still_charges_once(self):
        self.server.fail_next("busy", "drop_response")
        charge = self.client.charge("cus_4", 900, "w")
        self.assertEqual([c["id"] for c in self.server.ledger], [charge.id])
        self.assertEqual(len(set(_keys(self.server))), 1)

    def test_an_explicit_key_is_sent_on_every_attempt(self):
        self.server.fail_next("drop_response", "drop_request")
        self.client.charge("cus_5", 300, "v", idempotency_key="invoice-77")
        self.assertEqual(_keys(self.server), ["invoice-77"] * 3)
        self.assertEqual(len(self.server.ledger), 1)


class GivingUp(unittest.TestCase):
    def setUp(self):
        self.server = FakeTransport()
        self.client = PaymentsClient(self.server)

    def test_at_most_three_attempts_when_requests_are_lost(self):
        self.server.fail_next(*["drop_request"] * 5)
        with self.assertRaises(TransportTimeout):
            self.client.charge("cus_1", 100, "a")
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(self.server.ledger, [])

    def test_at_most_three_attempts_when_responses_are_lost(self):
        self.server.fail_next(*["drop_response"] * 5)
        with self.assertRaises(TransportTimeout):
            self.client.charge("cus_1", 100, "a")
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(len(self.server.ledger), 1, "the first attempt was charged; no retry may charge again")
        self.assertEqual(len(set(_keys(self.server))), 1)

    def test_at_most_three_attempts_when_the_server_stays_busy(self):
        self.server.fail_next(*["busy"] * 4)
        with self.assertRaises(ServerBusy):
            self.client.charge("cus_1", 100, "a")
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(self.server.ledger, [])

    def test_the_last_error_is_raised(self):
        self.server.fail_next("busy", "busy", "drop_request")
        with self.assertRaises(TransportTimeout):
            self.client.charge("cus_1", 100, "a")
        self.assertEqual(len(self.server.requests), 3)


class NotRetried(unittest.TestCase):
    def setUp(self):
        self.server = FakeTransport()
        self.client = PaymentsClient(self.server)

    def test_a_declined_card_is_not_retried(self):
        self.server.decline("cus_9")
        with self.assertRaises(PaymentDeclined):
            self.client.charge("cus_9", 999, "x")
        self.assertEqual(len(self.server.requests), 1, "a declined card was tried again")
        self.assertEqual(self.server.ledger, [])

    def test_a_decline_after_a_lost_request_is_not_retried_either(self):
        self.server.decline("cus_9")
        self.server.fail_next("drop_request")
        with self.assertRaises(PaymentDeclined):
            self.client.charge("cus_9", 999, "x")
        self.assertEqual(len(self.server.requests), 2)

    def test_a_bad_request_is_not_retried(self):
        with self.assertRaises(BadRequest):
            self.client.charge("cus_1", 0, "nothing")
        self.assertEqual(len(self.server.requests), 1)

    def test_a_key_conflict_is_not_retried(self):
        self.client.charge("cus_1", 100, "first", idempotency_key="k-1")
        with self.assertRaises(IdempotencyConflict):
            self.client.charge("cus_1", 200, "second", idempotency_key="k-1")
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(len(self.server.ledger), 1)


class DifferentChargesDifferentKeys(unittest.TestCase):
    def test_identical_charges_are_two_charges(self):
        server = FakeTransport()
        client = PaymentsClient(server)
        first = client.charge("cus_1", 450, "coffee beans")
        second = client.charge("cus_1", 450, "coffee beans")
        self.assertNotEqual(first.id, second.id, "two charges were collapsed into one")
        self.assertEqual(len(server.ledger), 2)
        keys = _keys(server)
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1], "two different charges must not share a key")

    def test_each_charge_keeps_its_own_key_through_retries(self):
        server = FakeTransport()
        client = PaymentsClient(server)
        server.fail_next("drop_response", None, "drop_request")
        client.charge("cus_1", 450, "coffee beans")
        client.charge("cus_1", 450, "coffee beans")
        keys = _keys(server)
        self.assertEqual(len(keys), 4)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[2], keys[3])
        self.assertNotEqual(keys[0], keys[2])
        self.assertEqual(len(server.ledger), 2)


def _invoices(*rows):
    return [Invoice(number, customer, amount) for number, customer, amount in rows]


class InvoiceRuns(unittest.TestCase):
    def setUp(self):
        self.server = FakeTransport()
        self.client = PaymentsClient(self.server)

    def assert_one_charge_each(self, invoices):
        for invoice in invoices:
            made = self.server.charges_for(invoice.customer)
            self.assertEqual(len(made), 1, f"{invoice.number} was charged {len(made)} times")
            self.assertEqual(invoice.status, "paid")
            self.assertEqual(invoice.charge_id, made[0]["id"])
            self.assertEqual(made[0]["amount_cents"], invoice.amount_cents)

    def test_lost_responses_do_not_stop_the_run_or_double_charge(self):
        invoices = _invoices(
            ("INV-2001", "cus_a", 1500), ("INV-2002", "cus_b", 2500), ("INV-2003", "cus_c", 990),
            ("INV-2004", "cus_d", 120), ("INV-2005", "cus_e", 77),
        )
        self.server.fail_next("drop_response", None, None, "drop_response", "drop_response",
                              None, "busy", None)
        summary = run_invoices(self.client, invoices)
        self.assertEqual(summary.paid, [i.number for i in invoices])
        self.assert_one_charge_each(invoices)
        self.assertEqual(len(self.server.ledger), 5)

    def test_two_invoices_of_one_customer_are_two_charges(self):
        invoices = _invoices(("INV-3001", "cus_a", 1500), ("INV-3002", "cus_a", 1500))
        self.server.fail_next("drop_response", None, "drop_request")
        run_invoices(self.client, invoices)
        self.assertEqual(len(self.server.ledger), 2)
        self.assertEqual([i.status for i in invoices], ["paid", "paid"])
        self.assertNotEqual(invoices[0].charge_id, invoices[1].charge_id)
        self.assertEqual(sorted(i.charge_id for i in invoices), sorted(c["id"] for c in self.server.ledger))

    def test_a_declined_invoice_is_tried_once_and_the_run_goes_on(self):
        invoices = _invoices(("INV-4001", "cus_a", 100), ("INV-4002", "cus_b", 200), ("INV-4003", "cus_c", 300))
        self.server.decline("cus_b")
        summary = run_invoices(self.client, invoices)
        self.assertEqual(summary.declined, ["INV-4002"])
        self.assertEqual([i.status for i in invoices], ["paid", "declined", "paid"])
        self.assertEqual(len(_keys(self.server, "cus_b")), 1, "a declined card was tried again")

    def test_a_run_that_stopped_and_is_run_again_never_charges_twice(self):
        invoices = _invoices(("INV-5001", "cus_a", 1000), ("INV-5002", "cus_b", 2000), ("INV-5003", "cus_c", 3000))
        # cus_b's charge reaches the server, but every response is lost: the
        # client gives up after its retries and the run stops there.
        self.server.fail_next(None, "drop_response", "drop_response", "drop_response")
        with self.assertRaises(TransportTimeout):
            run_invoices(self.client, invoices)
        self.assertEqual(invoices[0].status, "paid")
        self.assertEqual(invoices[1].status, "open",
                         "the invoice whose charge stopped the run must stay \"open\" (invoices.py)")
        self.assertEqual(invoices[2].status, "open", "the run must stop at the invoice that failed")
        self.assertEqual(_keys(self.server, "cus_c"), [], "the run must stop at the invoice that failed")
        self.assertEqual(len(_keys(self.server, "cus_b")), 3)
        self.assertEqual(len(self.server.charges_for("cus_b")), 1)

        # The network is back: the job is run again. A new run is a new
        # process with a NEW client (invoices.py): only the saved invoices
        # carry over, so a key kept anywhere else is gone.
        summary = run_invoices(PaymentsClient(self.server), invoices)
        self.assertEqual(sorted(summary.paid), ["INV-5002", "INV-5003"])
        self.assert_one_charge_each(invoices)
        self.assertEqual(len(self.server.ledger), 3)
        keys_b = _keys(self.server, "cus_b")
        self.assertEqual(len(keys_b), 4)
        self.assertEqual(len(set(keys_b)), 1, "the resumed run must send the key the stopped run used")

    def test_a_resumed_run_skips_what_is_done(self):
        invoices = _invoices(("INV-6001", "cus_a", 1000), ("INV-6002", "cus_b", 2000))
        run_invoices(self.client, invoices)
        before = len(self.server.requests)
        summary = run_invoices(self.client, invoices)
        self.assertEqual(summary.paid, [])
        self.assertEqual(len(self.server.requests), before)
        self.assert_one_charge_each(invoices)


if __name__ == "__main__":
    unittest.main()
