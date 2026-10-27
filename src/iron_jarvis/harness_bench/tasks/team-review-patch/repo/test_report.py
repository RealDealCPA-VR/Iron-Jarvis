import unittest

from orderdesk.report import best_sellers, sales_total

ORDERS = [
    {"lines": [{"name": "Notebook", "price_cents": 1250, "qty": 2}, {"name": "Pen", "price_cents": 300, "qty": 5}]},
    {"lines": [{"name": "Ink", "price_cents": 800, "qty": 2}, {"name": "Notebook", "price_cents": 1250, "qty": 1}]},
]


class ReportTest(unittest.TestCase):
    def test_sales_total(self):
        self.assertEqual(sales_total(ORDERS), 2500 + 1500 + 1600 + 1250)

    def test_best_sellers(self):
        self.assertEqual(best_sellers(ORDERS), ["Pen", "Notebook", "Ink"])
        self.assertEqual(best_sellers(ORDERS, top=1), ["Pen"])


if __name__ == "__main__":
    unittest.main()
