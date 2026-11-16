"""North-star plan 3.13 (v1.503.0): a team job's receipt includes its
workers' files.

``outcome.session_result`` read ONE session, and both the chat delivery
(``chat_delivery.delivery_result``) and a paired computer's reply
(``task_door._reply_files``) used it — so a lead that handed the writing to a
worker delivered a receipt (and sent a reply) without the file the worker
made. ``session_result(team=True)`` walks the delegation tree
(``mission._walk``) and adds every worker's documents
(``mission._team_documents``, the mission screen's own reading).
"""

from __future__ import annotations

import json
from pathlib import Path

from iron_jarvis.agents.chat_delivery import delivery_result
from iron_jarvis.agents.outcome import session_result
from iron_jarvis.agents.task_door import _reply_files
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, Session, SessionStatus, ToolInvocation, UndoJournal
from iron_jarvis.platform import build_platform


def _wrote(db, sid, run_id, path: Path, tool_id: str):
    db.add(ToolInvocation(id=tool_id, session_id=sid, agent_run_id=run_id, tool="write_file",
                          ok=True, reversibility="reversible"))
    db.add(UndoJournal(action_id=tool_id, session_id=sid, agent_run_id=run_id, tool="write_file",
                       kind="file_delete", pre_inline=json.dumps({"path": str(path)})))


def _team(tmp_path):
    """A lead (wrote notes.md) whose worker (a delegated child run) wrote
    report.md in its own folder."""
    p = build_platform(str(tmp_path / "home"))
    lead_ws, worker_ws = tmp_path / "lead", tmp_path / "worker"
    lead_ws.mkdir()
    worker_ws.mkdir()
    (lead_ws / "notes.md").write_text("notes", encoding="utf-8")
    (worker_ws / "report.md").write_text("the report", encoding="utf-8")
    with session_scope(p.engine) as db:
        db.add(Session(id="lead", task="write the report", status=SessionStatus.COMPLETED,
                       workspace_path=str(lead_ws)))
        db.add(AgentRun(id="lead-run", session_id="lead"))
        db.add(Session(id="worker", task="write report.md", status=SessionStatus.COMPLETED,
                       workspace_path=str(worker_ws)))
        db.add(AgentRun(id="worker-run", session_id="worker", parent_id="lead-run"))
        _wrote(db, "lead", "lead-run", lead_ws / "notes.md", "t-lead")
        _wrote(db, "worker", "worker-run", worker_ws / "report.md", "t-worker")
        db.commit()
    return p, lead_ws, worker_ws


def _names(paths):
    return sorted(Path(str(x)).name for x in paths)


def test_one_session_reads_its_own_files_only(tmp_path):
    p, _, _ = _team(tmp_path)
    assert _names(session_result(p.engine, "lead")["documents"]) == ["notes.md"]


def test_team_mode_adds_the_workers_files_after_the_leads(tmp_path):
    p, lead_ws, worker_ws = _team(tmp_path)
    docs = session_result(p.engine, "lead", team=True)["documents"]
    assert docs == [str((lead_ws / "notes.md").resolve()), str((worker_ws / "report.md").resolve())]


def test_a_solo_run_reads_the_same_in_team_mode(tmp_path):
    p, _, _ = _team(tmp_path)
    assert session_result(p.engine, "worker", team=True)["documents"] == \
        session_result(p.engine, "worker")["documents"]


def test_the_chat_receipt_carries_the_workers_files(tmp_path):
    p, _, _ = _team(tmp_path)
    assert _names(delivery_result(p.engine, "lead")["documents"]) == ["notes.md", "report.md"]


def test_a_paired_computers_reply_carries_the_workers_files(tmp_path):
    p, _, _ = _team(tmp_path)
    files, left = _reply_files(p.engine, "lead")
    assert sorted(f["name"] for f in files) == ["notes.md", "report.md"], (files, left)
