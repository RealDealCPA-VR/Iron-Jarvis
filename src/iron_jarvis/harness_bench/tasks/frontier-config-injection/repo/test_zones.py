import unittest

from shipping import settings, zones


class ZonesTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        zones.clear_cache()
        self.addCleanup(settings.reset)
        self.addCleanup(zones.clear_cache)

    def test_us_zones(self):
        self.assertEqual(zones.lookup("10115"), 8)
        self.assertEqual(zones.lookup("94105"), 1)
        self.assertEqual(zones.lookup("12345-6789"), 8)
        self.assertEqual(zones.lookup(" 60311 "), 4)

    def test_eu_zones(self):
        settings.CONFIG["region"] = "EU"
        self.assertEqual(zones.lookup("10115"), 1)
        self.assertEqual(zones.lookup("8001"), 4)
        self.assertEqual(zones.lookup("60311"), 3)

    def test_postcodes_of_another_region_are_refused(self):
        with self.assertRaises(zones.ZoneError) as caught:
            zones.lookup("8001")
        self.assertEqual(str(caught.exception), "not a postcode of region US: '8001'")
        settings.CONFIG["region"] = "EU"
        with self.assertRaises(zones.ZoneError):
            zones.lookup("12345-6789")

    def test_the_region_decides_the_zone(self):
        self.assertEqual(zones.lookup("10115"), 8)
        settings.CONFIG["region"] = "EU"
        self.assertEqual(zones.lookup("10115"), 1)
        settings.CONFIG["region"] = "US"
        self.assertEqual(zones.lookup("10115"), 8)

    def test_lookups_are_repeatable(self):
        self.assertEqual([zones.lookup("94105") for _ in range(3)], [1, 1, 1])
        zones.clear_cache()
        self.assertEqual(zones.lookup("94105"), 1)


if __name__ == "__main__":
    unittest.main()
