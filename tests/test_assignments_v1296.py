"""Assignments (v1.296.0): give an agent a job and the job waits for it.

Offline, on ``build_platform(tmp_path)`` + the MockLLM (the shape of
``tests/test_dynamic_agents.py``). The dispatcher is driven by calling its
``tick()`` directly — never by sleeping on ``run_forever``.

Pins, in order:
1. create validates and refuses honestly (unknown, supervisor, planner,
   a custom coordinator, remote, blank, depth, the 20-cap);
2. an idempotency key coalesces into the live row;
3. ``claim_next`` is a compare-and-swap under a barrier race;
4. dispatch order is priority, then FIFO;
5. ``finish`` → done / failed / BLOCKED on the third failure, in the words;
6. the dispatcher HOLDS a paused, exhausted or busy agent with the sentence;
7. a full tick runs a queued job end-to-end on the MockLLM and the hook
   settles it (origin, credit, events, allowance tail);
8. a FAILED run counts a failure; 9. boot requeue; 10. health; 11. the
   roster says "idle"; 12. the tool queues with depth and refuses a
   supervisor; 13. "assignment" is an asking origin.
"""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import select

from iron_jarvis.agents import dynamic_models  # noqa: F401  (registers the table)
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.roster import build_roster
from iron_jarvis.agents.runtime import ASKING_ORIGINS, ATTENDED_ORIGINS
from iron_jarvis.assignments import AssignmentDispatcher, AssignmentStore, agent_health
from iron_jarvis.assignments import models as asg_models  # noqa: F401
from iron_jarvis.assignments.dispatcher import HELD_BUSY
from iron_jarvis.assignments.models import AssignmentRecord
from iron_jarvis.assignments.store import BLOCK_AFTER_FAILURES, MAX_QUEUED_PER_ASSIGNEE
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import EventRecord, Session, SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

PROMPT = "You are Scout, a focused research helper. Be concise."
TOOLS = ["read_file", "write_file", "list_files"]
NAME = "scout"
ROSTER = "custom:scout"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine(
        {**p.config.permissions, "assign_work": "allow", "spawn_agent": "allow"}
    )
    p.orchestrator = Orchestrator(p)
    p.agents_registry.register(NAME, PROMPT, TOOLS)
    return p


@pytest.fixture
def store(platform) -> AssignmentStore:
    return platform.assignments


@pytest.fixture
def dispatcher(platform) -> AssignmentDispatcher:
    d = AssignmentDispatcher(platform, interval_s=0.05)
    platform.assignment_dispatcher = d
    return d


def _events(engine, type_: str) -> list[dict]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(EventRecord).where(EventRecord.type == type_)))
    return [json.loads(r.payload_json or "{}") for r in rows]


def _sessions(engine) -> list[Session]:
    with session_scope(engine) as db:
        return list(db.exec(select(Session)))


