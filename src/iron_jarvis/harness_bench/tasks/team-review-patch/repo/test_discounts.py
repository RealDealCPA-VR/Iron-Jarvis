import unittest

from orderdesk.discounts import coupon_percent, discount_cents


class DiscountTest(unittest.TestCase):
    def test_coupons(self):
        self.assertEqual(coupon_percent("save10"), 10)
        self.assertEqual(coupon_percent(" WELCOME5 "), 5)
        self.assertEqual(coupon_percent(None), 0)

    def test_unknown_coupon(self):
        with self.assertRaises(ValueError):
            coupon_percent("FREE")

    def test_discount_rounds_half_up(self):
        self.assertEqual(discount_cents(7000, 10), 700)
        self.assertEqual(discount_cents(1250, 10), 125)
        self.assertEqual(discount_cents(1010, 5), 51)


if __name__ == "__main__":
    unittest.main()
