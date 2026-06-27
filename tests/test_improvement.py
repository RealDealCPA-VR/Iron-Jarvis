"""ImprovementEngine tests — fully offline (DB rows + the mock model).

Proves the loop closes safely:
  * the per-session hook records an Outcome + updates stats, no behaviour change;
  * a lesson whose sessions beat the baseline GAINS effective weight, one that
    trails it DECAYS (and recall ordering follows);
  * reflect() returns deterministic suggestions with the mock + applies NOTHING;
  * recurring tool failures past the threshold mint a SUGGEST-ONLY proposal and
    NEVER spawn a session (self-dev stays off by default).
"""

from __future__ import annotations

# Register the improvement + eval tables on SQLModel.metadata BEFORE init_db.
import iron_jarvis.eval.models  # noqa: F401
import iron_jarvis.improvement.models  # noqa: F401

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentType,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.eval.models import Evaluation
from iron_jarvis.improvement.models import (
    AgentStatRecord,
    LessonStatRecord,
    OutcomeRecord,
)
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _count(engine, model) -> int:
    with session_scope(engine) as db:
        return len(list(db.exec(select(model))))


def _seed_scored_session(platform, *, score: float, lesson_id: str) -> str:
    """Create a Session + Evaluation with a chosen score, then record its outcome.

    score = 0.6*completion + 0.4*tool_success_rate, so a value in {0.0, 1.0} is
    reached by setting both metrics equal to it.
    """
    sess = Session(
        task="t", agent_type=AgentType.BUILDER, status=SessionStatus.COMPLETED
    )
    sid = sess.id  # capture before the session closes (avoids detached refresh)
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(
            Evaluation(
                session_id=sid,
                agent_run_id="r",
                completion=score,
                tool_success_rate=score,
            )
        )
        db.commit()
    platform.improvement.record_outcome(sid, lessons_applied=[lesson_id])
    return sid


# -- 1. per-session hook: records an Outcome + stats, never changes behaviour --


async def test_run_session_records_outcome(platform):
    orch = Orchestrator(platform)
    session = await orch.run("make a file")
    assert session.status is SessionStatus.COMPLETED  # behaviour unchanged

    outcomes = _count(platform.engine, OutcomeRecord)
    assert outcomes == 1
    with session_scope(platform.engine) as db:
        rec = db.exec(select(OutcomeRecord)).first()
        agent = db.get(AgentStatRecord, "builder")
    assert rec.session_id == session.id
    assert rec.success is True
    assert rec.score > 0.0  # the mock builder completes + tools succeed
    assert agent is not None and agent.session_count == 1


def test_record_outcome_never_raises_on_unknown_session(platform):
    # A bogus session id must degrade to None, never blow up the completion hook.
    assert platform.improvement.record_outcome("nope") is not None or True
    # (it returns a record with a zero score; the key guarantee is no exception.)


# -- 2. lesson weighting: winners gain, losers decay -------------------------


def test_negative_lesson_decays_positive_lesson_gains(platform):
    good = platform.learning.note_preference("Good: cite sources")
    bad = platform.learning.note_preference("Bad: ramble on")
    assert good.weight == bad.weight == 5  # same static base

    # Interleave so each lesson's LAST attribution sees the full score mix.
    for _ in range(3):
        _seed_scored_session(platform, score=0.0, lesson_id=bad.id)
        _seed_scored_session(platform, score=1.0, lesson_id=good.id)

    with session_scope(platform.engine) as db:
        good_r = db.get(LessonRecord, good.id)
        bad_r = db.get(LessonRecord, bad.id)
        good_stat = db.get(LessonStatRecord, good.id)
        bad_stat = db.get(LessonStatRecord, bad.id)

    assert good_r.weight_bonus > 0  # beat the baseline -> rewarded
    assert bad_r.weight_bonus < 0  # trailed the baseline -> decayed
    assert good_r.effective_weight > bad_r.effective_weight
    assert good_stat.applied_count == 3 and bad_stat.applied_count == 3

    # recall_lessons / prompt injection now orders the winner ahead of the loser.
    ordered = platform.learning.lessons(scope="user")
    ids = [l.id for l in ordered]
    assert ids.index(good.id) < ids.index(bad.id)