def _ctx(platform, tmp_path, session_id="parent-session"):
    return ToolContext(
        workspace=tmp_path,
        session_id=session_id,
        agent_run_id="run1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _spy_usage(platform, usage: dict):
    """Stamp a token usage onto the mock's answers so a run SPENDS (the
    v1144 provider spy's shape)."""
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            resp = await real_complete(system=system, messages=messages, tools=tools, **kw)
            resp.usage = dict(usage)
            return resp

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


async def _settle(platform):
    """Let the store's fire-and-forget publishes land in the ledger."""
    await platform.assignments.flush_events()
    await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# 1. create: validation + honest refusals
# --------------------------------------------------------------------------- #


def test_create_queues_a_job_with_a_derived_title_and_publishes_created(platform, store):
    rec, created = store.create(NAME, "Rename the 2025 returns\nby client name", reason="backlog")
    assert created is True and rec.status == "queued"
    assert rec.assignee == ROSTER, "a bare slug canonicalises to custom:<slug>"
    assert rec.title == "Rename the 2025 returns"
    assert rec.id.startswith("asg_") and rec.source == "user" and rec.depth == 0
    rec2, _ = store.create("builder", "x" * 300)
    assert len(rec2.title) == 80 and rec2.title.endswith("…")
    assert rec2.assignee == "builder"


@pytest.mark.parametrize(
    "assignee, task, kw, words",
    [
        ("nobody", "do it", {}, "unknown agent"),
        ("supervisor", "do it", {}, "supervisor cannot take"),
        ("planner", "do it", {}, "can delegate work itself"),
        ("remote:hermes", "do it", {}, "remote agent"),
        ("custom:ghost", "do it", {}, "no custom agent named"),
        ("builder", "   ", {}, "needs a task"),
        ("", "do it", {}, "needs an assignee"),
        ("builder", "do it", {"depth": 3}, "this deep"),
    ],
)
def test_create_refuses_with_one_plain_sentence(store, assignee, task, kw, words):
    with pytest.raises(ValueError) as exc:
        store.create(assignee, task, **kw)
    assert words in str(exc.value)
    assert store.list() == [], "a refused assignment leaves no row"


def test_create_refuses_a_custom_coordinator(platform, store):
    platform.agents_registry.register("boss", "You coordinate.", ["delegate", "read_file"])
    with pytest.raises(ValueError, match="can delegate work itself"):
        store.create("boss", "run the team")
    platform.agents_registry.register("chief", "You coordinate.", [], base_type="supervisor")
    with pytest.raises(ValueError, match="supervisor"):
        store.create("custom:chief", "run the team")


def test_create_caps_the_queue_per_assignee(store):
    for i in range(MAX_QUEUED_PER_ASSIGNEE):
        store.create("builder", f"job {i}")
    with pytest.raises(ValueError, match="already has 20 assignments waiting"):
        store.create("builder", "one more")
    # Another agent's queue is its own.
    store.create(NAME, "fine")


# --------------------------------------------------------------------------- #
# 2. idempotency coalesces
# --------------------------------------------------------------------------- #


def test_idempotency_key_coalesces_into_the_live_row(store):
    a, created_a = store.create("builder", "nightly sweep", idempotency_key="sweep")
    b, created_b = store.create("builder", "nightly sweep", idempotency_key="sweep")
    assert created_a and not created_b and b.id == a.id
    assert b.coalesced_count == 1
    assert len(store.list(assignee="builder")) == 1
    # A different assignee with the same key is a different job.
    c, created_c = store.create(NAME, "nightly sweep", idempotency_key="sweep")
    assert created_c and c.id != a.id
    # Once the first is settled, the key is free again.
    store.claim_next("builder")
    store.start(a.id, "s1")
    store.finish(a.id, True)
    d, created_d = store.create("builder", "nightly sweep", idempotency_key="sweep")
    assert created_d and d.id != a.id


# --------------------------------------------------------------------------- #
# 3. the claim is a compare-and-swap
# --------------------------------------------------------------------------- #


def test_claim_next_is_atomic_under_a_barrier_race(store):
    """Two claimers at the same instant over ONE queued row: exactly one
    wins. Barrier-synchronised and repeated — a race that fires one time in
    ten passes a single-shot test nine times out of ten."""
    for trial in range(15):
        rec, _ = store.create("builder", f"race {trial}")
        gate = threading.Barrier(2)

        def take(_n):
            gate.wait(timeout=10)
            return store.claim_next("builder")

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(take, ["a", "b"]))
        winners = [r for r in (first, second) if r is not None]
        assert len(winners) == 1, f"trial {trial}: {len(winners)} claimers won the same row"
        assert winners[0].id == rec.id and winners[0].status == "claimed"
        assert winners[0].claim_token
        store.start(rec.id, f"s{trial}")
        store.finish(rec.id, True)


# --------------------------------------------------------------------------- #
# 4. priority, then FIFO
# --------------------------------------------------------------------------- #


def test_claim_order_is_priority_then_oldest(store):
    low1, _ = store.create("builder", "low first")
    low2, _ = store.create("builder", "low second")
    high, _ = store.create("builder", "high", priority=5)
    order = []
    while (row := store.claim_next("builder")) is not None:
        order.append(row.id)
        store.start(row.id, "s")
        store.finish(row.id, True)
    assert order == [high.id, low1.id, low2.id]


# --------------------------------------------------------------------------- #
# 5. finish: done / failed / blocked at 3 with the sentence
# --------------------------------------------------------------------------- #


