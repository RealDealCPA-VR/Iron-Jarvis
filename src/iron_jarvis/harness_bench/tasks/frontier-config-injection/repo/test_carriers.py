import unittest
from decimal import Decimal

from shipping import carriers, settings, zones
from shipping.carriers import express, ground
from shipping.carriers.base import Quote
from shipping.model import Shipment, make_item

ADA = Shipment("Ada Lovelace", "10115", (make_item("book", (24, 17, 3), "0.9"), make_item("mug", (12, 9, 7), "0.4")))


class CarrierTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        zones.clear_cache()
        self.addCleanup(settings.reset)
        self.addCleanup(zones.clear_cache)

    def test_ground(self):
        self.assertEqual(ground.quote(ADA), Quote("Ground", "standard", Decimal("12.50"), "USD", 9))

    def test_express(self):
        self.assertEqual(express.quote(ADA), Quote("Express", "two-day", Decimal("22.50"), "USD", 2))
        settings.CONFIG.update(region="EU", currency="EUR", fuel_surcharge_pct=10)
        self.assertEqual(express.quote(ADA), Quote("Express", "next-day", Decimal("12.56"), "EUR", 1))

    def test_registry(self):
        self.assertEqual(carriers.names(), ["express", "ground"])
        self.assertIs(carriers.get("ground"), ground)
        with self.assertRaises(ValueError):
            carriers.get("pigeon")


if __name__ == "__main__":
    unittest.main()
