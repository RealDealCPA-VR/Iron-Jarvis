"""North-star plan 4.9 (D8): the 90-day clean-up says what it removes.

By default everything older than 90 days in ``EventRecord``,
``ToolInvocation``, ``AgentRun`` and ``UndoJournal`` was deleted daily, under a
setting described as "How long the activity log and ledger are kept". Still 90
days; now the description says exactly what goes, a run's final answer
(``AgentRun.result``) is KEPT (the row stays), and an aged run's transcript
says its details were removed after N days instead of reading empty.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import prune_events, session_scope
from iron_jarvis.core.ids import new_uid, utcnow
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    EventRecord,
    Session,
    ToolInvocation,
    UndoJournal,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings.schema import SETTINGS


def _aged_run(engine, *, days: int = 100) -> tuple[str, str]:
    old = utcnow() - timedelta(days=days)
    with session_scope(engine) as db:
        s = Session(task="the old task", created_at=old)
        db.add(s)
        db.commit()
        db.refresh(s)
        run = AgentRun(session_id=s.id, state=AgentState.COMPLETED, result="the final answer", created_at=old)
        db.add(run)
        db.commit()
        db.refresh(run)
        db.add(ToolInvocation(session_id=s.id, agent_run_id=run.id, tool="read_file", created_at=old))
        db.add(EventRecord(id=new_uid("evt"), type="session.created", session_id=s.id, created_at=old))
        db.add(UndoJournal(action_id=new_uid("tool"), session_id=s.id, agent_run_id=run.id, tool="write_file",
                           kind="file_delete", created_at=old))
        db.commit()
        return s.id, run.id


def test_an_aged_run_keeps_its_final_answer(platform):
    sid, run_id = _aged_run(platform.engine)

    prune_events(platform.engine, 90)

    with session_scope(platform.engine) as db:
        run = db.get(AgentRun, run_id)
        assert run is not None, "the run's final answer was deleted with its details"
        assert run.result == "the final answer"
        # …while its details went, as the setting says.
        assert list(db.exec(ToolInvocation.__table__.select().where(ToolInvocation.session_id == sid))) == []
        assert list(db.exec(EventRecord.__table__.select().where(EventRecord.session_id == sid))) == []
        assert list(db.exec(UndoJournal.__table__.select().where(UndoJournal.session_id == sid))) == []


async def test_an_aged_runs_transcript_says_its_details_were_removed(platform):
    platform.config.event_retention_days = 90
    sid, _run = _aged_run(platform.engine)
    orch = Orchestrator(platform)
    before = orch.transcript(sid)
    assert before["details_removed_after_days"] is None  # not removed yet

    prune_events(platform.engine, 90)

    after = orch.transcript(sid)
    assert after["details_removed_after_days"] == 90
    assert after["tools"] == []
    assert [r["result"] for r in after["runs"]] == ["the final answer"]


async def test_a_recent_run_never_says_so(platform):
    platform.config.event_retention_days = 90
    orch = Orchestrator(platform)
    s = await orch.create_session("new task")
    prune_events(platform.engine, 90)
    assert orch.transcript(s.id)["details_removed_after_days"] is None


def test_the_run_page_reads_it(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    sid, _run = _aged_run(p.engine)
    prune_events(p.engine, int(p.config.event_retention_days))

    body = client.get(f"/sessions/{sid}").json()

    assert body["transcript"]["details_removed_after_days"] == p.config.event_retention_days == 90


def test_the_setting_says_exactly_what_goes():
    row = next(s for s in SETTINGS if s.key == "event_retention_days")
    words = row.help.lower()
    for needed in ("steps", "details", "undo", "final answer", "kept"):
        assert needed in words, needed
    assert "ledger" not in words  # an engine word
