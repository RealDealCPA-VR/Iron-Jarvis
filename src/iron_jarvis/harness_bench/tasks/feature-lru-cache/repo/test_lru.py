import unittest

from lru import LRUCache


class LRUTests(unittest.TestCase):
    def test_docstring_example(self):
        cache = LRUCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)
        cache.put("c", 3)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(len(cache), 2)


if __name__ == "__main__":
    unittest.main()
