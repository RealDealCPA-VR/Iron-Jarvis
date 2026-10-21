# IJ-BENCH-HIDDEN-CHECK hard-performance
import random
import time
import unittest

from ranking import count_inversions, kendall_distance, sortedness


def slow_count(items):
    items = list(items)
    return sum(1 for i in range(len(items)) for j in range(i + 1, len(items)) if items[i] > items[j])


def best_time(fn, *args, repeat=3):
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        fn(*args)
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return best


class HiddenRanking(unittest.TestCase):
    def test_same_results_as_before(self):
        rng = random.Random(42)
        cases = [[], [7], [2, 2, 2], [3, 1, 2, 3, 1], [5, 4, 3, 2, 1], [1.5, -2, 1.5, 0, -2]]
        for _ in range(60):
            n = rng.randrange(0, 40)
            cases.append([rng.randrange(-5, 6) for _ in range(n)])
        cases.append(["pear", "apple", "fig", "apple", "kiwi"])
        cases.append([(1, "b"), (1, "a"), (0, "z")])
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(count_inversions(case), slow_count(case))
        self.assertEqual(count_inversions(iter([3, 2, 1])), 3)
        reversed_big = list(range(300, 0, -1))
        self.assertEqual(count_inversions(reversed_big), 300 * 299 // 2)

    def test_the_callers_still_work(self):
        self.assertEqual(kendall_distance(["a", "b", "c", "d"], ["b", "a", "d", "c"]), 2)
        self.assertEqual(sortedness([2, 1, 3, 4]), 1 - 1 / 6)
        with self.assertRaises(ValueError):
            kendall_distance(["a"], ["b"])

    def test_it_is_fast(self):
        """Measured against this machine's own sorted() on the same data, with
        a generous floor, so a slow PC does not fail a fast algorithm."""
        rng = random.Random(7)
        data = [rng.randrange(5000) for _ in range(20000)]
        baseline = best_time(sorted, data)
        limit = max(1.5, 400 * baseline)
        start = time.perf_counter()
        got = count_inversions(data)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, limit, f"20,000 items took {elapsed:.2f} s (limit {limit:.2f} s)")
        self.assertEqual(got, _merge_count(data))


def _merge_count(items):
    """An independent O(n log n) count, to check the fast answer at size."""
    items = list(items)
    if len(items) < 2:
        return 0
    mid = len(items) // 2
    left, right = items[:mid], items[mid:]
    count = _merge_count(left) + _merge_count(right)
    left.sort()
    right.sort()
    j = 0
    for x in left:
        while j < len(right) and right[j] < x:
            j += 1
        count += j
    return count
