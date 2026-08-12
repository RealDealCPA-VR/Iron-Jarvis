"""Cancel reaches EVERY lane (v1.166.4).

Only background (wait:false) runs were registered as cancellable — a
schedule-fired session and every wait:true / directly-awaited lane had no
``_running`` entry, so ``cancel_session`` fell into its else-branch: the row
flipped CANCELLED, the UI said work stopped, and the agent kept calling the
model and executing tools.

``run_session`` now self-registers ``asyncio.current_task()`` when nobody else
did, and self-removes in its ``finally`` so the handle never outlives the run
(a wait:true HTTP task or a multi-step workflow task lives on after
run_session returns — a stale entry would lie to the concurrency governor and
dangle a cancellable handle at a finished run).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.core.models import AgentState, SessionStatus


class _HangingRun:
    """A runtime whose agent 'works' until cancelled — the lane under test."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self, session, agent_def):  # noqa: D102
        self.started.set()
        try:
            await asyncio.Event().wait()  # works forever unless cancelled
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _InstantRun:
    """A runtime that completes immediately with a real-shaped run record."""

    class _Rec:
        state = AgentState.COMPLETED
        provider, model, result = "mock", "m", "done"
        input_tokens = output_tokens = 0

    async def run(self, session, agent_def):  # noqa: D102
        return self._Rec()


async def test_directly_awaited_run_is_cancellable(platform, orchestrator):
    """The wait:true / schedule-fired shape: nothing registers the task —
    run_session must register itself so cancel actually stops the agent."""
    hang = _HangingRun()
    orchestrator.runtime = hang
    s = await orchestrator.create_session("direct lane")

    task = asyncio.create_task(orchestrator.run_session(s.id))  # no spawn_managed
    await hang.started.wait()
    assert s.id in orchestrator._running, "run_session must self-register"

    orchestrator.cancel_session(s.id)
    try:
        await task
        raise AssertionError("the run survived a cancel — the lying-else path")
    except asyncio.CancelledError:
        pass

    assert hang.cancelled, "the agent kept working after 'cancelled'"
    row = orchestrator.get_session(s.id)
    assert row.status is SessionStatus.CANCELLED
    assert row.finished_at is not None
    assert s.id not in orchestrator._running


async def test_self_registration_never_clobbers_spawn_managed(platform, orchestrator):
    """spawn_managed already registered this exact task — a second register
    would stack a second slot-free hook and over-promote the queue."""
    calls: list[str] = []
    real_register = orchestrator.register_running

    def counting_register(sid, task):
        calls.append(sid)
        real_register(sid, task)

    orchestrator.register_running = counting_register
    orchestrator.runtime = _InstantRun()
    s = await orchestrator.create_session("background lane")
    t = orchestrator.spawn_managed(s.id, orchestrator.run_session(s.id))
    assert t is not None
    await t
    assert calls.count(s.id) == 1, "run_session re-registered over spawn_managed"
    assert orchestrator.get_session(s.id).status is SessionStatus.COMPLETED


async def test_handle_released_when_the_run_ends_not_the_task(platform, orchestrator):
    """A long-lived caller task (workflow engine shape): after run_session
    returns, the _running entry must already be gone even though the outer
    task is still alive — else the governor counts a ghost and cancel points
    a live handle at finished work."""
    orchestrator.runtime = _InstantRun()
    s = await orchestrator.create_session("engine step")
    ran = asyncio.Event()
    hold = asyncio.Event()

    async def outer():
        await orchestrator.run_session(s.id)
        ran.set()
        await hold.wait()  # the workflow task lives on

    task = asyncio.create_task(outer())
    await ran.wait()
    assert s.id not in orchestrator._running, (
        "the self-registered handle outlived run_session"
    )
    assert not task.done()  # the outer task really is still alive
    hold.set()
    await task
    assert orchestrator.get_session(s.id).status is SessionStatus.COMPLETED
