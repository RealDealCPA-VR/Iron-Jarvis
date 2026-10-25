import unittest
from decimal import Decimal

from shipping import carriers
from shipping.carriers import express, ground
from shipping.carriers.base import Quote
from shipping.model import Shipment, make_item
from shipping.settings import Settings

ADA = Shipment("Ada Lovelace", "10115", (make_item("book", (24, 17, 3), "0.9"), make_item("mug", (12, 9, 7), "0.4")))


class CarrierTests(unittest.TestCase):
    def test_ground(self):
        self.assertEqual(ground.quote(ADA, settings=Settings()), Quote("Ground", "standard", Decimal("12.50"), "USD", 9))

    def test_express(self):
        self.assertEqual(express.quote(ADA, settings=Settings()), Quote("Express", "two-day", Decimal("22.50"), "USD", 2))
        eu = Settings(region="EU", currency="EUR", fuel_surcharge_pct=10)
        self.assertEqual(express.quote(ADA, settings=eu), Quote("Express", "next-day", Decimal("12.56"), "EUR", 1))

    def test_registry(self):
        self.assertEqual(carriers.names(), ["express", "ground"])
        self.assertIs(carriers.get("ground"), ground)
        with self.assertRaises(ValueError):
            carriers.get("pigeon")


if __name__ == "__main__":
    unittest.main()
