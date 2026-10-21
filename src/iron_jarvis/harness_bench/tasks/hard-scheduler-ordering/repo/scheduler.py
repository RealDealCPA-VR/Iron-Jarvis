"""A tiny cooperative scheduler.

A task is a generator. Each value it YIELDS is a request:

    yield                         give up the CPU and run again later
    yield Sleep(n)                sleep for n ticks (n >= 1)
    yield Spawn(gen)              start ``gen`` as a new task with THIS task's priority
    yield Spawn(gen, priority=p)  ... or with priority p (and ``name=`` to name it)

The value sent back into the generator is the new task's id after a Spawn,
and None after anything else.

THE ORDER this scheduler promises:

1. A ready task with a LOWER priority number always runs before a ready task
   with a higher number.
2. Among ready tasks of the SAME priority, the one that became ready EARLIEST
   runs first. A task that yields becomes ready again at that moment, so
   equal-priority tasks take turns (round robin). After a Spawn, the new task
   becomes ready first and then the spawning task becomes ready again.
3. The clock starts at 0 and moves only when no task is ready: it jumps to the
   earliest time a sleeping task wakes. ``Sleep(n)`` asked at time t wakes at
   t + n. Tasks that wake at the same time become ready in the order they
   went to sleep.

``run()`` runs until every task has finished and returns the LOG: a list of
``(time, task name)`` with one entry for every step a task ran, in order.
"""

import heapq


class Sleep:
    def __init__(self, ticks):
        if ticks < 1:
            raise ValueError("a task must sleep at least one tick")
        self.ticks = ticks


class Spawn:
    def __init__(self, gen, priority=None, name=None):
        self.gen = gen
        self.priority = priority
        self.name = name


class Task:
    _next_id = 0

    def __init__(self, gen, priority, name):
        Task._next_id += 1
        self.id = Task._next_id
        self.gen = gen
        self.priority = priority
        self.name = name or f"task{self.id}"
        self.send_value = None


class Scheduler:
    def __init__(self):
        self.ready = []  # a heap of (priority, task)
        self.sleeping = {}  # wake time -> [tasks]
        self.now = 0
        self.log = []

    def spawn(self, gen, priority=0, name=None):
        """Start a task; returns its id."""
        task = Task(gen, priority, name)
        self._make_ready(task)
        return task.id

    def _make_ready(self, task):
        heapq.heappush(self.ready, (task.priority, task))

    def run(self):
        while self.ready or self.sleeping:
            if not self.ready:
                self.now = min(self.sleeping)
                for task in reversed(self.sleeping.pop(self.now)):
                    self._make_ready(task)
            _, task = heapq.heappop(self.ready)
            self.log.append((self.now, task.name))
            try:
                request = task.gen.send(task.send_value)
            except StopIteration:
                continue
            task.send_value = None
            if request is None:
                self._make_ready(task)
            elif isinstance(request, Sleep):
                self.sleeping.setdefault(self.now + request.ticks, []).append(task)
            elif isinstance(request, Spawn):
                priority = 0 if request.priority is None else request.priority
                task.send_value = self.spawn(request.gen, priority, request.name)
                self._make_ready(task)
            else:
                raise TypeError(f"a task yielded {request!r}")
        return self.log
