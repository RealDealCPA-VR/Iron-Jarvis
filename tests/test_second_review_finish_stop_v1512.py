"""Second review D6 (v1.512.0): Stop is honoured while the finish check waits
for a long test run (the GPT dialect's carrier, ``process_start``).

After the first 30 seconds ``FinishCheck.take`` polled ``process_read`` in
30-second slices up to its budget (120 s, or the model's own time limit up to
600 s). A Stop pressed then did nothing until the budget ended: the test
program kept running, and the run was recorded "failed: cut off by its time
limit".

Now every read is raced against the turn's Stop — the way ``stall.run_step``
learns of it (the turn's handle in ``core/turns``, found by the turn id the
check's own ``invoke`` carries). On Stop the test program is stopped
(``process_stop``, once) and the run is recorded NOT RUN, "stopped by you",
never failed. A cancelled turn stops the program too.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.harness import finish as F

TURN = "bt_finish_stop_v1512"


def _check() -> F.FinishCheck:
    fc = F.FinishCheck(engine=None, home=None, event_bus=None, session_id="build:x", folder=Path("."),
                       mark=0, request="fix it")
    fc._by_id["ij-finish-1"] = F._Run(command="npm test", args={}, source="yours")
    return fc


class SlowRun:
    """The check's own calls: ``process_read`` reports the test run still
    running until ``release`` is set; ``process_stop`` is recorded."""

    def __init__(self, turn_id: str = TURN) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.reading = asyncio.Event()
        self.release = asyncio.Event()
        self.turn_id = turn_id

    async def __call__(self, name: str, args: dict):
        self.calls.append((name, dict(args)))
        if name == "process_read":
            self.reading.set()
            await self.release.wait()
            return SimpleNamespace(ok=True, output="more output", data={"id": "p1", "running": True})
        return SimpleNamespace(ok=True, output="stopped", data={"id": "p1", "running": False})

    def stops(self) -> list[dict]:
        return [a for n, a in self.calls if n == "process_stop"]


def _started() -> SimpleNamespace:
    return SimpleNamespace(ok=True, data={"id": "p1", "running": True}, output="", error=None)


@pytest.fixture()
def handle():
    h = TURNS.register(TURN)
    yield h
    TURNS.release(TURN, h)


async def test_stop_during_a_long_test_run_stops_the_program_and_records_not_run(handle):
    fc = _check()
    run = SlowRun()
    loop = asyncio.get_running_loop()
    began = loop.time()
    taking = asyncio.ensure_future(fc.take(SimpleNamespace(id="ij-finish-1", name="process_start"), _started(),
                                           "first output", refused=False, invoke=run))
    try:
        await asyncio.wait_for(run.reading.wait(), 5)
        # Fourth plan G27 (CS6): the user's Stop names the user.
        TURNS.stop(TURN, by="you")
        # Promptly: never the rest of a 30-second slice or the 120-second budget.
        await asyncio.wait_for(asyncio.shield(taking), 5)
    finally:
        run.release.set()
    assert loop.time() - began < 5
    assert run.stops() == [{"id": "p1"}], run.calls
    rec = fc._by_id["ij-finish-1"]
    assert rec.verdict == F.TESTS_NOT_RUN, rec
    assert rec.why == F.STOPPED_BY_YOU
    assert rec.timed_out is False
    assert "first output" in rec.output


async def test_a_stop_before_the_first_read_still_stops_the_program(handle):
    fc = _check()
    run = SlowRun()
    TURNS.stop(TURN)
    try:
        await asyncio.wait_for(fc.take(SimpleNamespace(id="ij-finish-1", name="process_start"), _started(), "",
                                       refused=False, invoke=run), 5)
    finally:
        run.release.set()
    assert run.stops() == [{"id": "p1"}]
    assert fc._by_id["ij-finish-1"].verdict == F.TESTS_NOT_RUN


async def test_a_cancelled_turn_stops_the_test_program_on_its_way_out():
    fc = _check()
    run = SlowRun(turn_id="")  # no Stop handle at all (the plain lane)
    taking = asyncio.ensure_future(fc.take(SimpleNamespace(id="ij-finish-1", name="process_start"), _started(),
                                           "", refused=False, invoke=run))
    await asyncio.wait_for(run.reading.wait(), 5)
    taking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(taking, 5)
    run.release.set()
    assert run.stops() == [{"id": "p1"}], run.calls


async def test_with_no_stop_a_run_that_ends_is_read_as_before(handle):
    fc = _check()

    async def invoke(name, args):
        return SimpleNamespace(ok=True, output="2 passed", data={"id": "p1", "running": False, "exit_code": 0})

    invoke.turn_id = TURN  # type: ignore[attr-defined]
    await asyncio.wait_for(fc.take(SimpleNamespace(id="ij-finish-1", name="process_start"), _started(), "",
                                   refused=False, invoke=invoke), 5)
    rec = fc._by_id["ij-finish-1"]
    assert rec.verdict == F.TESTS_PASSED and "2 passed" in rec.output


def test_the_checks_own_invoke_names_its_turn():
    invoke = CT.finish_invoke(SimpleNamespace(), SimpleNamespace(turn_id=TURN, called_as=""), {}, lambda: set(), set())
    assert invoke.turn_id == TURN
