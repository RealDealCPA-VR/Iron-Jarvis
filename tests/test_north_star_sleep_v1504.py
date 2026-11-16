"""v1.504.0 (north-star 4.11): the PC sleeping is not a tool's failure.

What really happens (read, not guessed): asyncio's clock is ``time.monotonic``,
which on Windows with this Python (3.12) is ``GetTickCount64()``
(``time.get_clock_info("monotonic").implementation``), and Microsoft documents
GetTickCount64 as the time since the system STARTED, pointing to
``QueryUnbiasedInterruptTime`` for the time it has been awake. So the loop's
clock runs on while the PC sleeps: a call that had worked for seconds when the
lid closed was, on waking, past its 600 s deadline and ledgered "did not finish
within 600 s — it was stopped".

The deadline now counts only time AWAKE (``registry._AWAKE_CLOCK``: the
unbiased interrupt time on Windows, ``time.monotonic`` elsewhere, where it
already leaves suspend out): when the loop's timer fires, the call's awake time
is read, and a deadline not really reached is set again for what is left.

Both clocks are INJECTED here: the loop's own clock is moved forward an hour
(the sleep) while the awake clock moves one second. Never a sleep.
"""

from __future__ import annotations

import asyncio

import pytest

import iron_jarvis.tools.registry as registry_mod
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.tools.permissions import PermissionEngine


class _LongCall(Tool):
    name = "long_call_v1504"
    description = "fixture"
    reversibility = Reversibility.IRREVERSIBLE
    input_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = False  # the deadline cut it off

    async def execute(self, args, ctx):
        # The registry's deadline scope, as ``mcp/client._call_read_timeout``
        # reads it: it must hold a real deadline on the loop's clock.
        self.scope = registry_mod._DEADLINE_SCOPE.get()
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.stopped = True
            raise
        return ToolResult(ok=True, output="finished its work")


class _Clocks:
    """The loop's clock (counts sleep, like GetTickCount64) and the awake
    clock (does not), both moved by the test."""

    def __init__(self, monkeypatch) -> None:
        self.loop = asyncio.get_running_loop()
        self._real = self.loop.time
        self.loop_offset = 0.0
        self.awake = 0.0
        self.loop.time = lambda: self._real() + self.loop_offset  # type: ignore[method-assign]
        monkeypatch.setattr(registry_mod, "_AWAKE_CLOCK", lambda: self.awake)

    def restore(self) -> None:
        del self.loop.time

    def asleep(self, seconds: float) -> None:
        self.loop_offset += seconds

    def awake_for(self, seconds: float) -> None:
        self.loop_offset += seconds
        self.awake += seconds


async def _turns(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def _setup(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    tool = _LongCall()
    p.registry.register(tool)
    perms = PermissionEngine({**p.config.permissions, tool.name: "allow"})
    ctx = ToolContext(
        workspace=tmp_path, session_id="s-1504-sleep", agent_run_id="r-1504",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    return p, tool, perms, ctx


@pytest.mark.asyncio
async def test_an_hour_asleep_is_not_counted_against_the_deadline(tmp_path, monkeypatch):
    p, tool, perms, ctx = _setup(tmp_path)
    clocks = _Clocks(monkeypatch)
    try:
        task = asyncio.create_task(p.registry.invoke(tool.name, {}, ctx, perms, deadline_s=600))
        await asyncio.wait_for(tool.started.wait(), 5)
        loop = asyncio.get_running_loop()
        # The MCP client sizes its read timeout from this (v1.322.0).
        assert tool.scope.when() is not None
        assert 599 <= tool.scope.when() - loop.time() <= 600.5
        clocks.awake_for(5)  # five seconds of real work...
        clocks.asleep(3600)  # ...then the lid closes for an hour
        await _turns()
        assert not tool.stopped and not task.done(), "the PC's sleep was ledgered as the tool's failure"
        # What is left (595 s awake) is the scope's deadline again.
        assert 594 <= tool.scope.when() - loop.time() <= 595.5
        clocks.awake_for(30)  # it wakes and finishes its work
        tool.release.set()
        res = await asyncio.wait_for(task, 5)
        assert res.ok and res.output == "finished its work"
    finally:
        clocks.restore()


@pytest.mark.asyncio
async def test_the_deadline_still_stops_a_call_that_was_awake_that_long(tmp_path, monkeypatch):
    p, tool, perms, ctx = _setup(tmp_path)
    clocks = _Clocks(monkeypatch)
    try:
        task = asyncio.create_task(p.registry.invoke(tool.name, {}, ctx, perms, deadline_s=600))
        await asyncio.wait_for(tool.started.wait(), 5)
        clocks.asleep(3600)
        await _turns()
        assert not tool.stopped and not task.done()
        clocks.awake_for(601)  # now it has really been working past its deadline
        res = await asyncio.wait_for(task, 5)
        assert res.ok is False
        assert res.error.startswith(f"{tool.name} did not finish within 600 s")
    finally:
        clocks.restore()


@pytest.mark.asyncio
async def test_time_asleep_before_the_deadline_extends_it_by_that_much(tmp_path, monkeypatch):
    """Awake 500 s, asleep an hour, then awake 99 s: 599 s of work, still in
    time; one more second awake and it is past."""
    p, tool, perms, ctx = _setup(tmp_path)
    clocks = _Clocks(monkeypatch)
    try:
        task = asyncio.create_task(p.registry.invoke(tool.name, {}, ctx, perms, deadline_s=600))
        await asyncio.wait_for(tool.started.wait(), 5)
        clocks.awake_for(500)
        await _turns()
        clocks.asleep(3600)
        await _turns()
        clocks.awake_for(99)
        await _turns()
        assert not tool.stopped and not task.done()
        clocks.awake_for(2)
        res = await asyncio.wait_for(task, 5)
        assert res.ok is False and "did not finish within 600 s" in res.error
    finally:
        clocks.restore()


def test_the_awake_clock_on_this_platform_is_a_working_clock():
    clock = registry_mod._awake_clock()
    a = clock()
    b = clock()
    assert isinstance(a, float) and b >= a
