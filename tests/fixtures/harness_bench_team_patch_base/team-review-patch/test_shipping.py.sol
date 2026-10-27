import unittest

from orderdesk.shipping import shipping_cents


class ShippingTest(unittest.TestCase):
    def test_free_from_100_dollars(self):
        self.assertEqual(shipping_cents(10000), 0)
        self.assertEqual(shipping_cents(25000), 0)

    def test_flat_fee_below(self):
        self.assertEqual(shipping_cents(9999), 599)


if __name__ == "__main__":
    unittest.main()
