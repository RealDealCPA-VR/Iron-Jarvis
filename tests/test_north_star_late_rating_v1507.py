"""North-star fix 7.4 (v1.507.0): a rating given after a run ended re-scores it.

``ImprovementEngine.record_outcome`` was idempotent per session: the first
call (at the run's end) wrote the outcome, and every later call returned at
once. A thumbs-down given on the run's page AFTER it finished was folded into
nothing, so the run's score, its agent's record and the lessons it was told
never heard it. Now a later rating re-scores the run: the outcome row takes
the new score, and the agent's and each lesson's sums move by the DIFFERENCE
(never a second count). A call that finds the same score changes nothing.

Real app and route, real engine; the evaluation row is written by hand.
"""

from __future__ import annotations

import iron_jarvis.eval.models  # noqa: F401
import iron_jarvis.improvement.models  # noqa: F401

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.models import Evaluation
from iron_jarvis.improvement.models import AgentStatRecord, LessonStatRecord, OutcomeRecord
from iron_jarvis.learning.models import LessonRecord


@pytest.fixture()
def scene(tmp_path):
    app = create_app(str(tmp_path / "d"))
    platform = app.state.platform
    lesson = platform.learning._add_lesson("Cite the source of each figure", source="feedback", weight=3)
    sess = Session(task="Sort the March photos by date", agent_type=AgentType.BUILDER,
                   status=SessionStatus.COMPLETED)
    sid = sess.id
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(Evaluation(session_id=sid, agent_run_id="r", completion=1.0, tool_success_rate=1.0))
        db.commit()
    first = platform.improvement.record_outcome(sid, lessons_applied=[lesson.id])
    assert first is not None and first.score == 1.0
    return TestClient(app), platform, sid, lesson.id


def _state(platform, sid, lid):
    with session_scope(platform.engine) as db:
        (out,) = list(db.exec(select(OutcomeRecord).where(OutcomeRecord.session_id == sid)))
        agent = db.get(AgentStatRecord, "builder")
        stat = db.get(LessonStatRecord, lid)
        return {
            "score": out.score, "success": out.success, "outcomes": len(list(db.exec(select(OutcomeRecord)))),
            "agent_runs": agent.session_count, "agent_sum": round(agent.score_sum, 4),
            "agent_ok": agent.success_count, "agent_recent": agent.recent_json,
            "lesson_applied": stat.applied_count, "lesson_sum": round(stat.score_sum, 4),
        }


def test_a_thumbs_down_after_the_run_ended_re_scores_it_once(scene):
    client, platform, sid, lid = scene
    before = _state(platform, sid, lid)
    assert (before["score"], before["agent_sum"], before["lesson_sum"]) == (1.0, 1.0, 1.0)
    r = client.post(f"/sessions/{sid}/feedback", json={"rating": "down", "comment": ""})
    assert r.status_code == 200, r.text
    after = _state(platform, sid, lid)
    assert after["score"] == 0.4  # a thumbs-down scales the run's score by 0.4
    assert after["outcomes"] == 1 and after["agent_runs"] == 1 and after["lesson_applied"] == 1  # no second count
    assert after["agent_sum"] == 0.4 and after["lesson_sum"] == 0.4  # moved by the difference
    assert after["agent_recent"] == "[0.4]"
    # The same rating again, or the run's end hook again: nothing moves twice.
    client.post(f"/sessions/{sid}/feedback", json={"rating": "down", "comment": ""})
    assert platform.improvement.record_outcome(sid) is None
    assert _state(platform, sid, lid) == after


def test_the_lessons_weight_follows_the_new_score(scene):
    client, platform, sid, lid = scene
    # A second run told the same lesson, so the lesson has enough samples.
    other = Session(task="t", agent_type=AgentType.BUILDER, status=SessionStatus.COMPLETED)
    with session_scope(platform.engine) as db:
        db.add(other)
        db.add(Evaluation(session_id=other.id, agent_run_id="r", completion=1.0, tool_success_rate=1.0))
        db.commit()
        oid = other.id
    platform.improvement.record_outcome(oid, lessons_applied=[lid])
    with session_scope(platform.engine) as db:
        assert db.get(LessonRecord, lid).weight_bonus == 0.0  # level with the baseline
    client.post(f"/sessions/{sid}/feedback", json={"rating": "down", "comment": ""})
    with session_scope(platform.engine) as db:
        stat = db.get(LessonStatRecord, lid)
        agent = db.get(AgentStatRecord, "builder")
        bonus = db.get(LessonRecord, lid).weight_bonus
    assert stat.applied_count == 2 and round(stat.score_sum, 4) == 1.4
    assert agent.session_count == 2 and round(agent.score_sum, 4) == 1.4
    assert bonus == 0.0  # its runs ARE the baseline here: re-derived, not left stale
    # A thumbs-up on the other run lifts it back by the difference only.
    client.post(f"/sessions/{oid}/feedback", json={"rating": "up", "comment": ""})
    with session_scope(platform.engine) as db:
        assert round(db.get(LessonStatRecord, lid).score_sum, 4) == 1.4  # 1.0 capped at 1.0: no change


def test_a_rating_on_a_run_with_no_outcome_yet_writes_none(tmp_path):
    client = TestClient(create_app(str(tmp_path / "d")))
    platform = client.app.state.platform
    r = client.post("/sessions/sess_unknown/feedback", json={"rating": "down", "comment": ""})
    assert r.status_code == 200
    with session_scope(platform.engine) as db:
        assert list(db.exec(select(OutcomeRecord))) == []  # the end hook writes the first one
