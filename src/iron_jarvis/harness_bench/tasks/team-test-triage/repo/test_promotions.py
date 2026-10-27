import unittest

from inventory.promotions import PRICE_LIST, promoted_prices


class PromotionTest(unittest.TestCase):
    def test_ten_percent_off_one_product(self):
        self.assertEqual(promoted_prices(10, ["ab-1"])["AB-1"], 2250)

    def test_twenty_percent_off_two_products(self):
        self.assertEqual(promoted_prices(20, [" AB-1", "cd-2 "]),
                         {"AB-1": 2000, "CD-2": 960, "EF-3": 1800, "GH-4": 999})

    def test_half_a_cent_rounds_up(self):
        self.assertEqual(promoted_prices(50, ["GH-4"])["GH-4"], 500)

    def test_every_product_is_on_the_list(self):
        self.assertEqual(sorted(PRICE_LIST), ["AB-1", "CD-2", "EF-3", "GH-4"])


if __name__ == "__main__":
    unittest.main()
