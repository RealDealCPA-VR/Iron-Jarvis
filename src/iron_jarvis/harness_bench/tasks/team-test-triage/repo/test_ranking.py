import unittest

from reports.ranking import repeat_customers, top_customers

ORDERS = [
    {"customer": "c2", "total_cents": 500},
    {"customer": "c1", "total_cents": 300},
    {"customer": "c3", "total_cents": 800},
    {"customer": "c1", "total_cents": 200},
]


class RankingTest(unittest.TestCase):
    def test_top_customers(self):
        self.assertEqual(top_customers(ORDERS, n=2), [("c3", 800), ("c1", 500)])

    def test_a_tie_goes_by_id(self):
        self.assertEqual(top_customers(ORDERS)[1:], [("c1", 500), ("c2", 500)])

    def test_repeat_customers(self):
        self.assertEqual(repeat_customers(ORDERS), ["c1"])


if __name__ == "__main__":
    unittest.main()
