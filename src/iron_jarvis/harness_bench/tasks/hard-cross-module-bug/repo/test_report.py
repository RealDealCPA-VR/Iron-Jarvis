import unittest

from order import Order
from report import order_report


class ReportTests(unittest.TestCase):
    def test_mugs_and_spoons(self):
        text = order_report(Order().add("MUG", 2).add("SPOON", 3), "TX")
        self.assertIn("Subtotal: 40.85", text)
        self.assertIn("Tax (TX): 3.37", text)
        self.assertIn("Total: 44.22", text)

    def test_no_tax_region(self):
        text = order_report(Order().add("TEAPOT"), "OR")
        self.assertTrue(text.endswith("Total: 34.95"))


if __name__ == "__main__":
    unittest.main()
