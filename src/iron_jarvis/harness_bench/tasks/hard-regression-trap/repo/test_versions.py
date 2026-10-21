import unittest

from versions import compare, latest, sort_versions


class VersionTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(compare("1.2.3", "1.2.4"), -1)
        self.assertEqual(compare("2.0", "1.9"), 1)

    def test_bug_report_multi_digit_parts(self):
        # Reported: 1.10 is newer than 1.9, but compare says it is older.
        self.assertEqual(compare("1.10", "1.9"), 1)
        self.assertEqual(latest(["1.9", "1.10", "1.2"]), "1.10")

    def test_sort(self):
        self.assertEqual(sort_versions(["1.2", "1.1", "1.3"]), ["1.1", "1.2", "1.3"])


if __name__ == "__main__":
    unittest.main()