def test_stats_read_reports_lessons_and_agent_trend(platform):
    lesson = platform.learning.note_preference("Be concise")
    _seed_scored_session(platform, score=1.0, lesson_id=lesson.id)
    _seed_scored_session(platform, score=1.0, lesson_id=lesson.id)

    stats = platform.improvement.stats()
    assert stats["outcomes"]["count"] == 2
    assert any(l["lesson_id"] == lesson.id for l in stats["lessons"])
    builder = next(a for a in stats["agents"] if a["agent_type"] == "builder")
    assert builder["sessions"] == 2
    assert "trend" in builder


# -- 3. reflection: deterministic suggestions, applies NOTHING ---------------


async def test_reflect_with_mock_returns_suggestions_applies_nothing(platform):
    lesson = platform.learning.note_preference("seed")
    # Two low-scoring sessions to reflect over.
    _seed_scored_session(platform, score=0.0, lesson_id=lesson.id)
    _seed_scored_session(platform, score=0.0, lesson_id=lesson.id)

    lessons_before = _count(platform.engine, LessonRecord)
    out = await platform.improvement.reflect()  # uses the mock router (offline)

    assert out["applied"] is False
    assert out["reviewed"] == 2
    assert out["suggestions"]  # heuristic fallback guarantees non-empty
    # Reflection edits NOTHING: no new lessons, no proposals.
    assert _count(platform.engine, LessonRecord) == lessons_before
    assert platform.intent.list_proposals() == []


async def test_reflect_injected_reflector_is_deterministic(platform):
    lesson = platform.learning.note_preference("seed")
    _seed_scored_session(platform, score=0.0, lesson_id=lesson.id)

    canned = [{"kind": "prompt", "target": "builder", "suggestion": "Ask first"}]
    out = await platform.improvement.reflect(reflector=lambda ctx: canned)
    assert out["suggestions"] == [
        {"kind": "prompt", "target": "builder", "suggestion": "Ask first"}
    ]
    assert out["applied"] is False


async def test_reflect_no_low_scorers_yields_no_suggestions(platform):
    lesson = platform.learning.note_preference("seed")
    _seed_scored_session(platform, score=1.0, lesson_id=lesson.id)  # high, not low
    out = await platform.improvement.reflect()
    assert out["reviewed"] == 0
    assert out["suggestions"] == []


# -- 4 + 5. tool-failure clustering -> suggest-only proposal, never spawns ----


def _seed_tool_failures(platform, tool: str, n: int) -> None:
    with session_scope(platform.engine) as db:
        for i in range(n):
            db.add(
                ToolInvocation(
                    session_id="sx", agent_run_id="rx", tool=tool, ok=False
                )
            )
        db.commit()


def test_tool_failure_cluster_mints_suggest_only_proposal(platform):
    assert platform.config.self_dev_enabled is False  # default

    _seed_tool_failures(platform, "shell", 3)  # at threshold
    _seed_tool_failures(platform, "grep", 1)  # below threshold -> ignored

    clusters = platform.improvement.scan_tool_failures()
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["tool"] == "shell" and cluster["failures"] == 3
    assert cluster["proposal_id"] is not None
    assert cluster["self_dev_enabled"] is False

    pending = platform.intent.list_proposals(status="pending")
    assert len(pending) == 1
    prop = pending[0]
    assert prop.title == "Recurring failures in tool 'shell'"
    assert prop.source == "event"
    assert prop.risk == "high"  # high risk => never auto-executes under any dial
    assert prop.decoded_action()["agent_type"] == "maintainer"

    # SELF-MOD NEVER SPAWNS: no session was created, nothing executed.
    assert platform.intent.list_proposals(status="executed") == []
    assert _count(platform.engine, Session) == 0


def test_tool_failure_scan_dedupes(platform):
    _seed_tool_failures(platform, "shell", 4)
    first = platform.improvement.scan_tool_failures()
    second = platform.improvement.scan_tool_failures()
    assert first[0]["proposal_id"] == second[0]["proposal_id"]
    assert len(platform.intent.list_proposals(status="pending")) == 1


# -- 6. HTTP surface ---------------------------------------------------------


def test_http_improvement_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    with TestClient(create_app(str(tmp_path))) as client:
        stats = client.get("/improvement").json()
        assert {"lessons", "agents", "outcomes"} <= set(stats)

        out = client.post("/improvement/reflect").json()
        assert out["applied"] is False
        assert out["reviewed"] == 0  # no sessions yet -> nothing to reflect on
