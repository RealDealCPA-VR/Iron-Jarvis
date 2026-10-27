import unittest

from reports.aggregate import busiest_day, daily_totals, order_day


def order(created, cents=100, customer="c1"):
    return {"created": created, "total_cents": cents, "customer": customer}


class AggregateTest(unittest.TestCase):
    def test_a_utc_order_stays_on_its_day(self):
        self.assertEqual(order_day(order("2026-03-01T10:00:00Z")), "2026-03-01")

    def test_a_late_evening_in_the_west_is_the_next_utc_day(self):
        self.assertEqual(order_day(order("2026-03-01T23:30:00-05:00")), "2026-03-02")

    def test_daily_totals_across_midnight(self):
        orders = [order("2026-03-01T22:00:00-05:00", 1000), order("2026-03-02T01:00:00Z", 500),
                  order("2026-03-01T12:00:00Z", 200)]
        self.assertEqual(daily_totals(orders), {"2026-03-01": 200, "2026-03-02": 1500})

    def test_busiest_day(self):
        orders = [order("2026-03-01T21:00:00-04:00"), order("2026-03-01T22:15:00-04:00"),
                  order("2026-03-01T09:00:00Z")]
        self.assertEqual(busiest_day(orders), "2026-03-02")
        self.assertIsNone(busiest_day([]))


if __name__ == "__main__":
    unittest.main()