def _run_once(store, rec_id, ok, error=""):
    row = store.get(rec_id)
    if row.status == "queued":
        store.claim_next(row.assignee)
    store.start(rec_id, "s")
    return store.finish(rec_id, ok, error)


def test_finish_done_failed_and_blocked_on_the_third_failure(platform, store):
    rec, _ = store.create("builder", "flaky")
    done = _run_once(store, rec.id, True)
    assert done.status == "done" and done.attempts == 1 and done.failure_count == 0

    rec, _ = store.create("builder", "breaks")
    failed = _run_once(store, rec.id, False, "boom 1")
    assert failed.status == "failed" and failed.failure_count == 1 and failed.last_error == "boom 1"
    # retry carries the count forward; the chain is what the breaker counts.
    second = store.retry(rec.id)
    assert second.status == "queued" and second.failure_count == 1 and second.source == f"retry:{rec.id}"
    _run_once(store, second.id, False, "boom 2")
    third = store.retry(second.id)
    blocked = _run_once(store, third.id, False, "boom 3")
    assert blocked.status == "blocked" and blocked.failure_count == BLOCK_AFTER_FAILURES
    assert blocked.blocked_reason == "3 failed runs in a row — last error: boom 3"
    assert blocked.held_reason == ""
    # unblock resets the breaker; the row is queued again.
    back = store.unblock(third.id)
    assert back.status == "queued" and back.failure_count == 0 and back.blocked_reason == ""
    # finish is idempotent against a row that is not running.
    assert store.finish(third.id, False, "late").status == "queued"


async def test_blocked_publishes_assignment_blocked(platform, store):
    rec, _ = store.create("builder", "breaks")

    def fail(row_id):
        store.claim_next("builder")
        store.start(row_id, "s")
        return store.finish(row_id, False, "nope")

    with session_scope(platform.engine) as db:  # pre-load two strikes
        row = db.get(AssignmentRecord, rec.id)
        row.failure_count = 2
        db.add(row)
        db.commit()
    blocked = fail(rec.id)
    assert blocked.status == "blocked"
    await _settle(platform)
    events = _events(platform.engine, EventType.ASSIGNMENT_BLOCKED)
    assert len(events) == 1 and events[0]["id"] == rec.id
    assert events[0]["blocked_reason"].startswith("3 failed runs in a row")


def test_cancel_and_retry(store):
    rec, _ = store.create("builder", "later")
    assert store.cancel(rec.id).status == "cancelled"
    assert store.cancel(rec.id).status == "cancelled", "idempotent"
    again = store.retry(rec.id)
    assert again.status == "queued" and again.task == "later" and again.failure_count == 0
    assert store.retry(again.id) is None, "only a settled row can be retried"


# --------------------------------------------------------------------------- #
# 6. the dispatcher holds: paused / exhausted / busy
# --------------------------------------------------------------------------- #


async def test_tick_holds_a_paused_agent_with_the_reason(platform, store, dispatcher):
    platform.agents_registry.set_paused(NAME, "day off")
    rec, _ = store.create(NAME, "summarize")
    result = await dispatcher.tick()
    assert result["started"] == [] and result["held"] == 1
    assert store.get(rec.id).status == "queued"
    assert store.get(rec.id).held_reason == "scout is paused: day off"
    assert _sessions(platform.engine) == [], "no session for a paused agent"
    # resume → the next tick starts it.
    platform.agents_registry.resume(NAME)
    result = await dispatcher.tick()
    assert result["started"] == [rec.id]
    await dispatcher.drain()
    assert store.get(rec.id).status == "done"


async def test_tick_holds_an_exhausted_agent(platform, store, dispatcher):
    platform.agents_registry.register(NAME, PROMPT, TOOLS, allowance_tokens=100)
    with session_scope(platform.engine) as db:
        db.add(Session(task="seeded", agent_name=ROSTER, input_tokens=150, provider="mock", model="mock-1"))
        db.commit()
    rec, _ = store.create(NAME, "summarize")
    result = await dispatcher.tick()
    assert result["held"] == 1
    assert store.get(rec.id).held_reason.startswith("scout has used its monthly allowance")


