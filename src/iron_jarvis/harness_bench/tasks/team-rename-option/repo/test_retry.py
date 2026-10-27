import unittest

from syncer import TransientError, with_retries


class Flaky:
    """Fails with TransientError ``failures`` times, then answers "done"."""

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientError("try again")
        return "done"


class RetryTest(unittest.TestCase):
    def test_succeeds_after_failures(self):
        fn = Flaky(2)
        self.assertEqual(with_retries(fn, max_retries=3), "done")
        self.assertEqual(fn.calls, 3)

    def test_gives_up(self):
        fn = Flaky(5)
        with self.assertRaises(TransientError):
            with_retries(fn, max_retries=2)
        self.assertEqual(fn.calls, 3)

    def test_zero_means_one_try(self):
        fn = Flaky(1)
        with self.assertRaises(TransientError):
            with_retries(fn, max_retries=0)
        self.assertEqual(fn.calls, 1)

    def test_waits_between_tries(self):
        waits = []
        with_retries(Flaky(2), max_retries=3, delay_s=0.5, sleep=waits.append)
        self.assertEqual(waits, [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
