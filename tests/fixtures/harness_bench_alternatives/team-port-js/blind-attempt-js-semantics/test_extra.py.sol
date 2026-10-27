import unittest

from strutil import format_bytes, natural_compare, natural_sort, parse_query, title_case, wrap_text


class ExtraTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(format_bytes(999999, decimals=0), "1 MB")
        self.assertEqual(format_bytes(0.1), "0.1 B")
        self.assertEqual(format_bytes(1e-7), "1e-7 B")
        self.assertEqual(format_bytes(1.5), "1.5 B")
        self.assertEqual(format_bytes(1.005e3, decimals=2), "1.00 kB")
        self.assertTrue(format_bytes(1e300).endswith("e+285 PB"))
        with self.assertRaises(ValueError):
            format_bytes(1, decimals=7)
        with self.assertRaises(TypeError):
            format_bytes(float("nan"))

    def test_query(self):
        self.assertEqual(parse_query("c=%zz&b=%E2%82%AC&a=%E2%82"), {"c": "%zz", "b": "€", "a": "%E2%82"})
        self.assertEqual(list(parse_query("b=1&2=x&1=y")), ["1", "2", "b"])
        self.assertEqual(parse_query("a=1&a[]=2"), {"a": ["1", "2"]})

    def test_natural(self):
        self.assertEqual(natural_sort(["a10", "a9", "a007", "a7"]), ["a7", "a007", "a9", "a10"])
        self.assertEqual(natural_compare("￿", "\U00010000"), 1)
        self.assertEqual(natural_compare("a", "a"), 0)

    def test_wrap(self):
        self.assertEqual(wrap_text("aaaa bbbb", 3), "aaa\na b\nbbb")
        self.assertEqual(wrap_text("\U0001F600b", 1), "\ud83d\n\ude00\nb")
        self.assertEqual(wrap_text("", 5), "")

    def test_titles(self):
        self.assertEqual(title_case("the lord of the rings: a tale of x86 vs. iPhone"),
                         "The Lord of the Rings: A Tale of x86 vs. iPhone")
        self.assertEqual(title_case("\U00010428 straße-ab"), "\U00010428 Straße-Ab")
        self.assertEqual(title_case("go to"), "Go To")


if __name__ == "__main__":
    unittest.main()
