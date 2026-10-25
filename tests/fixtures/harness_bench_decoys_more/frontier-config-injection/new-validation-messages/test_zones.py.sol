import unittest

from shipping import zones
from shipping.settings import Settings

US = Settings()
EU = Settings(region="EU")


class ZonesTests(unittest.TestCase):
    def test_us_zones(self):
        self.assertEqual(zones.lookup("10115", settings=US), 8)
        self.assertEqual(zones.lookup("94105", settings=US), 1)
        self.assertEqual(zones.lookup("12345-6789", settings=US), 8)
        self.assertEqual(zones.lookup(" 60311 ", settings=US), 4)

    def test_eu_zones(self):
        self.assertEqual(zones.lookup("10115", settings=EU), 1)
        self.assertEqual(zones.lookup("8001", settings=EU), 4)
        self.assertEqual(zones.lookup("60311", settings=EU), 3)

    def test_postcodes_of_another_region_are_refused(self):
        with self.assertRaises(zones.ZoneError) as caught:
            zones.lookup("8001", settings=US)
        self.assertEqual(str(caught.exception), "not a postcode of region US: '8001'")
        with self.assertRaises(zones.ZoneError):
            zones.lookup("12345-6789", settings=EU)

    def test_the_region_decides_the_zone(self):
        # No clear_cache() in between: the cache must never mix regions.
        self.assertEqual(zones.lookup("10115", settings=US), 8)
        self.assertEqual(zones.lookup("10115", settings=EU), 1)
        self.assertEqual(zones.lookup("10115", settings=US), 8)
        self.assertEqual(zones.lookup("8001", settings=EU), 4)
        with self.assertRaises(zones.ZoneError):
            zones.lookup("8001", settings=US)

    def test_lookups_are_repeatable(self):
        self.assertEqual([zones.lookup("94105", settings=US) for _ in range(3)], [1, 1, 1])
        zones.clear_cache()
        self.assertEqual(zones.lookup("94105", settings=US), 1)


if __name__ == "__main__":
    unittest.main()
