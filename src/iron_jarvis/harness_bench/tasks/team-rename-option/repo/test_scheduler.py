import unittest

from syncer import Job, plan_jobs


class SchedulerTest(unittest.TestCase):
    def test_round_robin(self):
        jobs = plan_jobs(["c", "a", "b"], workers=2, max_retries=1)
        self.assertEqual(jobs, [Job("a", 1, 2), Job("b", 2, 2), Job("c", 1, 2)])

    def test_the_default_tries(self):
        self.assertEqual(plan_jobs(["a"])[0].tries, 4)

    def test_bad_workers(self):
        with self.assertRaises(ValueError):
            plan_jobs(["a"], workers=0)


if __name__ == "__main__":
    unittest.main()
