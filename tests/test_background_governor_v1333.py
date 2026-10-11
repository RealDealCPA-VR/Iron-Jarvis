"""The background-run governor and the "busy" notice (v1.333.0, Lane G).

The user's decision: no cap by default, but say so when an unusual number of
background runs are going at once and ASK whether to start a queue.

* ``Orchestrator.run_session`` is the ONE place every root run passes, so it
  marks an unattended run (origin not in ``runtime.ATTENDED_ORIGINS``) as
  background work (``core.procs.background_lane``), counts it, and rings
  ``runs.busy`` once per episode past ``busy_runs_notice_at``;
* with a cap set it HOLDS the cap for the doors that never went through
  ``spawn_managed`` (schedule fires on APScheduler's thread, goals, workflow
  agent steps, the phone) without ever parking a run behind its own parent;
* the assignment dispatcher waits a minute, not 5 s, when it has nothing.

Offline: ``runtime.run`` is replaced by a fake whose length the test controls,
so every assertion counts runs, rows and events, never seconds.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import BUSY_SNOOZE_S, Orchestrator
from iron_jarvis.assignments.dispatcher import AssignmentDispatcher
from iron_jarvis.core import procs
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentState, AgentType, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.engine import WorkflowEngine, load_workflow

#: How long a test may wait for something that must happen (liveness only —
#: never asserted as a speed).
LIVENESS_S = 30.0


def _set_limit(platform, n: int) -> None:
    platform.config.max_concurrent_sessions = n


def _done_run(session) -> SimpleNamespace:
    return SimpleNamespace(
        id=None,
        state=AgentState.COMPLETED,
        provider="mock",
        model="mock-1",
        result=f"done: {session.task}",
        input_tokens=0,
        output_tokens=0,
    )


class _Gates:
    """A fake ``runtime.run`` whose runs each hold until the test opens
    their gate; records which runs started and what lane they saw."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.lane: dict[str, tuple[bool, bool]] = {}
        self._gates: dict[str, asyncio.Event] = {}
        self.hold = True

    def gate(self, sid: str) -> asyncio.Event:
        return self._gates.setdefault(sid, asyncio.Event())

    async def run(self, session, agent_def):
        self.started.append(session.id)
        # The lane inline AND one thread hop away (a spawn site may run in a
        # worker thread: asyncio.to_thread copies the context there).
        in_thread = await asyncio.to_thread(procs.in_background_lane)
        self.lane[session.id] = (procs.in_background_lane(), in_thread)
        if self.hold:
            await self.gate(session.id).wait()
        return _done_run(session)


async def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + LIVENESS_S
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"never happened: {what}")
        await asyncio.sleep(0.01)


def _busy_events(platform) -> list[dict]:
    return [e.payload for e in platform.event_bus.history if e.type == EventType.RUNS_BUSY]


@pytest.fixture
def orch(platform):
    o = Orchestrator(platform)
    platform.orchestrator = o
    return o


# --------------------------------------------------------------------------- #
# 1. the background lane
# --------------------------------------------------------------------------- #


async def test_a_schedule_run_is_background_work_and_a_chat_or_job_run_is_not(platform, orch):
    fake = _Gates()
    fake.hold = False
    orch.runtime.run = fake.run
    sched = await orch.create_session("nightly report", AgentType.BUILDER, origin="schedule:nightly")
    chat = await orch.create_session("from chat", AgentType.BUILDER, origin="chat")
    job = await orch.create_session("pressed Run", AgentType.BUILDER, origin="job")
    for s in (sched, chat, job):
        done = await orch.run_session(s.id)
        assert done.status is SessionStatus.COMPLETED
    assert fake.lane[sched.id] == (True, True)  # inline and in a worker thread
    assert fake.lane[chat.id] == (False, False)
    assert fake.lane[job.id] == (False, False)
    # The mark never leaks out of the run that set it.
    assert procs.in_background_lane() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows priority classes")
