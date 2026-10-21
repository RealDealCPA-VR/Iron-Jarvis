# IJ-BENCH-HIDDEN-CHECK hard-regression-trap
import itertools
import unittest

from versions import compare, latest, sort_versions

ORDERED = [
    ("1.2.9", "1.2.10"), ("1.9", "1.10"), ("9.9", "10.0"), ("2.9.12", "2.10.0"),
    ("1.0.0-rc1", "1.0.0"), ("1.0.0-rc1", "1.0"), ("1.0-alpha", "1.0-beta"), ("1.0-beta", "1.0-rc1"),
    ("2.0-b", "2.0-rc"), ("1.0.0-rc2", "1.0.0-rc10"), ("v1.9", "v1.10"), ("1.9+y", "1.10+x"),
    ("1.10-rc1", "1.10"), ("1.9.1", "1.10-rc1"),
]
EQUAL = [
    ("1.2.10", "1.2.10"), ("1.0-a1", "1.0-alpha1"), ("1.0.0-rc.3", "1.0.0-rc3"), (" V2.0 ", "2.0"),
    ("1.0", "1.0.0"), ("1", "1.0.0"), ("1.0.0+build.5", "1.0.0"), ("v1.10.0", "1.10"),
]
INVALID = ["1.x", "", "1..2", "1.0-gamma", "abc", "1.2.", "v"]


class HiddenVersions(unittest.TestCase):
    def test_older_before_newer(self):
        for older, newer in ORDERED:
            with self.subTest(older=older, newer=newer):
                self.assertEqual(compare(older, newer), -1)
                self.assertEqual(compare(newer, older), 1)

    def test_equal(self):
        for a, b in EQUAL:
            with self.subTest(a=a, b=b):
                self.assertEqual(compare(a, b), 0)
                self.assertEqual(compare(b, a), 0)

    def test_invalid_versions_raise(self):
        for bad in INVALID:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    compare(bad, "1.0")
                with self.assertRaises(ValueError):
                    compare("1.0", bad)

    def test_sort_and_latest(self):
        self.assertEqual(
            sort_versions(["1.10", "1.9", "1.10-rc1", "v1.2", "1.9.1"]),
            ["v1.2", "1.9", "1.9.1", "1.10-rc1", "1.10"],
        )
        self.assertEqual(latest(["2.0-rc1", "1.99", "1.100"]), "2.0-rc1")
        with self.assertRaises(ValueError):
            latest([])

    def test_a_consistent_order(self):
        items = ["1.0-alpha", "1.0-a2", "1.0-beta", "1.0-rc2", "1.0-rc10", "1.0", "1.0.1", "1.2", "1.10", "2.0"]
        for a, b in itertools.combinations(items, 2):
            self.assertEqual(compare(a, b), -compare(b, a), (a, b))
            self.assertEqual(compare(a, b), -1, (a, b))
