# IJ-BENCH-HIDDEN-CHECK hard-scheduler-ordering
import unittest

from scheduler import Scheduler, Sleep, Spawn


def steps(n):
    """A task that runs n steps (yields n - 1 times)."""
    for _ in range(n - 1):
        yield


class HiddenScheduler(unittest.TestCase):
    def test_equal_priorities_take_turns(self):
        s = Scheduler()
        s.spawn(steps(3), name="a")
        s.spawn(steps(3), name="b")
        self.assertEqual([n for _t, n in s.run()], ["a", "b", "a", "b", "a", "b"])

    def test_three_way_round_robin(self):
        s = Scheduler()
        for name in ("x", "y", "z"):
            s.spawn(steps(2), priority=4, name=name)
        self.assertEqual([n for _t, n in s.run()], ["x", "y", "z", "x", "y", "z"])

    def test_lower_number_runs_first_even_if_spawned_later(self):
        s = Scheduler()
        s.spawn(steps(2), priority=1, name="low")
        s.spawn(steps(3), priority=0, name="high")
        self.assertEqual([n for _t, n in s.run()], ["high", "high", "high", "low", "low"])

    def test_same_wake_time_keeps_sleep_order(self):
        def sleeper(n):
            yield Sleep(n)

        s = Scheduler()
        s.spawn(sleeper(5), name="x")
        s.spawn(sleeper(5), name="y")
        self.assertEqual(s.run(), [(0, "x"), (0, "y"), (5, "x"), (5, "y")])

    def test_the_clock_jumps_to_the_earliest_wake(self):
        def p():
            yield Sleep(4)

        def q():
            yield Sleep(2)

        def r():
            yield Sleep(2)
            yield Sleep(1)

        s = Scheduler()
        s.spawn(p(), name="p")
        s.spawn(q(), name="q")
        s.spawn(r(), name="r")
        self.assertEqual(s.run(), [(0, "p"), (0, "q"), (0, "r"), (2, "q"), (2, "r"), (3, "r"), (4, "p")])

    def test_the_clock_waits_while_anything_is_ready(self):
        def nap():
            yield Sleep(1)

        s = Scheduler()
        s.spawn(nap(), name="s")
        s.spawn(steps(4), name="busy")
        self.assertEqual(s.run(), [(0, "s")] + [(0, "busy")] * 4 + [(1, "s")])

    def test_spawn_inherits_priority_and_sends_back_the_id(self):
        got = []

        def parent(tag):
            got.append((yield Spawn(steps(2), name=f"kid-{tag}")))

        s = Scheduler()
        s.spawn(parent("hi"), priority=1, name="hi")
        lo_id = s.spawn(parent("lo"), priority=5, name="lo")
        self.assertEqual(
            [n for _t, n in s.run()],
            ["hi", "kid-hi", "hi", "kid-hi", "lo", "kid-lo", "lo", "kid-lo"],
        )
        self.assertEqual(got, [lo_id + 1, lo_id + 2])

    def test_spawn_with_its_own_priority(self):
        def parent():
            yield Spawn(steps(2), priority=0, name="urgent")
            yield

        s = Scheduler()
        s.spawn(parent(), priority=5, name="parent")
        self.assertEqual([n for _t, n in s.run()], ["parent", "urgent", "urgent", "parent", "parent"])

    def test_a_task_waking_joins_behind_equal_tasks(self):
        def napper():
            yield Sleep(1)
            yield

        s = Scheduler()
        s.spawn(napper(), name="n")
        s.spawn(steps(1), name="once")
        self.assertEqual(s.run(), [(0, "n"), (0, "once"), (1, "n"), (1, "n")])
