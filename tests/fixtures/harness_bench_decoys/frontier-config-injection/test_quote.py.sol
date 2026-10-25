import unittest
from decimal import Decimal

from shipping import quote, zones
from shipping.carriers.base import Quote
from shipping.model import Shipment, make_item
from shipping.settings import Settings

GRACE = Shipment("Grace Hopper", "94105", (
    make_item("laptop", (36, 25, 3), "2.1"),
    make_item("charger", (12, 6, 4), "0.5"),
    make_item("bag", (40, 30, 8), "0.8"),
))


class QuoteTests(unittest.TestCase):
    def setUp(self):
        zones.clear_cache()
        self.addCleanup(zones.clear_cache)

    def test_cheapest_first(self):
        self.assertEqual(
            quote.quote_all(GRACE, settings=Settings()),
            [
                Quote("Ground", "standard", Decimal("8.40"), "USD", 2),
                Quote("Express", "next-day", Decimal("15.12"), "USD", 1),
            ],
        )
        self.assertEqual(quote.cheapest(GRACE, settings=Settings()).carrier, "Ground")

    def test_summary(self):
        eu = Settings(region="EU", currency="EUR", fuel_surcharge_pct=12)
        self.assertEqual(
            quote.summary(GRACE, settings=eu),
            "Quotes for Grace Hopper (94105, zone 4):\n"
            "  Ground   standard   EUR 11.13  6 days\n"
            "  Express  next-day   EUR 20.03  1 day",
        )


if __name__ == "__main__":
    unittest.main()
