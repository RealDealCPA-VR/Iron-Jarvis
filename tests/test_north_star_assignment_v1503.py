"""North-star plan 3.4 (v1.503.0, decision D12): a queued job shows how it
really ended.

``Orchestrator._post_run_assignment`` passed ``store.finish`` only "the run
did not raise", so a queued job whose run ended ``needs_you`` (an ask nobody
answered) or ``completed_with_failures`` (a change that did not land) was
marked DONE, said "Done" in the agent's inbox, and RESET its failure streak.

Now the row carries the ledger's verdict (``outcome``): the inbox says "Needs
you" / "Part failed" (dashboard/lib/statusWords), the reason is kept, and a
shortfall neither resets nor adds to the streak.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.assignments.dispatcher import AssignmentDispatcher
from iron_jarvis.assignments.store import as_dict
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import AgentState, EventRecord, Session, SessionStatus, ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.scheduling import run_outcome as RO


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.orchestrator = Orchestrator(p)
    return p


@pytest.fixture
def store(platform):
    return platform.assignments


@pytest.fixture
def dispatcher(platform):
    d = AssignmentDispatcher(platform, interval_s=0.05)
    platform.assignment_dispatcher = d
    return d


def _running(store, task="file the receipts", strikes=0):
    rec, _ = store.create("builder", task)
    store.claim_next("builder")
    store.start(rec.id, "sess-x")
    if strikes:
        def fn(row):
            row.failure_count = strikes
            return True

        store._mutate(rec.id, fn)
    return rec


@pytest.mark.parametrize("outcome", ["needs_you", "completed_with_failures", "did_nothing"])
def test_a_shortfall_is_kept_on_the_row_and_does_not_reset_the_streak(store, outcome):
    rec = _running(store, strikes=2)
    row = store.finish(rec.id, True, outcome=outcome)
    assert row.status == "done"
    assert row.outcome == outcome
    assert row.failure_count == 2, "a shortfall is not a clean run: the streak stays"
    assert row.last_error  # the reason, in words
    assert as_dict(row)["outcome"] == outcome


def test_a_clean_run_still_resets_the_streak(store):
    rec = _running(store, strikes=2)
    row = store.finish(rec.id, True, outcome="completed")
    assert row.status == "done" and row.failure_count == 0 and row.last_error == ""
    assert row.outcome == "completed"


def test_a_failed_run_still_strikes(store):
    rec = _running(store, strikes=1)
    row = store.finish(rec.id, False, "it broke")
    assert row.status == "failed" and row.failure_count == 2


def _partial_runtime(platform):
    async def run(session, agent_def, *a, **kw):
        with session_scope(platform.engine) as db:
            db.add(ToolInvocation(session_id=session.id, agent_run_id="r", tool="write_file",
                                  ok=False, reversibility="reversible", output="disk is full"))
            if "ASKED" in (session.task or ""):
                db.add(EventRecord(id=new_id("evt"), type=EventType.APPROVAL_RESOLVED,
                                   session_id=session.id,
                                   payload_json=json.dumps({"decision": "timeout"})))
            db.commit()
        return SimpleNamespace(state=AgentState.COMPLETED, provider=session.provider,
                               model=session.model, result="Filed the receipts.",
                               input_tokens=0, output_tokens=0)

    return run


async def test_the_hook_hands_the_verdict_to_the_row(platform, store, dispatcher, monkeypatch):
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _partial_runtime(platform))
    rec, _ = store.create("builder", "file the receipts")
    assert (await dispatcher.tick())["started"] == [rec.id]
    await dispatcher.drain()
    await platform.assignments.flush_events()
    row = store.get(rec.id)
    assert row.status == "done"
    assert row.outcome == "completed_with_failures"
    assert row.last_error.startswith(RO.PARTIAL_LEAD)
    with session_scope(platform.engine) as db:
        events = [json.loads(r.payload_json) for r in db.exec(
            __import__("sqlmodel").select(EventRecord).where(
                EventRecord.type == EventType.ASSIGNMENT_FINISHED))]
    assert len(events) == 1 and events[0]["outcome"] == "completed_with_failures"


async def test_the_sweep_hands_the_verdict_to_the_row(platform, store, dispatcher):
    rec, _ = store.create("builder", "stamped from outside")
    store.claim_next("builder")
    with session_scope(platform.engine) as db:
        db.add(Session(id="oob-1", task="t", status=SessionStatus.COMPLETED,
                       summary="done-ish", outcome="needs_you"))
        db.commit()
    store.start(rec.id, "oob-1")
    assert (await dispatcher.tick())["swept"] == 1
    row = store.get(rec.id)
    assert row.status == "done" and row.outcome == "needs_you"
