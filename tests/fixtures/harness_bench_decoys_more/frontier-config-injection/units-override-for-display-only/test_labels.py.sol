import unittest

from shipping import labels
from shipping.model import Shipment, make_item
from shipping.settings import Settings

ADA = Shipment("Ada Lovelace", "10115", (make_item("book", (24, 17, 3), "0.9"), make_item("mug", (12, 9, 7), "0.4")))
METRIC_LABEL = (
    "FROM:   Iron Shipping Co.\n"
    "TO:     Ada Lovelace\n"
    "ZONE:   8 (10115, US)\n"
    "BOX:    M, 35.0 x 25.0 x 15.0 cm\n"
    "WEIGHT: 1.70 kg"
)


class LabelTests(unittest.TestCase):
    def test_label(self):
        self.assertEqual(labels.render(ADA, settings=Settings()), METRIC_LABEL)

    def test_units_override_one_label(self):
        settings = Settings()
        self.assertEqual(
            labels.render(ADA, units="imperial", settings=settings),
            "FROM:   Iron Shipping Co.\n"
            "TO:     Ada Lovelace\n"
            "ZONE:   8 (10115, US)\n"
            "BOX:    M, 13.8 x 9.8 x 5.9 in\n"
            "WEIGHT: 3.75 lb",
        )
        self.assertEqual(settings.units, "metric")
        with self.assertRaises(labels.LabelError):
            labels.render(ADA, units="furlongs", settings=settings)
        with self.assertRaises(labels.LabelError):
            labels.render(Shipment(" ", "10115", ADA.items), units="imperial", settings=settings)
        self.assertEqual(labels.render(ADA, settings=settings), METRIC_LABEL)


if __name__ == "__main__":
    unittest.main()
