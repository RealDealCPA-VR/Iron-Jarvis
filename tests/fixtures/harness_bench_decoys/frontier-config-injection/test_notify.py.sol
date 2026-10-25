import unittest
from decimal import Decimal

from shipping import notify
from shipping.carriers.base import Quote
from shipping.model import Shipment, make_item
from shipping.settings import Settings

GRACE = Shipment("Grace Hopper", "94105", (
    make_item("laptop", (36, 25, 3), "2.1"),
    make_item("charger", (12, 6, 4), "0.5"),
    make_item("bag", (40, 30, 8), "0.8"),
))


class NotifyTests(unittest.TestCase):
    def test_message(self):
        quote = Quote("Express", "next-day", Decimal("19.6"), "EUR", 1)
        self.assertEqual(
            notify.message(GRACE, quote, settings=Settings()),
            "Hello Grace Hopper, Iron Shipping Co. has sent your parcel (4.20 kg) with Express"
            " (next-day). It cost USD 19.60 and should arrive in 1 day.",
        )
        nord = Settings(units="imperial", currency="EUR", sender="Paket Nord GmbH")
        self.assertEqual(
            notify.message(GRACE, quote, settings=nord),
            "Hello Grace Hopper, Paket Nord GmbH has sent your parcel (9.26 lb) with Express"
            " (next-day). It cost EUR 19.60 and should arrive in 1 day.",
        )


if __name__ == "__main__":
    unittest.main()
