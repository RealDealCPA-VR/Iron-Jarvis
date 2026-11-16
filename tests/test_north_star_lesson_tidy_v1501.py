"""v1.501.0 north-star plan 1.7 and 1.9.

* 1.7 the daily lesson tidy-up (dedup, then condensing) leaves a record: ONE
  ``memory.updated`` event per pass (``kind: "lesson_tidy"``) naming each
  task note it merged away and each condensed suggestion it made. A pass that
  changed nothing publishes nothing. The daemon's daily loop runs that pass.
* 1.9 a low-trust run (a phone or other-computer task, or one that read
  flagged content) writes no task note (``reflect`` is skipped), as the coding
  lessons already skip a low-trust turn.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.learning import engine as LE
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.platform import build_platform

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _tidy_events(bus) -> list[dict]:
    return [
        e.payload for e in bus.history
        if e.type == EventType.MEMORY_UPDATED and (e.payload or {}).get("kind") == "lesson_tidy"
    ]


# --------------------------------------------------------------------------- #
# 1.7
# --------------------------------------------------------------------------- #


def test_a_tidy_pass_publishes_one_event_naming_what_it_merged(platform):
    learning = platform.learning
    learning.reflect("s1", task="fix the tests", summary="ran pytest", ok=True)
    learning.reflect("s2", task="fix the tests", summary="patched conftest", ok=True)
    with session_scope(platform.engine) as db:
        older = db.exec(select(LessonRecord).where(LessonRecord.text.contains("ran pytest"))).one()
        older_id = older.id

    res = asyncio.run(LE.tidy_pass(learning, event_bus=platform.event_bus))
    assert res["deduped"] == 1
    [ev] = _tidy_events(platform.event_bus)
    assert [m["id"] for m in ev["merged"]] == [older_id]
    assert "ran pytest" in ev["merged"][0]["text"]
    assert ev["merged"][0]["into"]  # the note it was merged into
    assert ev["suggested"] == []

    # Nothing changed: nothing published.
    asyncio.run(LE.tidy_pass(learning, event_bus=platform.event_bus))
    assert len(_tidy_events(platform.event_bus)) == 1


def test_a_tidy_pass_with_a_model_names_the_suggestions_it_made(platform):
    learning = platform.learning
    for i in range(6):
        learning.reflect(f"s{i}", task=f"task {i}", summary=f"note {i}", ok=True)

    async def complete(prompt: str) -> str:
        return json.dumps(["Break work into small verified steps."])

    asyncio.run(LE.tidy_pass(learning, event_bus=platform.event_bus, complete=complete))
    [ev] = _tidy_events(platform.event_bus)
    assert [s["text"] for s in ev["suggested"]] == ["Break work into small verified steps."]
    assert ev["suggested"][0]["id"]


def test_the_daily_loop_runs_the_recorded_pass():
    src = (ROOT / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    body = src[src.index("async def _lesson_compact_loop"):src.index("compact_task = asyncio.create_task")]
    assert "tidy_pass(" in body
    # The loop no longer deletes or rewrites rows outside the recorded pass.
    assert "learning.dedup" not in body and "learning.distill(" not in body


def test_the_distill_now_press_leaves_the_same_record(tmp_path):
    """The Memory page's press (``POST /lessons/compact``) removed rows with
    no record; it now goes through the same recorded pass as the daily loop."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.learning.reflect("s1", task="fix the tests", summary="ran pytest", ok=True)
    platform.learning.reflect("s2", task="fix the tests", summary="patched conftest", ok=True)

    r = TestClient(app).post("/lessons/compact", params={"distill": "false"})
    assert r.status_code == 200 and r.json()["deduped"] == 1
    [ev] = _tidy_events(platform.event_bus)
    assert ev["merged_count"] == 1 and "ran pytest" in ev["merged"][0]["text"]


# --------------------------------------------------------------------------- #
# 1.9
# --------------------------------------------------------------------------- #


def _finished(platform, *, trust: str) -> Session:
    sess = Session(
        task="answer the phone message", agent_type=AgentType.BUILDER,
        status=SessionStatus.COMPLETED, summary="Sent the totals.", trust=trust,
    )
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.commit()
        db.refresh(sess)
        db.expunge(sess)
    return sess


def _notes(platform) -> list[str]:
    with session_scope(platform.engine) as db:
        return [r.text for r in db.exec(select(LessonRecord).where(LessonRecord.source == "reflection"))]


def test_a_low_trust_run_writes_no_task_note(platform):
    orch = Orchestrator(platform)
    orch._post_run_learning(_finished(platform, trust="low"))
    assert _notes(platform) == []
    # Control: the same run at full trust writes one.
    orch._post_run_learning(_finished(platform, trust="full"))
    assert len(_notes(platform)) == 1


def test_a_run_lowered_after_the_object_was_read_writes_no_task_note(platform):
    """The row is the record: a run tainted mid-run whose object in hand is
    stale still counts as low trust."""
    orch = Orchestrator(platform)
    sess = _finished(platform, trust="full")
    with session_scope(platform.engine) as db:
        row = db.get(Session, sess.id)
        row.trust = "low"
        db.add(row)
        db.commit()
    orch._post_run_learning(sess)
    assert _notes(platform) == []


def test_only_the_reflect_call_site_changed_in_the_orchestrator():
    src = (ROOT / "src" / "iron_jarvis" / "agents" / "orchestrator.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    body = src[src.index("def _post_run_learning"):src.index("# Skill learning (v1.135.0)")]
    assert re.search(r"low.trust", body, re.IGNORECASE)


def test_two_notes_written_in_the_same_instant_merge_the_same_way_every_time(platform):
    """v1.510.0. On GitHub's Windows machines two notes can carry the SAME
    ``created_at`` (the clock steps in whole ticks), and the tidy-up then
    merged whichever the database happened to list first: the test above
    passed only on its retry. The note written later is the newer one."""
    from datetime import datetime, timezone

    learning = platform.learning
    learning.reflect("s1", task="fix the tests", summary="ran pytest", ok=True)
    learning.reflect("s2", task="fix the tests", summary="patched conftest", ok=True)
    same = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(LessonRecord)))
        assert len(rows) == 2
        for r in rows:
            r.created_at = same
            db.add(r)
        db.commit()
        older_id = next(r.id for r in rows if "ran pytest" in r.text)

    report: list[dict] = []
    assert learning.dedup(report=report) == 1
    assert [m["id"] for m in report] == [older_id], "the note written first was not the one merged away"
