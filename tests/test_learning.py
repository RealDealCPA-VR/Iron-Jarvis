"""Self-correcting learning loop tests (§29). Fully offline — DB rows only."""

from __future__ import annotations

import pytest
from sqlmodel import select

import iron_jarvis.learning.models  # noqa: F401  (register tables before init_db)
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.events import EventBus
from iron_jarvis.learning.engine import LearningEngine
from iron_jarvis.learning.models import FeedbackRecord, LessonRecord
from iron_jarvis.learning.tools import RememberPreferenceTool, learning_tools
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def learning(engine):
    return LearningEngine(engine)


@pytest.fixture
def ctx(engine, tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


def _count(engine, model) -> int:
    with session_scope(engine) as db:
        return len(list(db.exec(select(model))))


def test_record_feedback_stores_record_and_distils_lesson(learning, engine):
    fb = learning.record_feedback("s1", "down", "Too verbose")

    assert isinstance(fb, FeedbackRecord)
    assert fb.session_id == "s1"
    assert fb.rating == "down"
    assert fb.comment == "Too verbose"

    # The down/comment feedback is distilled into a weight-3 feedback lesson.
    assert _count(engine, FeedbackRecord) == 1
    lessons = learning.lessons(scope=None)
    feedback_lessons = [l for l in lessons if l.source == "feedback"]
    assert len(feedback_lessons) == 1
    assert feedback_lessons[0].weight == 3
    assert "Too verbose" in feedback_lessons[0].text


def test_neutral_feedback_no_comment_distils_no_lesson(learning, engine):
    learning.record_feedback("s2", "up")  # positive, nothing to learn
    assert _count(engine, FeedbackRecord) == 1
    assert _count(engine, LessonRecord) == 0


def test_down_feedback_no_comment_distils_generic_lesson(learning):
    learning.record_feedback("s3", "down")
    lessons = learning.lessons(scope=None)
    assert len(lessons) == 1
    assert lessons[0].source == "feedback"
    assert "rejected" in lessons[0].text.lower()


def test_note_preference_creates_weight5_lesson(learning):
    rec = learning.note_preference("User prefers concise bullet-point summaries")
    assert rec.source == "preference"
    assert rec.weight == 5
    assert rec.scope == "user"
    assert "bullet-point" in rec.text


def test_reflect_on_failure_creates_lesson(learning):
    rec = learning.reflect("s1", task="ship the report", summary="", ok=False)
    assert rec is not None
    assert rec.source == "reflection"
    assert rec.weight == 1
    assert "ship the report" in rec.text
    assert "revisit" in rec.text.lower()


def test_reflect_on_empty_success_returns_none(learning):
    assert learning.reflect("s1", task="", summary="", ok=True) is None


def test_apply_to_prompt_injects_ordered_lessons(learning):
    learning.note_preference("User prefers concise bullet-point summaries")
    learning.record_feedback("s1", "down", "Too verbose")
    learning.reflect("s1", task="build a parser", summary="recursion worked", ok=True)

    out = learning.apply_to_prompt("BASE")

    assert "BASE" in out
    assert "What I've learned" in out
    assert "User prefers concise bullet-point summaries" in out

    # preference (weight 5) is injected before feedback (weight 3).
    pref_idx = out.index("bullet-point summaries")
    feedback_idx = out.index("Too verbose")
    assert pref_idx < feedback_idx
    # v1.279.0: a task REFLECTION is a note about a job, not knowledge about the
    # user — it stays in the store (for the Lessons tab and distillation) and
    # out of the prompt. On the daily driver 23 of 25 lessons were reflections
    # and six of the eight injected lines were "Worked well for '…'" rows.
    assert "build a parser" not in out
    assert any("build a parser" in l.text for l in learning.lessons(scope="user"))


def test_apply_to_prompt_unchanged_when_empty(learning):
    assert learning.apply_to_prompt("BASE") == "BASE"


def test_lessons_persist_across_fresh_engine(learning, engine):
    learning.note_preference("Always run tests before declaring done")

    fresh = LearningEngine(engine)
    lessons = fresh.lessons(scope="user")
    assert any("run tests" in l.text for l in lessons)


def test_feedback_for_returns_session_feedback(learning):
    learning.record_feedback("s1", "up", "nice")
    learning.record_feedback("s1", "down", "but slow")
    learning.record_feedback("other", "up")

    items = learning.feedback_for("s1")
    assert len(items) == 2
    assert {i.comment for i in items} == {"nice", "but slow"}


async def test_remember_preference_tool_stores_lesson(learning, ctx):
    tool = RememberPreferenceTool(learning)
    res = await tool.execute({"text": "User prefers dark mode mockups"}, ctx)

    assert res.ok
    lessons = learning.lessons(scope="user")
    assert any("dark mode" in l.text for l in lessons)
    assert lessons[0].source == "preference"
    assert lessons[0].weight == 5


async def test_learning_tools_exposes_both_tools(learning):
    names = {t.name for t in learning_tools(learning)}
    assert names == {"remember_preference", "recall_lessons"}
