import unittest

from strutil import parse_query


class ParseQueryTest(unittest.TestCase):
    def test_simple_pairs(self):
        self.assertEqual(parse_query("a=1&b=two"), {"a": "1", "b": "two"})

    def test_a_repeated_name_collects_a_list(self):
        self.assertEqual(parse_query("?color=red&color=blue"), {"color": ["red", "blue"]})

    def test_plus_is_a_space(self):
        self.assertEqual(parse_query("q=hello+world"), {"q": "hello world"})

    def test_brackets_make_a_list(self):
        self.assertEqual(parse_query("ids[]=7"), {"ids": ["7"]})


if __name__ == "__main__":
    unittest.main()
