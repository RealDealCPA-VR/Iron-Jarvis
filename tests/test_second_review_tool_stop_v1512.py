"""v1.512.0 — second review, group 12 part A: in the service that runs other
coding tools (``hosted/service.py``) Stop must stop, and a stop never races a
start.

H1  a tool that does not honour its own interrupt is closed by a second Stop,
    by "Close it now", or by itself after ``STOP_FORCE_AFTER_S``.
H2  every stop of a session's process runs under the session's lock; the
    reaper and the pool skip a session being started; a claim is refused
    while a start is in flight; a start that completes for a session that
    went (or after shutdown began) stops what it started.
H8  a Stop that lands while the turn is ENDING never lets the message out.
The open shutdown item: a start held in flight while ``shutdown`` runs is
not left running.

NO REAL CODING TOOL EVER STARTS HERE: the service's process class and driver
are stand-ins (``_Rig``) and the program path does not exist. Waits wait for
the thing asserted; time is the service's injected clock.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Driver
from tests._build_app_helpers_v1409 import until


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _short_stop_answer(monkeypatch):
    # The stand-in never ends a reply: the Stop route's answer waits its whole
    # bound for that end. A short bound keeps the suite quick; the second-Stop
    # grace reads the same constant on the injected clock.
    monkeypatch.setattr(S, "INTERRUPT_WAIT_S", 0.2)


class _Driver(Driver):
    """A coding tool's driver that hears every message and NEVER ends a
    reply by itself — its interrupt is heard and ignored (the wedged tool)."""

    harness = "codex"

    def __init__(self) -> None:
        async def _write(obj: Any) -> None:
            return None

        super().__init__(write=_write, emit=lambda ev: None, ask=lambda a: None,
                         on_session_id=lambda s: None, mode="ask")
        self.sent: list[str] = []
        self.interrupts = 0

    def argv(self, *, session_id: str = "", resume: bool = False) -> list[str]:
        return []

    async def open(self, *, session_id: str = "", resume: bool = False) -> None:
        return None

    async def send_user(self, text: str, files: Any) -> None:
        self.sent.append(text)

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def answer(self, *a: Any, **k: Any) -> None:
        return None

    def on_exit(self) -> None:
        return None

    def on_message(self, obj: Any) -> None:
        return None


class _Rig:
    """Stand-ins for the tool's process and driver. ``start_gate`` /
    ``stop_gate`` hold a start or a stop on its thread hop until set."""

    def __init__(self) -> None:
        self.procs: list[Any] = []
        self.drivers: list[_Driver] = []
        self.start_gate = threading.Event()
        self.start_gate.set()
        self.stop_gate = threading.Event()
        self.stop_gate.set()
        self.in_start = threading.Event()
        self.in_stop = threading.Event()

    def open_all(self) -> None:
        self.start_gate.set()
        self.stop_gate.set()

    def alive(self) -> list[Any]:
        return [p for p in self.procs if p.alive]


def _rig(svc: S.HostedService, monkeypatch, tmp_path: Path) -> _Rig:
    rig = _Rig()

    class _Proc:
        def __init__(self, argv: Any, env: Any = None, cwd: Any = None, name: str = "",
                     ends_left_behind: bool = False) -> None:
            self.alive = False
            self.stopped = False
            rig.procs.append(self)

        def start(self, loop: Any, on_message: Any, on_exit: Any) -> None:
            rig.in_start.set()
            rig.start_gate.wait(20)
            self.alive = True

        def stop(self) -> None:
            rig.in_stop.set()
            rig.stop_gate.wait(20)
            self.alive = False
            self.stopped = True

        def write(self, obj: Any) -> None:
            return None

        def stderr_text(self) -> str:
            return ""

    def new_driver(live: Any, row: Any, write: Any, **kw: Any) -> _Driver:
        d = _Driver()
        rig.drivers.append(d)
        return d

    monkeypatch.setattr(S, "HarnessProcess", _Proc)
    # A path that does not exist: never a real program (tests/_real_tool_guard.py).
    monkeypatch.setattr(S, "binary_for", lambda harness: [str(tmp_path / "no-such-coding-tool.exe")])
    monkeypatch.setattr(svc, "_new_driver", new_driver)
    return rig


def _clock(svc: S.HostedService) -> list[float]:
    t = [1000.0]
    svc.clock = lambda: t[0]
    return t


def _session(app, work: Path) -> Any:
    return store.create(app.state.platform.engine, harness="codex", folder=str(work))


async def _sent(svc: S.HostedService, rig: _Rig, sid: str, text: str = "write the report",
                tid: str = "t1") -> S.HostedTurn:
    """A message the tool has received (its reply is running)."""
    n = len(rig.drivers)
    turn = await svc.start_turn(sid, text, tid)
    await until(lambda: len(rig.drivers) > n and rig.drivers[-1].sent)
    assert turn.running and not turn.unsent
    return turn


def _notices(turn: S.HostedTurn) -> list[str]:
    return [str(e.get("text") or "") for e in turn.events if e.get("type") == "notice"]


def _lock_waited(live: S.LiveSession) -> bool:
    waiters = getattr(live.lock, "_waiters", None) or ()
    return any(not w.done() for w in waiters)


# --------------------------------------------------------------------------- #
# H1: a Stop the tool does not honour
# --------------------------------------------------------------------------- #
async def test_h1_a_second_stop_closes_a_tool_that_ignored_the_first(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    t = _clock(svc)
    row = _session(app, work)
    try:
        turn = await _sent(svc, rig, row.id)
        first = await svc.interrupt(row.id)
        assert rig.drivers[0].interrupts == 1
        assert turn.running and rig.procs[0].alive, "the stand-in ended the reply by itself"
        t[0] += S.INTERRUPT_WAIT_S
        second = await svc.interrupt(row.id)
        assert rig.procs[0].stopped, "a second Stop left a tool that ignored the first running"
        assert turn.status == "interrupted"
        said = W.STOP_NOT_HONOURED.format(label=W.label("codex"))
        assert _notices(turn).count(said) == 1, _notices(turn)
        assert W.STOPPED_PLAIN not in _notices(turn)
        assert first["stop_pending"] is True and first["words"] == W.STOP_ASKED.format(label="Codex")
        assert second.get("closed") is True
        # ...and the session is a stopped session: Archive works.
        await svc.archive(row.id)
        assert (await asyncio.to_thread(store.get, app.state.platform.engine, row.id)).archived_at is not None
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h1_a_quick_second_press_is_the_same_stop(app, work, tmp_path, monkeypatch):
    # A double click is not a second Stop: the tool gets its INTERRUPT_WAIT_S.
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    t = _clock(svc)
    row = _session(app, work)
    try:
        turn = await _sent(svc, rig, row.id)
        await svc.interrupt(row.id)
        t[0] += S.INTERRUPT_WAIT_S - 0.5
        again = await svc.interrupt(row.id)
        assert again.get("stop_pending") is True
        assert rig.procs[0].alive and turn.running, "a double click on Stop closed the tool"
        assert rig.drivers[0].interrupts == 1
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h1_close_it_now_closes_a_tool_whose_stop_is_pending(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        turn = await _sent(svc, rig, row.id)
        await svc.interrupt(row.id)
        out = await svc.close_tool(row.id)
        assert out["closed"] is True
        assert rig.procs[0].stopped and turn.status == "interrupted"
        assert W.STOP_NOT_HONOURED.format(label="Codex") in _notices(turn)
        # The next message carries on, on a process of its own.
        await _sent(svc, rig, row.id, "carry on", "t2")
        assert len(rig.procs) == 2 and rig.procs[1].alive
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h1_the_time_bound_closes_it_at_the_bound_and_not_before(app, work, tmp_path, monkeypatch):
    assert 30.0 <= S.STOP_FORCE_AFTER_S <= 120.0, "a tool winding down gracefully must not be cut off"
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    t = _clock(svc)
    row = _session(app, work)
    try:
        turn = await _sent(svc, rig, row.id)
        asked = t[0]
        await svc.interrupt(row.id)
        assert await svc.close_overdue_stops(now=asked + S.STOP_FORCE_AFTER_S - 0.5) == []
        assert rig.procs[0].alive and turn.running
        assert await svc.close_overdue_stops(now=asked + S.STOP_FORCE_AFTER_S) == [row.id]
        assert rig.procs[0].stopped and turn.status == "interrupted"
        assert W.STOP_NOT_HONOURED.format(label="Codex") in _notices(turn)
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h1_a_pending_stop_is_watched_by_itself(app, work, tmp_path, monkeypatch):
    # Nobody presses anything again: the Stop's own watcher closes the tool
    # once the service's clock passes the bound.
    monkeypatch.setattr(S, "STOP_RECHECK_S", 0.05)
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    t = _clock(svc)
    row = _session(app, work)
    try:
        turn = await _sent(svc, rig, row.id)
        await svc.interrupt(row.id)
        assert rig.procs[0].alive
        t[0] += S.STOP_FORCE_AFTER_S
        await until(lambda: rig.procs[0].stopped, timeout=10)
        await until(lambda: not turn.running, timeout=10)
        assert turn.status == "interrupted"
    finally:
        rig.open_all()
        await svc.shutdown()


# --------------------------------------------------------------------------- #
# H2: a stop is one at a time with a start
# --------------------------------------------------------------------------- #
async def test_h2_a_message_during_an_idle_stop_waits_for_it(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        first = await _sent(svc, rig, row.id)
        live = svc.live[row.id]
        svc._emit(live, S.make("done", stop="interrupted"))
        await until(lambda: not first.running and live.turn is None)
        late = max(live.last_active, live.last_heard) + S.IDLE_S + 1
        rig.stop_gate.clear()
        reap = asyncio.ensure_future(svc.reap_once(now=late))
        await until(rig.in_stop.is_set)
        second = await svc.start_turn(row.id, "one more thing", "t2")
        await until(lambda: len(rig.procs) >= 2 or _lock_waited(live))
        assert len(rig.procs) == 1, "a second process started while the first was still stopping"
        rig.stop_gate.set()
        assert await reap == [row.id]
        await until(lambda: len(rig.drivers) == 2 and rig.drivers[1].sent)
        assert second.running, f"the old stop ended the new message ({second.status})"
        assert rig.procs[1].alive and not rig.procs[1].stopped
        assert live.proc is rig.procs[1]
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h2_delete_during_a_start_leaves_nothing_running(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        rig.start_gate.clear()
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(rig.in_start.is_set)
        await svc.interrupt(row.id)  # Stop while the tool starts: the turn ends at once
        assert not turn.running
        refused = None
        try:
            await svc.delete(row.id)
        except S.Refused as exc:
            refused = exc
        if refused is not None:
            assert refused.sentence == W.RUNNING
        rig.start_gate.set()
        await until(lambda: turn.task.done())
        if refused is not None:
            await svc.delete(row.id)  # once the start settled, the delete goes through
        assert rig.alive() == [], "a coding tool runs that no session tracks"
        assert row.id not in svc.live
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h2_a_delete_before_the_start_takes_the_lock_starts_nothing(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    entered, go = asyncio.Event(), asyncio.Event()
    real = PN.record_turn_start_async

    async def held(*a: Any, **k: Any) -> Any:
        entered.set()
        await go.wait()
        return await real(*a, **k)

    monkeypatch.setattr(PN, "record_turn_start_async", held)
    try:
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(entered.is_set)
        await svc.interrupt(row.id)
        await svc.delete(row.id)
        go.set()
        await until(lambda: turn.task.done())
        assert rig.procs == [], "a deleted session's tool was started"
        assert row.id not in svc.live
    finally:
        go.set()
        rig.open_all()
        await svc.shutdown()


async def test_h2_a_message_stopped_before_its_start_took_the_lock_says_no_start(app, work, tmp_path,
                                                                                  monkeypatch):
    # Looked at again once the lock is held: no "starting" line for a
    # message that will never start, and nothing started.
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    entered, go = asyncio.Event(), asyncio.Event()
    real = PN.record_turn_start_async

    async def held(*a: Any, **k: Any) -> Any:
        entered.set()
        await go.wait()
        return await real(*a, **k)

    monkeypatch.setattr(PN, "record_turn_start_async", held)
    try:
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(entered.is_set)
        await svc.interrupt(row.id)
        go.set()
        await until(lambda: turn.task.done())
        live = svc.live[row.id]
        events, _total, _dropped = await asyncio.to_thread(live.log.read, 200)
        start_words = W.start_notice("codex", why="", resumed=False, idle_minutes=int(S.IDLE_S // 60))
        assert not any(e.get("type") == "notice" and e.get("text") == start_words for e in events), \
            "a start was announced for a message Stop had already ended"
        assert rig.procs == []
    finally:
        go.set()
        rig.open_all()
        await svc.shutdown()


async def test_h2_a_start_that_ends_after_its_session_went_stops_what_it_started(app, work, tmp_path,
                                                                                 monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        rig.start_gate.clear()
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(rig.in_start.is_set)
        live = svc.live.pop(row.id)  # the session left the table while its tool started
        rig.start_gate.set()
        await until(lambda: turn.task.done())
        assert rig.procs and rig.alive() == [], "a tool started for a session nothing tracks"
        assert live.proc is None and rig.drivers[0].sent == []
    finally:
        rig.open_all()
        await svc.shutdown()


@pytest.mark.parametrize("how", ["lock", "starting", "opening"])
async def test_h2_the_reaper_skips_a_session_being_started_or_stopped(app, work, tmp_path, monkeypatch, how):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        turn = await _sent(svc, rig, row.id)
        live = svc.live[row.id]
        svc._emit(live, S.make("done", stop="interrupted"))
        await until(lambda: not turn.running and live.turn is None)
        late = max(live.last_active, live.last_heard) + S.IDLE_S + 1
        if how == "lock":
            await live.lock.acquire()
        else:
            setattr(live, how, True)
        try:
            got = await asyncio.wait_for(svc.reap_once(now=late), 10)
            assert got == [] and rig.procs[0].alive, f"the reaper stopped a session ({how})"
        finally:
            if how == "lock":
                live.lock.release()
            else:
                setattr(live, how, False)
        assert await svc.reap_once(now=late) == [row.id]  # free again: it is reaped
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_h2_the_pool_never_picks_a_victim_whose_lock_is_held(app, work):
    svc = S.service_for(app.state.platform)
    live = svc._live(_session(app, work))

    class _Up:
        alive = True
        stopped = False

    live.proc = _Up()
    assert S._stoppable(live, svc.clock())
    await live.lock.acquire()
    try:
        assert not S._stoppable(live, svc.clock())
    finally:
        live.lock.release()
    live.opening = True
    assert not S._stoppable(live, svc.clock())
    live.opening = False
    live.proc = None


async def test_h2_a_claim_is_refused_while_a_start_is_in_flight(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        rig.start_gate.clear()
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(rig.in_start.is_set)
        await svc.interrupt(row.id)
        with pytest.raises(S.Refused) as got:
            await svc.archive(row.id)
        assert got.value.sentence == W.RUNNING
        assert svc.live[row.id].claim == ""
        rig.start_gate.set()
        await until(lambda: turn.task.done())
    finally:
        rig.open_all()
        await svc.shutdown()


# --------------------------------------------------------------------------- #
# H8: a Stop while the turn is ending
# --------------------------------------------------------------------------- #
async def test_h8_a_stop_while_the_turn_is_ending_never_sends_the_message(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    entered, held = asyncio.Event(), asyncio.Event()

    async def slow_turn_ended(svc_: Any, live: Any, turn: Any, stop: str) -> None:
        entered.set()
        await held.wait()

    try:
        rig.start_gate.clear()
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(rig.in_start.is_set)
        monkeypatch.setattr(CO, "turn_ended", slow_turn_ended)
        stop = asyncio.ensure_future(svc.interrupt(row.id))
        await until(entered.is_set)
        assert turn.ending and turn.running  # ``_end_turn`` is mid-way
        rig.start_gate.set()
        await until(lambda: bool(rig.drivers and rig.drivers[0].sent) or turn.task.done())
        assert rig.drivers[0].sent == [], "the message went to the tool after Stop"
        held.set()
        await stop
        assert turn.status == "interrupted"
    finally:
        held.set()
        rig.open_all()
        await svc.shutdown()


# --------------------------------------------------------------------------- #
# The open shutdown item
# --------------------------------------------------------------------------- #
async def test_a_start_in_flight_at_shutdown_is_not_left_running(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    try:
        rig.start_gate.clear()
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(rig.in_start.is_set)
        live = svc.live[row.id]
        closing = asyncio.ensure_future(svc.shutdown())
        # The start is let go only once shutdown WAITS for it (it holds the
        # session's lock); a shutdown that does not wait is over first.
        await until(lambda: _lock_waited(live) or closing.done())
        rig.start_gate.set()
        await closing
        # Nothing still starting once shutdown returned (the loop may end now).
        assert not svc._opening and not svc.live[row.id].lock.locked(), "shutdown left a start in flight"
        assert rig.alive() == [], "a coding tool outlived the shutdown"
        await until(lambda: turn.task.done())
        assert rig.procs and rig.alive() == [], "a coding tool outlived the shutdown"
        assert svc.live[row.id].proc is None
        assert rig.drivers[0].sent == []
    finally:
        rig.open_all()
        await svc.shutdown()


async def test_no_start_begins_once_shutdown_began(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    rig = _rig(svc, monkeypatch, tmp_path)
    _clock(svc)
    row = _session(app, work)
    entered, go = asyncio.Event(), asyncio.Event()
    real = PN.record_turn_start_async

    async def held(*a: Any, **k: Any) -> Any:
        entered.set()
        await go.wait()
        return await real(*a, **k)

    monkeypatch.setattr(PN, "record_turn_start_async", held)
    try:
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(entered.is_set)
        svc._closing = True  # what ``shutdown`` sets first
        go.set()
        await until(lambda: turn.task.done())
        assert rig.procs == [], "a start began after shutdown began"
        assert turn.status == "interrupted"
    finally:
        go.set()
        rig.open_all()
        await svc.shutdown()
