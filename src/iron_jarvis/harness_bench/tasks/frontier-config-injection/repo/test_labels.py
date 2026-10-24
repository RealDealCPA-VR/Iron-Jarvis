import unittest

from shipping import labels, settings, zones
from shipping.model import Shipment, make_item

ADA = Shipment("Ada Lovelace", "10115", (make_item("book", (24, 17, 3), "0.9"), make_item("mug", (12, 9, 7), "0.4")))


class LabelTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        zones.clear_cache()
        self.addCleanup(settings.reset)
        self.addCleanup(zones.clear_cache)

    def test_label(self):
        self.assertEqual(
            labels.render(ADA),
            "FROM:   Iron Shipping Co.\n"
            "TO:     Ada Lovelace\n"
            "ZONE:   8 (10115, US)\n"
            "BOX:    M, 35.0 x 25.0 x 15.0 cm\n"
            "WEIGHT: 1.70 kg",
        )

    def test_units_override_one_label(self):
        self.assertEqual(
            labels.render(ADA, units="imperial"),
            "FROM:   Iron Shipping Co.\n"
            "TO:     Ada Lovelace\n"
            "ZONE:   8 (10115, US)\n"
            "BOX:    M, 13.8 x 9.8 x 5.9 in\n"
            "WEIGHT: 3.75 lb",
        )
        self.assertEqual(settings.CONFIG["units"], "metric")
        with self.assertRaises(labels.LabelError):
            labels.render(ADA, units="furlongs")


if __name__ == "__main__":
    unittest.main()
