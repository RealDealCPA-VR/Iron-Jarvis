"""v1.501.0 north-star plan 1.8: every kept preference reaches the prompt.

``apply_to_prompt`` took the top 8 lessons by ``weight + weight_bonus``, and
the bonus came from outcomes credited to whatever ranked top at scoring time
(task notes included) rather than to what the run was actually told. Now:

* every kept preference (``source="preference"``, confirmed) is told,
  bounded by a CHARACTER budget, never a count of eight;
* the outcome bonus moves only lessons the owner did not state, and never
  reorders a stated one;
* an outcome is credited only to the lessons that were injected into THAT run.
"""

from __future__ import annotations

import iron_jarvis.eval.models  # noqa: F401
import iron_jarvis.improvement.models  # noqa: F401

import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.eval.models import Evaluation
from iron_jarvis.improvement.models import OutcomeRecord
from iron_jarvis.learning import engine as LE
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _set_bonus(engine, lesson_id: str, bonus: float) -> None:
    with session_scope(engine) as db:
        row = db.get(LessonRecord, lesson_id)
        row.weight_bonus = bonus
        db.add(row)
        db.commit()


def _scored(platform, score: float) -> str:
    sess = Session(task="t", agent_type=AgentType.BUILDER, status=SessionStatus.COMPLETED)
    sid = sess.id
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(Evaluation(session_id=sid, agent_run_id="r", completion=score, tool_success_rate=score))
        db.commit()
    return sid


def test_a_ninth_stated_preference_with_a_negative_bonus_still_reaches_the_prompt(platform):
    learning = platform.learning
    rows = [learning.note_preference(f"Stated preference number {i}") for i in range(9)]
    _set_bonus(platform.engine, rows[0].id, -4.0)
    # Feedback lessons with a big bonus cannot push a stated preference out.
    for i in range(8):
        fb = learning._add_lesson(f"Feedback lesson {i}", source="feedback", weight=3)
        _set_bonus(platform.engine, fb.id, 4.0)
    out = learning.apply_to_prompt("BASE")
    for i in range(9):
        assert f"Stated preference number {i}" in out, i
    # The ones the owner did not state still take up to `limit` (8) places.
    assert sum(f"Feedback lesson {i}" in out for i in range(8)) == 8


def test_the_lessons_block_is_bounded_by_characters(platform):
    learning = platform.learning
    for i in range(60):
        learning.note_preference(f"Preference {i:02d}: " + "word " * 40)
    out = learning.apply_to_prompt("")
    assert len(out) <= LE.LESSONS_BUDGET_CHARS + len(LE._LESSONS_HEADING)
    assert out.count("- Preference") >= 20  # far more than eight


def test_a_stated_preference_is_never_reordered_by_a_bonus(platform):
    learning = platform.learning
    stated = learning.note_preference("Cite sources")
    # A bonus written before this release (or by any path) is ignored for a
    # stated row: weight 5 stays ahead of a weight-4 feedback lesson.
    _set_bonus(platform.engine, stated.id, -4.0)
    feedback = learning._add_lesson("Feedback: check totals", source="feedback", weight=4)
    ids = [r.id for r in learning.lessons(scope="user")]
    assert ids.index(stated.id) < ids.index(feedback.id)


def test_the_bonus_moves_only_lessons_the_owner_did_not_state(platform):
    learning = platform.learning
    stated = learning.note_preference("Be concise")
    fb = learning._add_lesson("Feedback: be careful with dates", source="feedback", weight=3)
    for score in (1.0, 1.0, 0.0, 0.0):  # good runs first set the baseline
        sid = _scored(platform, score)
        platform.improvement.record_outcome(sid, lessons_applied=[stated.id, fb.id] if score == 0.0 else [])
    with session_scope(platform.engine) as db:
        assert db.get(LessonRecord, stated.id).weight_bonus == 0.0
        assert db.get(LessonRecord, fb.id).weight_bonus < 0.0


def test_an_outcome_is_credited_only_to_lessons_injected_into_that_run(platform):
    learning = platform.learning
    told = learning._add_lesson("Feedback: show the working", source="feedback", weight=3)
    learning.reflect("sess_other", task="other work", summary="did other work", ok=True)
    sid = _scored(platform, 1.0)
    learning.apply_to_prompt("BASE", session_id=sid)
    # A lesson added AFTER the run was told its lessons was never injected.
    later = learning._add_lesson("Feedback: added later", source="feedback", weight=3)
    platform.improvement.record_outcome(sid)
    with session_scope(platform.engine) as db:
        rec = db.exec(select(OutcomeRecord).where(OutcomeRecord.session_id == sid)).first()
        reflection_ids = [r.id for r in db.exec(select(LessonRecord).where(LessonRecord.source == "reflection"))]
    applied = json.loads(rec.lessons_applied)
    assert applied == [told.id]
    assert later.id not in applied and not set(reflection_ids) & set(applied)


def test_a_run_that_was_told_no_lessons_credits_none(platform):
    learning = platform.learning
    learning._add_lesson("Feedback: show the working", source="feedback", weight=3)
    sid = _scored(platform, 1.0)  # skip_memory run: apply_to_prompt never called
    platform.improvement.record_outcome(sid)
    with session_scope(platform.engine) as db:
        rec = db.exec(select(OutcomeRecord).where(OutcomeRecord.session_id == sid)).first()
    assert json.loads(rec.lessons_applied) == []
