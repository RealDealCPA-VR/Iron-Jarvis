import unittest

from scheduler import Scheduler, Sleep


class SchedulerTests(unittest.TestCase):
    def test_priorities(self):
        def worker():
            yield

        s = Scheduler()
        s.spawn(worker(), priority=5, name="late")
        s.spawn(worker(), priority=1, name="early")
        self.assertEqual([name for _t, name in s.run()], ["early", "early", "late", "late"])

    def test_two_tasks_of_the_same_priority_both_run(self):
        def worker():
            yield

        s = Scheduler()
        s.spawn(worker(), name="a")
        s.spawn(worker(), name="b")
        self.assertEqual(sorted(name for _t, name in s.run()), ["a", "a", "b", "b"])

    def test_sleep_moves_the_clock(self):
        def sleeper():
            yield Sleep(3)

        s = Scheduler()
        s.spawn(sleeper(), name="z")
        self.assertEqual(s.run(), [(0, "z"), (3, "z")])


if __name__ == "__main__":
    unittest.main()
