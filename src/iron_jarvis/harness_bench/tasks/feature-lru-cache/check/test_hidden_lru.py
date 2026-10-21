# IJ-BENCH-HIDDEN-CHECK feature-lru-cache
import unittest

from lru import LRUCache


class HiddenLRU(unittest.TestCase):
    def test_capacity_must_be_positive(self):
        with self.assertRaises(ValueError):
            LRUCache(0)
        with self.assertRaises(ValueError):
            LRUCache(-3)

    def test_default_on_miss(self):
        cache = LRUCache(1)
        self.assertIsNone(cache.get("x"))
        self.assertEqual(cache.get("x", 42), 42)

    def test_update_marks_used(self):
        cache = LRUCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)
        cache.put("c", 3)
        self.assertEqual(cache.get("a"), 10)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(len(cache), 2)

    def test_membership_does_not_mark_used(self):
        cache = LRUCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertTrue("a" in cache)
        cache.put("c", 3)
        self.assertFalse("a" in cache)
        self.assertTrue("b" in cache)

    def test_capacity_one(self):
        cache = LRUCache(1)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(len(cache), 1)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)

    def test_long_sequence(self):
        cache = LRUCache(3)
        for key in "abc":
            cache.put(key, key.upper())
        cache.get("a")
        cache.put("d", "D")
        cache.get("c")
        cache.put("e", "E")
        held = sorted(k for k in "abcde" if k in cache)
        self.assertEqual(held, ["c", "d", "e"])
