import unittest

from billing import BadRequest, FakeTransport, PaymentDeclined, PaymentsClient


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeTransport()
        self.client = PaymentsClient(self.server)

    def test_charge_returns_the_servers_charge(self):
        charge = self.client.charge("cus_1", 1250, "March rent")
        self.assertEqual(charge.customer, "cus_1")
        self.assertEqual(charge.amount_cents, 1250)
        self.assertEqual(charge.memo, "March rent")
        self.assertEqual([c["id"] for c in self.server.ledger], [charge.id])

    def test_every_request_carries_an_idempotency_key(self):
        self.client.charge("cus_1", 500, "a")
        self.client.charge("cus_2", 700, "b")
        keys = [r["headers"].get("Idempotency-Key") for r in self.server.requests]
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(keys))

    def test_declined_card_raises(self):
        self.server.decline("cus_9")
        with self.assertRaises(PaymentDeclined):
            self.client.charge("cus_9", 999, "x")
        self.assertEqual(self.server.ledger, [])

    def test_bad_amount_raises(self):
        with self.assertRaises(BadRequest):
            self.client.charge("cus_1", 0, "nothing")

    def test_charge_survives_a_lost_response(self):
        # Bug report: the network dropped the response and charge() raised
        # TransportTimeout, which stopped the whole invoice run.
        self.server.fail_next("drop_response")
        charge = self.client.charge("cus_3", 2000, "April rent")
        self.assertEqual(charge.customer, "cus_3")
        self.assertEqual(charge.amount_cents, 2000)


if __name__ == "__main__":
    unittest.main()