async def test_tick_runs_one_at_a_time_per_agent(platform, store, dispatcher):
    first, _ = store.create("builder", "first")
    second, _ = store.create("builder", "second")
    other, _ = store.create(NAME, "elsewhere")
    # Hold the first run open so the second sees a busy agent.
    gate = asyncio.Event()
    real_run = platform.orchestrator.run_session

    async def slow_run(session_id, definition=None):
        await gate.wait()
        return await real_run(session_id, definition=definition)

    platform.orchestrator.run_session = slow_run
    result = await dispatcher.tick()
    assert set(result["started"]) == {first.id, other.id}, "one per agent per tick"
    assert store.get(first.id).status == "running"
    result = await dispatcher.tick()
    assert result["started"] == [] and result["held"] == 1
    assert store.get(second.id).held_reason == HELD_BUSY.format(name="builder")
    gate.set()
    await dispatcher.drain()
    assert store.get(first.id).status == "done"
    result = await dispatcher.tick()
    assert result["started"] == [second.id]
    await dispatcher.drain()
    assert store.get(second.id).status == "done" and store.get(second.id).held_reason == ""


async def test_tick_holds_when_every_session_slot_is_busy(platform, store, dispatcher):
    platform.config.max_concurrent_sessions = 1
    platform.orchestrator._governed.add("someone-else")
    rec, _ = store.create("builder", "wait your turn")
    result = await dispatcher.tick()
    assert result["held"] == 1 and "slot" in store.get(rec.id).held_reason
    platform.orchestrator._governed.clear()
    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()


# --------------------------------------------------------------------------- #
# 7. a full tick, end to end on the MockLLM
# --------------------------------------------------------------------------- #


async def test_tick_runs_a_custom_agents_job_end_to_end(platform, store, dispatcher, tmp_path):
    _spy_usage(platform, {"input_tokens": 40, "output_tokens": 2})
    platform.agents_registry.register(NAME, PROMPT, TOOLS, allowance_tokens=10_000)
    rec, _ = store.create(NAME, "summarize the project", reason="weekly digest", priority=1)
    result = await dispatcher.tick()
    assert result["started"] == [rec.id]
    running = store.get(rec.id)
    assert running.status == "running" and running.session_id and running.held_reason == ""
    await dispatcher.drain()
    await _settle(platform)

    done = store.get(rec.id)
    assert done.status == "done" and done.attempts == 1 and done.finished_at is not None
    sessions = _sessions(platform.engine)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.id == done.session_id
    assert s.origin == f"assignment:{rec.id}"
    assert s.agent_name == ROSTER, "credited to the agent, so the allowance sees it"
    assert s.status is SessionStatus.COMPLETED
    assert "weekly digest" in s.task
    assert s.input_tokens >= 40, "the allowance tail is fed by the stamped usage"

    started = _events(platform.engine, EventType.ASSIGNMENT_STARTED)
    assert len(started) == 1 and started[0]["session_id"] == s.id and started[0]["assignee"] == ROSTER
    finished = _events(platform.engine, EventType.ASSIGNMENT_FINISHED)
    assert len(finished) == 1 and finished[0]["ok"] is True and finished[0]["id"] == rec.id
    created = _events(platform.engine, EventType.ASSIGNMENT_CREATED)
    assert len(created) == 1 and created[0]["reason"] == "weekly digest"
    # The allowance ledger credited the run to custom:scout.
    from iron_jarvis.agents import allowance

    spend = allowance.month_spend(platform.engine, ROSTER)
    assert spend["runs"] == 1 and spend["tokens"] >= 42
    # Nothing left for the next tick.
    assert (await dispatcher.tick())["started"] == []


