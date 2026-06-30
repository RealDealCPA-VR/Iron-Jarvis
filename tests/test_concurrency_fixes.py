"""Concurrency / data-integrity regression tests (the concurrency-lens fixes).

These actually exercise concurrent access (threads + asyncio.gather) to lock in the
fixes for: double-approve double-execution (H1), the config.toml write race (M1),
and the rolling-stats lost-update (M2). Offline (mock provider).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.config import _read_toml, persist_config_values
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType
from iron_jarvis.improvement.models import AgentStatRecord
from iron_jarvis.motivation import IntentEngine
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


@pytest.fixture
def engine(platform):
    eng = IntentEngine(platform, orchestrator=Orchestrator(platform))
    platform.intent = eng
    return eng


# --- H1: concurrent Approve runs the proposal exactly once --------------------


async def test_concurrent_approve_runs_proposal_once(platform, engine):
    platform.config.autonomy_enabled = True  # level stays "suggest" → a proposal, no auto-run
    engine.add_goal("do a concurrency-safe thing", priority=5)
    out = await engine.deliberate()
    pid = out["proposal_id"]
    goal_id = engine.get_proposal(pid).goal_id

    # Two simultaneous approvals of the SAME proposal (double-click / retry / 2 tabs).
    results = await asyncio.gather(
        engine.approve(pid, wait=True),
        engine.approve(pid, wait=True),
        return_exceptions=True,
    )
    ran = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(ran) == 1, "exactly one approval should have executed"
    assert len(errs) == 1 and isinstance(errs[0], ValueError)
    # Budget booked exactly once, not twice.
    assert engine.get_goal(goal_id).actions_taken == 1


# --- M1: concurrent config.toml writes lose no keys and never 500 -------------


def test_concurrent_config_writes_preserve_all_keys(tmp_path):
    errors: list[Exception] = []

    def writer(i: int) -> None:
        try:
            persist_config_values(tmp_path, {f"k{i}": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors}"
    doc = _read_toml(tmp_path / "config.toml")
    # No lost update: every writer's key survived the read-modify-write race.
    assert all(doc.get(f"k{i}") == i for i in range(24))
    assert not list((tmp_path).glob("config.toml.*tmp*"))  # temps cleaned up


# --- M2: rolling-stats increment loses nothing under concurrent record_outcome -


def test_record_outcome_no_lost_increment_under_threads(platform):
    from sqlmodel import select

    from iron_jarvis.improvement.models import OutcomeRecord

    orch = Orchestrator(platform)
    K = 8
    # Distinct builder sessions (the real lost-update race is across different
    # sessions sharing ONE AgentStatRecord, not the same session — record_outcome
    # is idempotent per-session).
    sids = [asyncio.run(orch.run(f"task {i}", AgentType.BUILDER)).id for i in range(K)]

    # Clear their auto-recorded outcomes so we can re-record all K concurrently,
    # then capture the (already-incremented) baseline.
    with session_scope(platform.engine) as db:
        for o in db.exec(select(OutcomeRecord)):
            db.delete(o)
        db.commit()
        base = db.get(AgentStatRecord, "builder").session_count

    def rec(sid: str) -> None:
        platform.improvement.record_outcome(sid)

    threads = [threading.Thread(target=rec, args=(s,)) for s in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with session_scope(platform.engine) as db:
        final = db.get(AgentStatRecord, "builder").session_count
    assert final - base == K  # every increment landed (no read-modify-write loss)
