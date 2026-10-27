import unittest

from versions import matches, parse_version


class VersionTest(unittest.TestCase):
    def test_parse(self):
        v = parse_version("1.4.2")
        self.assertEqual((v.major, v.minor, v.patch), (1, 4, 2))
        self.assertEqual(str(v), "1.4.2")

    def test_numbers_compare_as_numbers(self):
        self.assertLess(parse_version("1.9.0"), parse_version("1.10.0"))

    def test_caret(self):
        self.assertTrue(matches("1.5.0", "^1.2.3"))
        self.assertFalse(matches("2.0.0", "^1.2.3"))

    def test_malformed(self):
        with self.assertRaises(ValueError):
            parse_version("1.2")


if __name__ == "__main__":
    unittest.main()