async def test_a_builtin_runs_too_and_a_payload_rides_the_session(platform, store, dispatcher, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    rec, _ = store.create(
        "builder", "list the folder", payload={"workspace_root": str(folder), "max_steps": 4}
    )
    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()
    s = _sessions(platform.engine)[0]
    assert s.agent_name == "builder" and s.max_steps == 4
    assert str(folder) in (s.workspace_path or "")
    assert store.get(rec.id).status == "done"


# --------------------------------------------------------------------------- #
# 8. a FAILED run counts a failure (the hook at the failure finalizer)
# --------------------------------------------------------------------------- #


async def test_a_failed_run_increments_failure_count_through_the_hook(platform, store, dispatcher):
    def broken_get(p, m=None):
        raise RuntimeError("provider down at 3am")

    platform.providers.get = broken_get
    rec, _ = store.create(NAME, "summarize")
    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()
    await _settle(platform)
    row = store.get(rec.id)
    assert row.status == "failed" and row.failure_count == 1
    assert "provider down at 3am" in row.last_error
    s = _sessions(platform.engine)[0]
    assert s.status is SessionStatus.FAILED
    finished = _events(platform.engine, EventType.ASSIGNMENT_FINISHED)
    assert len(finished) == 1 and finished[0]["ok"] is False and "provider down" in finished[0]["error"]


async def test_a_cancelled_run_settles_the_assignment_through_the_hook(platform, store, dispatcher):
    gate = asyncio.Event()

    async def slow_run(session, agent_def):
        gate.set()
        await asyncio.sleep(30)

    platform.orchestrator.runtime.run = slow_run  # the real run_session handles the cancel
    rec, _ = store.create("builder", "long job")
    assert (await dispatcher.tick())["started"] == [rec.id]
    await gate.wait()
    sid = store.get(rec.id).session_id
    platform.orchestrator.cancel_session(sid)
    await dispatcher.drain()
    row = store.get(rec.id)
    # Reviewer (v1.296.0): a stop is NOT a failure — cancelled, no strike,
    # Retry stays offered. (The doer's version pinned failed/1.)
    assert row.status == "cancelled" and row.failure_count == 0
    assert row.last_error == "" and row.finished_at is not None
    finished = _events(platform.engine, EventType.ASSIGNMENT_FINISHED)
    assert len(finished) == 1 and finished[0]["ok"] is False
    assert finished[0]["status"] == "cancelled"


async def test_the_sweep_settles_a_running_row_whose_session_ended_without_the_hook(platform, store, dispatcher):
    rec, _ = store.create("builder", "stamped from outside")
    store.claim_next("builder")
    with session_scope(platform.engine) as db:
        db.add(Session(id="out-of-band", task="t", status=SessionStatus.CANCELLED, summary="stopped"))
        db.commit()
    store.start(rec.id, "out-of-band")
    result = await dispatcher.tick()
    assert result["swept"] == 1
    row = store.get(rec.id)
    # A session CANCELLED from outside is a cancelled assignment (no strike);
    # a FAILED one is a failed assignment carrying the summary's first line.
    assert row.status == "cancelled" and row.failure_count == 0
    rec2, _ = store.create("builder", "crashed from outside")
    store.claim_next("builder")
    with session_scope(platform.engine) as db:
        db.add(Session(id="out-of-band-2", task="t", status=SessionStatus.FAILED, summary="boom"))
        db.commit()
    store.start(rec2.id, "out-of-band-2")
    assert (await dispatcher.tick())["swept"] == 1
    row2 = store.get(rec2.id)
    assert row2.status == "failed" and row2.last_error == "boom" and row2.failure_count == 1


# --------------------------------------------------------------------------- #
# 9. boot: lost claims come back, once, with one event
# --------------------------------------------------------------------------- #


async def test_requeue_lost_at_boot_requeues_and_publishes_once(platform, store, dispatcher):
    a, _ = store.create("builder", "was running")
    store.claim_next("builder")
    store.start(a.id, "dead-session")
    b, _ = store.create(NAME, "was claimed")
    assert store.claim_next(ROSTER).id == b.id
    c, _ = store.create(NAME, "still queued")
    live, _ = store.create("builder", "alive")  # a second builder row, claimed by a live session
    with session_scope(platform.engine) as db:
        row = db.get(AssignmentRecord, live.id)
        row.status = "running"
        row.session_id = "live-session"
        db.add(row)
        db.commit()

    out = await dispatcher.requeue_lost_at_boot({"live-session"})
    assert {o["id"] for o in out} == {a.id, b.id}
    for id_ in (a.id, b.id):
        row = store.get(id_)
        assert row.status == "queued" and row.claim_token == ""
        assert row.held_reason == "restarted after an update or crash"
    assert store.get(c.id).held_reason == ""
    assert store.get(live.id).status == "running", "a live session's claim is kept"
    events = _events(platform.engine, EventType.ASSIGNMENT_REQUEUED)
    assert len(events) == 1 and events[0]["count"] == 2 and set(events[0]["ids"]) == {a.id, b.id}
    # Nothing to requeue → no event.
    assert await dispatcher.requeue_lost_at_boot({"live-session"}) == []
    assert len(_events(platform.engine, EventType.ASSIGNMENT_REQUEUED)) == 1


# --------------------------------------------------------------------------- #
# 10. health, 11. the roster says idle
# --------------------------------------------------------------------------- #


async def test_agent_health_reads_runs_and_the_queue(platform, store, dispatcher):
    empty = agent_health(platform.engine, store, ROSTER)
    assert empty == {
        "last_run_at": None, "last_outcome": None, "last_error": "",
        "last_wake_at": None, "queued": 0, "running": 0, "blocked": 0,
    }
    rec, _ = store.create(NAME, "summarize")
    store.create(NAME, "later")
    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()
    await _settle(platform)
    h = agent_health(platform.engine, store, ROSTER)
    assert h["last_outcome"] in ("completed", "completed_with_failures")
    assert h["last_run_at"] and h["last_wake_at"]
    assert h["queued"] == 1 and h["running"] == 0 and h["blocked"] == 0
    # A builtin's own runs may carry agent_name "" — the two-signal fallback.
    with session_scope(platform.engine) as db:
        db.add(Session(task="old", agent_type="builder", agent_name="", status=SessionStatus.FAILED,
                       summary="Session failed: boom\nmore"))
        db.commit()
    hb = agent_health(platform.engine, store, "builder")
    assert hb["last_outcome"] == "failed" and hb["last_error"] == "Session failed: boom"
    assert agent_health(None, None, "builder")["queued"] == 0, "never raises"


def test_roster_emits_idle_and_carries_health(platform, store):
    entries = {e.name: e for e in build_roster(platform)}
    assert entries[ROSTER].activity == "idle" and entries[ROSTER].health is not None
    assert entries["builder"].activity == "idle"
    assert entries[ROSTER].as_dict()["health"]["queued"] == 0
    store.create(NAME, "pending")
    entries = {e.name: e for e in build_roster(platform)}
    assert entries[ROSTER].activity == "unknown", "something waits — not idle"
    assert entries[ROSTER].health["queued"] == 1
    # No store → the lookup fails → "unknown", never "idle".
    platform.assignments = None
    entries = {e.name: e for e in build_roster(platform)}
    assert entries["builder"].activity == "unknown" and entries["builder"].health is None


# --------------------------------------------------------------------------- #
# 12. the assign_work tool
# --------------------------------------------------------------------------- #


async def test_assign_work_tool_queues_with_depth_and_refuses_a_supervisor(platform, store, dispatcher, tmp_path):
    result = await platform.registry.invoke(
        "assign_work", {"agent": NAME, "task": "index the folder", "reason": "for later"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error
    assert result.output.endswith("queued for custom:scout — it runs when custom:scout is free")
    row = store.get(result.data["assignment_id"])
    assert row.depth == 1 and row.source == "agent:parent-session" and row.reason == "for later"
    assert dispatcher._wake.is_set(), "the tool wakes the dispatcher"

    # From INSIDE an assignment run the depth climbs; past 2 it is refused.
    with session_scope(platform.engine) as db:
        db.add(Session(id="asg-run", task="t", origin=f"assignment:{row.id}"))
        db.commit()
    nested = await platform.registry.invoke(
        "assign_work", {"agent": "builder", "task": "sub-job"},
        _ctx(platform, tmp_path, session_id="asg-run"), platform.permissions,
    )
    assert nested.ok and nested.data["depth"] == 2
    with session_scope(platform.engine) as db:
        db.add(Session(id="asg-run-2", task="t", origin=f"assignment:{nested.data['assignment_id']}"))
        db.commit()
    too_deep = await platform.registry.invoke(
        "assign_work", {"agent": "builder", "task": "sub-sub-job"},
        _ctx(platform, tmp_path, session_id="asg-run-2"), platform.permissions,
    )
    assert too_deep.ok is False and "this deep" in too_deep.error

    refused = await platform.registry.invoke(
        "assign_work", {"agent": "supervisor", "task": "run the team"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert refused.ok is False and "supervisor cannot take" in refused.error
    assert len(store.list()) == 2


def test_assign_work_defaults_to_ask_and_sits_on_no_builtin_roster(tmp_path):
    from iron_jarvis.agents.types import _DEFINITIONS

    fresh = build_platform(str(tmp_path / "fresh"))  # the fixture above grants it
    assert fresh.config.permissions["assign_work"] == "ask"
    assert fresh.permissions._base["assign_work"] == "ask", "both copies seeded"
    assert fresh.registry.get("assign_work") is not None, "registered on every platform"
    for definition in _DEFINITIONS.values():
        assert "assign_work" not in (definition.tools or [])


# --------------------------------------------------------------------------- #
# 13. the origin may ask, unattended
# --------------------------------------------------------------------------- #


def test_assignment_is_an_asking_origin_but_not_attended():
    assert "assignment" in ASKING_ORIGINS
    assert not any(str(o).startswith("assignment") for o in ATTENDED_ORIGINS)


# --------------------------------------------------------------------------- #
# 14. reviewer pins (wave 2): loop safety, boot order, tick serialization,
#     the headless allowlist, the coordinator rule, the governor
# --------------------------------------------------------------------------- #


def test_create_from_a_worker_thread_with_no_bound_loop_keeps_the_row(platform, store):
    """The store publishes from sync code. With NO loop bound (the CLI, a
    sync test) and from a worker thread, ``create`` must neither raise nor
    lose the row — the event is dropped, the bookkeeping is not."""
    store.bind_loop(None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        rec, created = pool.submit(store.create, "builder", "from a thread").result(timeout=10)
    assert created and store.get(rec.id).status == "queued"
    assert store._event_tasks == set(), "nothing to await — the publish was dropped, not parked"
    # A CLOSED loop bound (shutdown) is the same: no raise, no lost row.
    dead = asyncio.new_event_loop()
    dead.close()
    store.bind_loop(dead)
    with ThreadPoolExecutor(max_workers=1) as pool:
        rec2, _ = pool.submit(store.create, "builder", "after shutdown").result(timeout=10)
    assert store.get(rec2.id).status == "queued"
    assert store._event_tasks == set()
    store.bind_loop(None)


async def test_boot_reconcile_then_requeue_puts_an_interrupted_run_back_without_a_strike(
    platform, store, dispatcher
):
    """The lifespan order: ``reconcile_interrupted_sessions`` (the session row
    goes FAILED — nothing runs on a fresh process) THEN ``requeue_lost_at_boot``.
    The assignment is QUEUED again with the restart reason and NO failure
    strike — an interruption is not the agent failing."""
    rec, _ = store.create(NAME, "was mid-run when the daemon restarted")
    assert store.claim_next(ROSTER).id == rec.id
    with session_scope(platform.engine) as db:
        db.add(Session(id="interrupted-run", task="t", status=SessionStatus.ACTIVE,
                       origin=f"assignment:{rec.id}", agent_name=ROSTER))
        db.commit()
    assert store.start(rec.id, "interrupted-run").status == "running"
    orch = platform.orchestrator
    assert orch._running == {}, "a fresh process runs nothing"
    assert orch.reconcile_interrupted_sessions() >= 1
    with session_scope(platform.engine) as db:
        assert db.get(Session, "interrupted-run").status is SessionStatus.FAILED
    out = await dispatcher.requeue_lost_at_boot(set(orch._running))
    assert [o["id"] for o in out] == [rec.id]
    row = store.get(rec.id)
    assert row.status == "queued" and row.failure_count == 0 and row.attempts == 0
    assert row.held_reason == "restarted after an update or crash"
    assert row.session_id == "interrupted-run", "the old session id stays for the trail"
    # The first sweep after boot leaves a queued row alone (no strike there either).
    assert (await dispatcher.sweep()) == 0
    assert store.get(rec.id).status == "queued" and store.get(rec.id).failure_count == 0


async def test_overlapping_ticks_start_one_job_per_agent(platform, store, dispatcher):
    """``tick`` is serialized. Two passes in flight at once (run_forever's
    pass + a wake) both read "builder is free" and the CAS re-pick hands the
    loser builder's NEXT row — two sessions for one agent. The lock makes the
    second pass wait and then HOLD on the first's claim."""
    first, _ = store.create("builder", "first")
    second, _ = store.create("builder", "second")
    gate = asyncio.Event()
    real_run = platform.orchestrator.run_session

    async def slow_run(session_id, definition=None):
        await gate.wait()
        return await real_run(session_id, definition=definition)

    platform.orchestrator.run_session = slow_run
    a, b = await asyncio.gather(dispatcher.tick(), dispatcher.tick())
    started = a["started"] + b["started"]
    assert started == [first.id], f"one job per agent across overlapping passes, got {started}"
    assert a["held"] + b["held"] == 1
    assert store.get(second.id).status == "queued"
    assert store.get(second.id).held_reason == HELD_BUSY.format(name="builder")
    assert len(_sessions(platform.engine)) == 1
    gate.set()
    await dispatcher.drain()


def test_assign_work_is_headless_safe_and_asks_by_default():
    """An unattended run (a schedule, a goal) may queue work exactly as it may
    delegate: ``assign_work`` sits in SAFE_HEADLESS_TOOLS, so the daemon's own
    resolver grants its ``ask`` with nobody present — and the default tier IS
    ask (never allow), seeded in default_permissions like spawn_agent."""
    from iron_jarvis.core.config import default_permissions
    from iron_jarvis.tools.permissions import (
        DENY_FLOOR_TOOLS,
        SAFE_HEADLESS_TOOLS,
        headless_ask_resolver,
    )

    assert "assign_work" in SAFE_HEADLESS_TOOLS
    assert "assign_work" not in DENY_FLOOR_TOOLS
    assert default_permissions()["assign_work"] == "ask"
    assert headless_ask_resolver()("assign_work", {}) is True
    engine = PermissionEngine(default_permissions(), ask_resolver=headless_ask_resolver())
    decision = engine.authorize("assign_work", {})
    assert decision.allowed is True, decision.reason
    # the control: a host-touching ask tool is still refused headless
    assert engine.authorize("shell", {}).allowed is False


def test_a_custom_agent_holding_assign_work_is_an_assignee_unless_it_delegates(platform, store):
    """The coordinator rule is keyed on ``delegate`` ONLY: holding
    ``assign_work`` does not make an agent a coordinator (it queues, it does
    not fan out now); holding ``delegate`` does, with or without it."""
    platform.agents_registry.register("queuer", "You queue follow-ups.", ["assign_work", "read_file"])
    rec, _ = store.create("queuer", "do the thing")
    assert rec.assignee == "custom:queuer"
    platform.agents_registry.register("boss2", "You coordinate.", ["assign_work", "delegate"])
    with pytest.raises(ValueError, match="can delegate work itself"):
        store.create("boss2", "run the team")


async def test_the_governor_holds_without_a_session_row_and_unlimited_runs(platform, store, dispatcher):
    """``max_concurrent_sessions`` = 1 with a FOREIGN session in the governed
    set: the tick holds with the slot sentence and creates NO session row.
    0 (unlimited) with the same foreign session: it runs."""
    from iron_jarvis.assignments.dispatcher import HELD_SLOTS

    platform.config.max_concurrent_sessions = 1
    platform.orchestrator._governed.add("someone-elses-run")
    rec, _ = store.create("builder", "wait your turn")
    result = await dispatcher.tick()
    assert result["held"] == 1 and result["started"] == []
    assert store.get(rec.id).held_reason == HELD_SLOTS
    assert _sessions(platform.engine) == [], "held = no session row was made"
    platform.config.max_concurrent_sessions = 0
    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()
    assert store.get(rec.id).status == "done" and len(_sessions(platform.engine)) == 1
