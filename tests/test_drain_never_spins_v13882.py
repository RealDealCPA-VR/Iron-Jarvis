"""A drain never spins on a task that finished just before it (v1.388.2).

v1.388.1's Tests gate hung once in ``test_phone_apps_agent_v1387`` (the worker
was killed at the 120 s limit); a loop of 120 runs under full-suite load hung 5
times, and every dump showed the main thread inside the phone poller's
``drain()`` — stepping, not waiting. The cause: ``while tasks: await
asyncio.gather(*list(tasks))``. A tracked task leaves its set through a done
callback the event loop runs with ``call_soon``; when every task handed to
``gather`` has already finished, Python 3.12's ``gather`` completes eagerly and
awaiting a finished future never yields, so the removal callback never runs and
the ``while`` spins forever. The daemon's shutdown awaits the same drains under
``asyncio.wait_for(..., 5)``, which cannot fire while nothing yields.

Each case below puts exactly that state into a drain's set — a finished task
whose removal callback has not run — and calls the REAL ``drain()`` in a
thread with its own event loop, so the old shape fails here in seconds instead
of hanging pytest. (A spinning thread from the old shape is a daemon thread; it
dies with the test process.)
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from iron_jarvis.core.tasks import drain_tasks


def _finished_task(loop: asyncio.AbstractEventLoop) -> asyncio.Future:
    fut = loop.create_future()
    fut.set_result(None)
    return fut


def _drains_within(make_owner, attr: str, seconds: float = 10.0) -> bool:
    """Run ``owner.drain()`` on a fresh loop in a thread, with one finished
    task (no removal callback) in ``owner.<attr>``. True when it returns."""
    done = threading.Event()

    def run() -> None:
        async def main() -> None:
            owner = make_owner()
            tasks = getattr(owner, attr)
            tasks.add(_finished_task(asyncio.get_running_loop()))
            await owner.drain()
            done.set()

        asyncio.run(main())

    threading.Thread(target=run, daemon=True, name="drain-probe").start()
    return done.wait(seconds)


def _bare(cls, attr: str):
    """The real class with only the tracked-task set: drain() reads nothing
    else."""

    def make():
        obj = object.__new__(cls)
        setattr(obj, attr, set())
        return obj

    return make


def test_the_phone_pollers_drain_returns_with_a_finished_task_still_listed():
    from iron_jarvis.comm.inbound import InboundPoller

    assert _drains_within(_bare(InboundPoller, "_session_tasks"), "_session_tasks")


def test_the_media_jobs_drain_returns_with_a_finished_task_still_listed():
    from iron_jarvis.media.jobs import MediaJobs

    assert _drains_within(_bare(MediaJobs, "_tasks"), "_tasks")


def test_the_assignment_dispatchers_drain_returns_with_a_finished_task_still_listed():
    from iron_jarvis.assignments.dispatcher import AssignmentDispatcher

    assert _drains_within(_bare(AssignmentDispatcher, "_inflight"), "_inflight")


@pytest.mark.asyncio
async def test_drain_tasks_still_waits_for_running_tasks_and_ones_added_meanwhile():
    tasks: set[asyncio.Future] = set()
    order: list[str] = []

    async def job(name: str, delay: float, then=None):
        await asyncio.sleep(delay)
        order.append(name)
        if then is not None:
            t = asyncio.create_task(then)
            tasks.add(t)
            t.add_done_callback(tasks.discard)

    first = asyncio.create_task(job("first", 0.05, then=job("added", 0.05)))
    tasks.add(first)
    first.add_done_callback(tasks.discard)
    await asyncio.wait_for(drain_tasks(tasks), timeout=10)
    assert order == ["first", "added"]
    # The removal callbacks of the finished tasks ran before it returned.
    assert tasks == set()


@pytest.mark.asyncio
async def test_drain_tasks_never_raises_for_a_tasks_own_failure():
    tasks: set[asyncio.Future] = set()

    async def boom():
        raise RuntimeError("delivery failed")

    t = asyncio.create_task(boom())
    tasks.add(t)
    t.add_done_callback(tasks.discard)
    await asyncio.wait_for(drain_tasks(tasks), timeout=10)
    assert t.done() and isinstance(t.exception(), RuntimeError)


@pytest.mark.asyncio
async def test_a_finished_tasks_queued_removal_runs_before_the_drain_returns():
    tasks: set[asyncio.Future] = set()
    fut = asyncio.get_running_loop().create_future()
    fut.add_done_callback(tasks.discard)
    tasks.add(fut)
    fut.set_result(None)  # finished; its removal is queued, not yet run
    assert fut in tasks
    await drain_tasks(tasks)
    assert tasks == set()