async def test_a_child_started_inside_a_schedule_run_gets_below_normal_priority(platform, orch):
    flags: dict[str, int] = {}

    async def run(session, agent_def):
        flags[session.origin] = procs.priority_flags()
        return _done_run(session)

    orch.runtime.run = run
    for origin in ("schedule:nightly", "chat"):
        s = await orch.create_session("t", AgentType.BUILDER, origin=origin)
        await orch.run_session(s.id)
    assert flags["schedule:nightly"] & procs.BELOW_NORMAL_PRIORITY_CLASS
    assert not flags["chat"] & procs.BELOW_NORMAL_PRIORITY_CLASS


# --------------------------------------------------------------------------- #
# 2. one live count across every door
# --------------------------------------------------------------------------- #


async def test_every_unattended_door_is_counted_and_attended_runs_are_not(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    origins = [
        "schedule:a", "goal:g1", "workflow:w", "reflex:r", "comm:phone",
        "assignment:x", "autonomy", "chat", "job:mission",
    ]
    sessions = [
        await orch.create_session(f"t{i}", AgentType.BUILDER, origin=o)
        for i, o in enumerate(origins)
    ]
    # A fresh threshold so the count itself is what is under test here.
    platform.config.busy_runs_notice_at = 0
    tasks = [asyncio.create_task(orch.run_session(s.id)) for s in sessions]
    await _until(lambda: len(fake.started) == len(sessions), "every run started")
    assert orch.unattended_running() == 7  # the seven unattended doors, not chat/job
    for s in sessions:
        fake.gate(s.id).set()
    await asyncio.gather(*tasks)
    assert orch.unattended_running() == 0


# --------------------------------------------------------------------------- #
# 3. the notice
# --------------------------------------------------------------------------- #


async def _start_n(orch, fake, n: int, origin: str = "schedule:s") -> list[tuple[str, asyncio.Task]]:
    out = []
    for i in range(n):
        s = await orch.create_session(f"bg {i}", AgentType.BUILDER, origin=origin)
        out.append((s.id, asyncio.create_task(orch.run_session(s.id))))
        await _until(lambda sid=s.id: sid in fake.started, "the run started")
    return out


async def _finish(fake, runs) -> None:
    for sid, _t in runs:
        fake.gate(sid).set()
    await asyncio.gather(*(t for _sid, t in runs))


async def test_more_runs_than_the_threshold_ring_the_bell_once_per_episode(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    first = await _start_n(orch, fake, 4)
    assert _busy_events(platform) == []  # four is the threshold, not over it
    more = await _start_n(orch, fake, 2)  # five, then six
    assert _busy_events(platform) == [{"running": 5, "threshold": 4, "cap": 3}]
    await _finish(fake, first + more)


async def test_an_episode_that_rang_stays_quiet_however_long_it_lasts(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    now = [1000.0]
    orch._clock = lambda: now[0]
    runs = await _start_n(orch, fake, 5)
    assert len(_busy_events(platform)) == 1
    now[0] += 3 * 3600  # hours later, the same pile-up is still going
    runs += await _start_n(orch, fake, 1)
    assert len(_busy_events(platform)) == 1
    await _finish(fake, runs)


async def test_a_new_episode_rings_again_but_never_twice_in_an_hour(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    now = [1000.0]
    orch._clock = lambda: now[0]
    base = await _start_n(orch, fake, 4)
    spike = await _start_n(orch, fake, 1)
    assert len(_busy_events(platform)) == 1
    await _finish(fake, spike)  # back to the threshold: the episode ends
    now[0] += 10 * 60
    spike = await _start_n(orch, fake, 1)
    assert len(_busy_events(platform)) == 1  # a new episode, but inside the hour
    await _finish(fake, spike)
    now[0] += 51 * 60  # 61 minutes after the first notice
    spike = await _start_n(orch, fake, 1)
    assert len(_busy_events(platform)) == 2
    await _finish(fake, spike + base)


async def test_an_episode_that_began_inside_the_hour_rings_once_the_hour_has_passed(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    now = [1000.0]
    orch._clock = lambda: now[0]
    base = await _start_n(orch, fake, 4)
    spike = await _start_n(orch, fake, 1)
    await _finish(fake, spike)
    now[0] += 30 * 60
    second = await _start_n(orch, fake, 1)  # new episode, suppressed by the hour
    assert len(_busy_events(platform)) == 1
    now[0] += 31 * 60
    third = await _start_n(orch, fake, 1)  # same episode, the hour is up
    assert len(_busy_events(platform)) == 2
    await _finish(fake, base + second + third)


async def test_no_notice_while_a_cap_is_set(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    _set_limit(platform, 10)  # a cap the six runs fit under: nothing parks
    runs = await _start_n(orch, fake, 6)
    assert _busy_events(platform) == []
    await _finish(fake, runs)


async def test_a_threshold_of_zero_never_rings(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    platform.config.busy_runs_notice_at = 0
    runs = await _start_n(orch, fake, 6)
    assert _busy_events(platform) == []
    await _finish(fake, runs)


async def test_attended_runs_never_ring(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    runs = await _start_n(orch, fake, 6, origin="job")
    assert orch.unattended_running() == 0
    assert _busy_events(platform) == []
    await _finish(fake, runs)


async def test_not_now_keeps_the_daemon_quiet_for_a_day(platform, orch):
    fake = _Gates()
    orch.runtime.run = fake.run
    now = [5000.0]
    orch._clock = lambda: now[0]
    assert orch.snooze_busy_notice() == BUSY_SNOOZE_S
    base = await _start_n(orch, fake, 5)
    assert _busy_events(platform) == []  # inside the quiet
    now[0] += BUSY_SNOOZE_S + 1
    later = await _start_n(orch, fake, 1)
    assert len(_busy_events(platform)) == 1  # the day is over
    await _finish(fake, base + later)


def test_the_bells_not_now_route_quiets_the_real_daemon_for_a_day(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        orch = client.app.state.platform.orchestrator
        now = [100.0]
        orch._clock = lambda: now[0]
        r = client.post("/runs/busy/not-now", json={})
        assert r.status_code == 200 and r.json() == {"ok": True, "quiet_s": BUSY_SNOOZE_S}
        assert orch._busy_quiet_until == 100.0 + BUSY_SNOOZE_S


def test_busy_runs_notice_at_is_a_setting_the_writer_saves(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.get("/settings/values").json()["values"]["busy_runs_notice_at"] == 4
        r = client.put("/settings/values", json={"values": {"busy_runs_notice_at": 6}})
        assert r.status_code == 200, r.text
        assert client.app.state.platform.config.busy_runs_notice_at == 6
        # "Queue extras" is the same one write path, with an Undo in the ledger.
        r = client.put("/settings/values", json={"values": {"max_concurrent_sessions": 3}})
        assert r.status_code == 200 and r.json()["action_id"]
        assert client.app.state.platform.config.max_concurrent_sessions == 3


# --------------------------------------------------------------------------- #
# 4. a cap holds at every door, without deadlock
# --------------------------------------------------------------------------- #


def test_cap_two_and_three_schedule_fires_run_two_and_queue_one(platform, orch):
    """The schedule door's real shape: each fire is ``asyncio.run`` on its
    own APScheduler worker thread, so three fires are three event loops."""
    _set_limit(platform, 2)
    started: list[str] = []
    lock = threading.Lock()
    release = threading.Event()

    async def run(session, agent_def):
        with lock:
            started.append(session.id)
        await asyncio.to_thread(release.wait, LIVENESS_S)
        return _done_run(session)

    orch.runtime.run = run
    ids = [
        asyncio.run(orch.create_session(f"fire {i}", AgentType.BUILDER, origin=f"schedule:s{i}")).id
        for i in range(3)
    ]
    results: dict[str, str] = {}

    def fire(sid: str) -> None:
        done = asyncio.run(orch.run_session(sid))
        results[sid] = done.status.value

    threads = [threading.Thread(target=fire, args=(sid,), daemon=True) for sid in ids]
    for t in threads:
        t.start()
    deadline = time.monotonic() + LIVENESS_S
    while not (len(started) == 2 and len(orch._queued) == 1):
        assert time.monotonic() < deadline, (started, list(orch._queued))
        time.sleep(0.01)
    time.sleep(0.2)  # room for a wrong third start to show up
    assert len(started) == 2
    queued_id = orch._queued[0][0]
    assert queued_id not in started
    assert orch.get_session(queued_id).status is SessionStatus.QUEUED
    assert len(orch._governed) == 2
    release.set()
    for t in threads:
        t.join(LIVENESS_S)
    assert sorted(started) == sorted(ids)  # the queued fire ran once a slot freed
    assert results == {sid: "completed" for sid in ids}
    assert not orch._queued and not orch._governed and not orch._running


async def test_a_queued_door_run_waits_its_turn_behind_spawn_managed_work(platform, orch):
    _set_limit(platform, 1)
    fake = _Gates()
    orch.runtime.run = fake.run
    a = await orch.create_session("assignment holds the slot", AgentType.BUILDER, origin="assignment:1")
    goal = await orch.create_session("goal iteration", AgentType.BUILDER, origin="goal:g")
    assert orch.spawn_managed(a.id, orch.run_session(a.id)) is not None
    await _until(lambda: a.id in fake.started, "the managed run started")
    # The goal engine's own shape: ensure_future + register_running.
    t = asyncio.ensure_future(orch.run_session(goal.id))
    orch.register_running(goal.id, t)
    await _until(lambda: orch.get_session(goal.id).status is SessionStatus.QUEUED, "goal parked")
    assert goal.id not in fake.started
    queued = [e.payload for e in platform.event_bus.history if e.type == EventType.SESSION_QUEUED]
    assert queued and queued[-1]["position"] == 1
    fake.gate(a.id).set()
    await _until(lambda: goal.id in fake.started, "the goal got the freed slot")
    fake.gate(goal.id).set()
    assert (await asyncio.wait_for(t, LIVENESS_S)).status is SessionStatus.COMPLETED


async def test_a_door_run_cancelled_while_parked_ends_cancelled_and_leaves_the_queue(platform, orch):
    _set_limit(platform, 1)
    fake = _Gates()
    orch.runtime.run = fake.run
    holder = await orch.create_session("holds", AgentType.BUILDER, origin="schedule:a")
    parked = await orch.create_session("parked", AgentType.BUILDER, origin="schedule:b")
    th = asyncio.create_task(orch.run_session(holder.id))
    await _until(lambda: holder.id in fake.started, "holder started")
    tp = asyncio.create_task(orch.run_session(parked.id))
    await _until(lambda: len(orch._queued) == 1, "parked")
    orch.cancel_session(parked.id)
    with pytest.raises(asyncio.CancelledError):
        await tp
    assert orch.get_session(parked.id).status is SessionStatus.CANCELLED
    assert not orch._queued and parked.id not in fake.started
    fake.gate(holder.id).set()
    await th
    assert not orch._governed


async def test_a_workflow_with_an_agent_step_completes_under_cap_one(platform, orch):
    _set_limit(platform, 1)
    fake = _Gates()
    fake.hold = False
    orch.runtime.run = fake.run
    wf = load_workflow({"name": "two steps", "steps": [
        {"name": "one", "agent": "builder", "task": "first"},
        {"name": "two", "agent": "builder", "task": "second"},
    ]})
    rec = await asyncio.wait_for(WorkflowEngine(platform, orch).run(wf), LIVENESS_S)
    assert rec.status == "completed" and len(fake.started) == 2


async def test_a_run_that_runs_a_workflow_itself_under_cap_one_does_not_wait_on_itself(platform, orch):
    """The deadlock the gate must never build: the outer run holds the only
    slot and AWAITS a workflow whose agent step needs a run of its own."""
    _set_limit(platform, 1)
    started: list[str] = []
    wf = load_workflow({"name": "inner", "steps": [{"name": "s", "agent": "builder", "task": "inner step"}]})

    async def run(session, agent_def):
        started.append(session.origin)
        if session.origin == "schedule:outer":
            rec = await WorkflowEngine(platform, orch).run(wf)
            assert rec.status == "completed"
        return _done_run(session)

    orch.runtime.run = run
    outer = await orch.create_session("outer", AgentType.BUILDER, origin="schedule:outer")
    done = await asyncio.wait_for(orch.run_session(outer.id), LIVENESS_S)
    assert done.status is SessionStatus.COMPLETED
    assert started == ["schedule:outer", "workflow:inner"]


def test_a_parked_managed_run_freed_by_a_schedule_thread_starts_on_the_daemon_loop(platform, orch):
    """A slot freed by a run finishing on APScheduler's thread must start the
    next parked ``spawn_managed`` run on the DAEMON loop: created on the
    scheduler loop it would be cancelled the moment that fire returned."""
    _set_limit(platform, 1)
    release = threading.Event()
    ran_on: dict[str, int] = {}

    async def run(session, agent_def):
        ran_on[session.id] = threading.get_ident()
        if session.origin == "schedule:s":
            await asyncio.to_thread(release.wait, LIVENESS_S)
        return _done_run(session)

    orch.runtime.run = run

    async def main() -> str:
        sched = await orch.create_session("fire", AgentType.BUILDER, origin="schedule:s")
        queued = await orch.create_session("parked", AgentType.BUILDER, origin="assignment:9")
        th = threading.Thread(target=lambda: asyncio.run(orch.run_session(sched.id)), daemon=True)
        th.start()
        await _until(lambda: sched.id in ran_on, "the fire holds the slot")
        assert orch.spawn_managed(queued.id, orch.run_session(queued.id)) is None  # parked
        release.set()
        await asyncio.to_thread(th.join, LIVENESS_S)
        await _until(lambda: queued.id in ran_on, "the parked run started")
        task = orch._running.get(queued.id)
        if task is not None:
            await asyncio.wait_for(task, LIVENESS_S)
        assert orch.get_session(queued.id).status is SessionStatus.COMPLETED
        return queued.id

    queued_id = asyncio.run(main())
    assert ran_on[queued_id] == threading.get_ident()  # this (the daemon) loop's thread


# --------------------------------------------------------------------------- #
# 4b. review round: who counts as background, shutdown, a cross-thread
#     cancel, and the context a promoted run starts in
# --------------------------------------------------------------------------- #


def test_an_untagged_origin_is_a_person_and_a_tagged_door_is_not():
    from iron_jarvis.agents.orchestrator import is_unattended_origin

    # Every door that creates a run with no origin is a person (the Sessions
    # page's New form, Spotlight, ``ij run``, a custom agent's Run).
    assert is_unattended_origin(None) is False
    assert is_unattended_origin("") is False
    assert is_unattended_origin("  ") is False
    assert is_unattended_origin("chat") is False
    assert is_unattended_origin("job:mission") is False
    assert is_unattended_origin("schedule:nightly") is True
    assert is_unattended_origin("memory-review") is True


async def test_a_run_started_by_hand_with_no_origin_is_never_background_or_parked(platform, orch):
    """``POST /sessions {wait: true}`` from the New form carries no origin:
    the person is waiting on the answer, so under a full cap it runs at
    once (attended), outside the background lane and outside the count."""
    _set_limit(platform, 1)
    fake = _Gates()
    orch.runtime.run = fake.run
    holder = await orch.create_session("holds the slot", AgentType.BUILDER, origin="schedule:a")
    th = asyncio.create_task(orch.run_session(holder.id))
    await _until(lambda: holder.id in fake.started, "the schedule run holds the slot")
    by_hand = await orch.create_session("typed into the New form", AgentType.BUILDER)
    assert by_hand.origin is None
    fake.gate(by_hand.id).set()
    done = await asyncio.wait_for(orch.run_session(by_hand.id), LIVENESS_S)
    assert done.status is SessionStatus.COMPLETED
    assert fake.lane[by_hand.id] == (False, False)
    assert not orch._queued
    assert orch.unattended_running() == 1  # the schedule run only
    fake.gate(holder.id).set()
    await th


async def test_a_door_run_arriving_while_the_daemon_shuts_down_never_parks(platform, orch):
    """``shutdown_queue`` already discarded the queue: a schedule fire that
    lands after it must not park where nothing will ever wake it."""
    _set_limit(platform, 1)
    fake = _Gates()
    orch.runtime.run = fake.run
    holder = await orch.create_session("holds", AgentType.BUILDER, origin="schedule:a")
    th = asyncio.create_task(orch.run_session(holder.id))
    await _until(lambda: holder.id in fake.started, "holder started")
    orch.shutdown_queue()
    late = await orch.create_session("late fire", AgentType.BUILDER, origin="schedule:late")
    await asyncio.wait_for(orch.run_session(late.id), LIVENESS_S)
    assert late.id not in fake.started
    assert not orch._queued
    fake.gate(holder.id).set()
    await th


async def test_a_parked_door_run_whose_row_was_cancelled_from_another_thread_is_finalized(platform, orch):
    """``cancel_session`` from a foreign thread stamps the row CANCELLED at
    once and hands ``task.cancel`` to the run's loop. A promotion that reads
    the row first drops the ticket: the run must still end as a real stop
    (``session.completed``), not return silently past its own cancel."""
    _set_limit(platform, 1)
    fake = _Gates()
    orch.runtime.run = fake.run
    holder = await orch.create_session("holds", AgentType.BUILDER, origin="schedule:a")
    parked = await orch.create_session("parked", AgentType.BUILDER, origin="schedule:b")
    th = asyncio.create_task(orch.run_session(holder.id))
    await _until(lambda: holder.id in fake.started, "holder started")
    tp = asyncio.create_task(orch.run_session(parked.id))
    await _until(lambda: len(orch._queued) == 1, "parked")
    row = orch.get_session(parked.id)
    row.status = SessionStatus.CANCELLED  # the cross-thread stamp, cancel still in flight
    orch._save(row)
    fake.gate(holder.id).set()  # the slot frees; the promotion reads CANCELLED
    await th
    done = await asyncio.wait_for(tp, LIVENESS_S)
    assert done.status is SessionStatus.CANCELLED
    assert parked.id not in fake.started
    completed = [
        e for e in platform.event_bus.history
        if e.type == EventType.SESSION_COMPLETED and e.session_id == parked.id
    ]
    assert len(completed) == 1
    assert not orch._queued and not orch._governed


async def test_a_persons_parked_run_promoted_by_a_schedule_runs_end_is_not_background_work(platform, orch):
    """The slot-free hook runs in the context of the run that freed it; a
    schedule run is in the background lane, and a promoted run must not
    inherit that lane (below-normal priority) from it."""
    _set_limit(platform, 1)
    fake = _Gates()
    orch.runtime.run = fake.run
    sched = await orch.create_session("nightly", AgentType.BUILDER, origin="schedule:s")
    job = await orch.create_session("pressed Run", AgentType.BUILDER, origin="job")
    ts = asyncio.create_task(orch.run_session(sched.id))  # self-registers
    await _until(lambda: sched.id in fake.started, "the schedule run holds the slot")
    assert orch.spawn_managed(job.id, orch.run_session(job.id)) is None  # parked
    fake.gate(job.id).set()
    fake.gate(sched.id).set()
    await ts
    await _until(lambda: job.id in fake.lane, "the parked run was promoted")
    assert fake.lane[sched.id] == (True, True)
    assert fake.lane[job.id] == (False, False)
    task = orch._running.get(job.id)
    if task is not None:
        await asyncio.wait_for(task, LIVENESS_S)


# --------------------------------------------------------------------------- #
# 5. the dispatcher rests when it has nothing
# --------------------------------------------------------------------------- #


class _Counted:
    """Wrap an assignment store and count the dispatcher's SELECTs."""

    def __init__(self, store) -> None:
        self._s = store
        self.queued_calls = 0
        self.list_calls = 0

    def queued(self, *a, **k):
        self.queued_calls += 1
        return self._s.queued(*a, **k)

    def list(self, *a, **k):
        self.list_calls += 1
        return self._s.list(*a, **k)

    def __getattr__(self, name):
        return getattr(self._s, name)


async def _drive(dispatcher, fake_seconds: float) -> list[float]:
    """Run ``run_forever`` on a counted fake clock: each wait advances it by
    its timeout (or by nothing, when a wake is already set)."""
    clock = [0.0]
    waits: list[float] = []
    stop = asyncio.Event()

    async def fake_sleep(stop_ev, timeout):
        waits.append(timeout)
        if not dispatcher._wake.is_set():
            clock[0] += timeout
        if clock[0] >= fake_seconds:
            stop_ev.set()
        await asyncio.sleep(0)

    dispatcher._sleep = fake_sleep
    await asyncio.wait_for(dispatcher.run_forever(stop), LIVENESS_S)
    return waits


async def test_an_empty_queue_is_read_once_a_minute_not_every_five_seconds(platform):
    counted = _Counted(platform.assignments)
    platform.assignments = counted
    d = AssignmentDispatcher(platform)
    await _drive(d, 600)
    # Ten idle minutes: 10 passes (each 2 SELECTs) instead of 120.
    assert counted.queued_calls == 10 and counted.list_calls == 10


async def test_a_queued_assignment_keeps_the_five_second_pass(platform):
    counted = _Counted(platform.assignments)
    platform.assignments = counted
    platform.assignments.create("builder", "waits behind a held slot")
    platform.orchestrator = None  # the pass holds it: "not ready yet"
    d = AssignmentDispatcher(platform)
    await _drive(d, 600)
    assert counted.queued_calls == 120


async def test_a_wake_during_a_pass_is_kept_for_the_wait_after_it(platform):
    store = platform.assignments
    d = AssignmentDispatcher(platform)
    seen: list[bool] = []
    real_queued = store.queued

    def queued_then_wake(*a, **k):
        out = real_queued(*a, **k)
        if not seen:
            d.wake()  # a job queued while the pass was reading
        return out

    store.queued = queued_then_wake
    stop = asyncio.Event()

    async def fake_sleep(stop_ev, timeout):
        seen.append(d._wake.is_set())
        stop_ev.set()

    d._sleep = fake_sleep
    await asyncio.wait_for(d.run_forever(stop), LIVENESS_S)
    assert seen == [True]


async def test_a_wake_from_another_thread_reaches_an_idle_dispatcher(platform):
    loop = asyncio.get_running_loop()
    second_pass = asyncio.Event()
    counted = _Counted(platform.assignments)
    real_queued = counted.queued

    def queued(*a, **k):
        out = real_queued(*a, **k)
        if counted.queued_calls == 2:  # store reads run in a worker thread
            loop.call_soon_threadsafe(second_pass.set)
        return out

    counted.queued = queued
    platform.assignments = counted
    d = AssignmentDispatcher(platform)  # idle wait: 60 s
    stop = asyncio.Event()
    loop_task = asyncio.create_task(d.run_forever(stop))
    try:
        await _until(lambda: counted.queued_calls == 1, "the first pass")
        # A schedule-fired run's assign tool, on APScheduler's thread. The
        # loop is left IDLE while it waits (no polling): only a thread-safe
        # wake can rouse it before the idle minute is out. The thread waits
        # until this loop is surely blocked — a set that lands while the loop
        # is still busy would be seen anyway and prove nothing.
        threading.Timer(0.3, d.wake).start()
        await asyncio.wait_for(second_pass.wait(), LIVENESS_S)
    finally:
        stop.set()
        await asyncio.wait_for(loop_task, LIVENESS_S)
