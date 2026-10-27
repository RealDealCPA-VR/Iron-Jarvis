import unittest

from strutil import format_bytes


class FormatBytesTest(unittest.TestCase):
    def test_small_counts_stay_in_bytes(self):
        self.assertEqual(format_bytes(512), "512 B")

    def test_decimal_units(self):
        self.assertEqual(format_bytes(2300), "2.3 kB")
        self.assertEqual(format_bytes(1234567, decimals=2), "1.23 MB")

    def test_binary_units(self):
        self.assertEqual(format_bytes(3 * 1024 * 1024, binary=True), "3.0 MiB")

    def test_negative(self):
        self.assertEqual(format_bytes(-2000), "-2.0 kB")

    def test_not_a_number(self):
        with self.assertRaises(TypeError):
            format_bytes("1kB")


if __name__ == "__main__":
    unittest.main()
