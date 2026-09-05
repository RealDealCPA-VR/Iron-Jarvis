"""Wave 1 (v1.227.0), lane BE-2 — the truth about a run: ``Session.outcome``,
``unanswered_asks``, ``waiting_on``, and the hand-off bubble checked against
the ledger.

Converted from the 2026-09-04 audit reproductions (``test_q4_outcome_honesty``,
``test_approvals_lane_audit::test_A4``). Live fact behind them: session_7e56
had 24 ``rename_real_file`` calls, 24 failed (every ask timed out), files
changed [], status COMPLETED — and the result card headlined "Task complete".
And the escalated hand-off bubble relayed "I saved report.docx" verbatim while
the card beneath it said nothing was written.

Each test asserts a value the fix produces and HEAD did not:

* ``Session.outcome`` is an additive column the boot reconcile heals;
* ``derive_outcome`` — needs_you beats everything, completed_with_failures
  needs a MUTATING failure, a failed read stays ``completed``, a FAILED /
  ACTIVE / unknown session is None;
* ``unanswered_asks`` is counted from the persisted ``approval.resolved``
  events with ``decision == "timeout"``;
* a real run whose only ask timed out is ``needs_you`` on the row, on
  ``GET /sessions/{id}/result`` and on ``GET /sessions/{id}.session``;
* every serialised session row carries ``outcome`` and ``waiting_on``, the
  latter derived from ``approvals.pending_for`` and None when that seam is
  absent (the approvals lane adds it in the same release);
* the agent lane applies ``_claimed_write_note`` to ``session.summary`` and
  leaves a summary alone when the file really was written.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.outcome import (
    OUTCOME_COMPLETED,
    OUTCOME_NEEDS_YOU,
    OUTCOME_WITH_FAILURES,
    derive_outcome,
    did_nothing,
    session_outcome,
    session_result,
)
from iron_jarvis.core.db import _reconcile_additive_columns, init_db, make_engine, session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, EventRecord, Session as SessionRow, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _claimed_write_note
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter


# --------------------------------------------------------------------------- #
# Scripting helpers (the shape tests/test_multiagent.py uses).
# --------------------------------------------------------------------------- #
def call(name: str, args: dict, i: int = 1) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args)], finish_reason="tool_use"
    )


def final(text_: str) -> LLMResponse:
    return LLMResponse(text=text_, finish_reason="stop")


def script(platform, responses) -> None:
    platform.providers.register(
        "mock", lambda model=None: MockLLMAdapter(script=list(responses))
    )


def _shell_then_done():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c0", name="shell", arguments={"command": "mv a b"}),
            ])
        else:
            resp = LLMResponse(text="Task incomplete — 0 of 1 renamed, pending your approval.")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    return SimpleNamespace(app=app, platform=app.state.platform, client=TestClient(app))


# --------------------------------------------------------------------------- #
# The column.
# --------------------------------------------------------------------------- #
def test_session_outcome_is_an_additive_column_the_boot_reconcile_heals(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    init_db(engine)
    with engine.begin() as conn:  # an install from before the column existed
        conn.execute(text('ALTER TABLE "session" DROP COLUMN "outcome"'))
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("session")')).all()}
    assert "outcome" not in cols

    _reconcile_additive_columns(engine)

    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("session")')).all()}
    assert "outcome" in cols
    with session_scope(engine) as db:  # and the healed table round-trips the value
        db.add(SessionRow(id="s-old", task="t", outcome=OUTCOME_NEEDS_YOU))
        db.commit()
        assert db.get(SessionRow, "s-old").outcome == OUTCOME_NEEDS_YOU


# --------------------------------------------------------------------------- #
# The rule, pure.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "result, status, expected",
    [
        ({"found": False}, None, None),
        ({"found": True, "status": "completed"}, None, OUTCOME_COMPLETED),
        ({"found": True, "status": "completed", "tools_failed_mutating": 1}, None,
         OUTCOME_WITH_FAILURES),
        ({"found": True, "status": "completed", "tools_failed_mutating": 1,
          "unanswered_asks": 1}, None, OUTCOME_NEEDS_YOU),
        ({"found": True, "status": "failed", "unanswered_asks": 2}, None, OUTCOME_NEEDS_YOU),
        ({"found": True, "status": "failed"}, None, None),
        ({"found": True, "status": "active"}, None, None),
        ({"found": True, "status": "cancelled"}, None, None),
        # the orchestrator's explicit status wins over the row's stale one
        ({"found": True, "status": "active"}, SessionStatus.COMPLETED, OUTCOME_COMPLETED),
        ({"found": True, "status": "completed"}, SessionStatus.FAILED, None),
    ],
)
def test_derive_outcome_rules(result, status, expected):
    assert derive_outcome(result, status) == expected


# --------------------------------------------------------------------------- #
# The count comes from the event log.
# --------------------------------------------------------------------------- #
def _event(sid: str, type_: str, payload: dict, n: int) -> EventRecord:
    return EventRecord(id=f"ev-{n}", type=type_, session_id=sid, payload_json=json.dumps(payload))


def test_unanswered_asks_are_counted_from_the_persisted_approval_events(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    with session_scope(p.engine) as db:
        db.add(SessionRow(id="s-asks", task="rename", status=SessionStatus.COMPLETED))
        db.add(SessionRow(id="s-other", task="rename", status=SessionStatus.COMPLETED))
        db.add(_event("s-asks", EventType.APPROVAL_RESOLVED, {"decision": "timeout"}, 1))
        db.add(_event("s-asks", EventType.APPROVAL_RESOLVED, {"decision": "timeout"}, 2))
        db.add(_event("s-asks", EventType.APPROVAL_RESOLVED, {"decision": "once"}, 3))
        db.add(_event("s-asks", EventType.APPROVAL_REQUESTED, {"tool": "shell"}, 4))
        db.add(_event("s-other", EventType.APPROVAL_RESOLVED, {"decision": "timeout"}, 5))
        db.add(_event("s-asks", EventType.APPROVAL_RESOLVED, {}, 6))
        db.commit()

    out = session_result(p.engine, "s-asks")
    assert out["unanswered_asks"] == 2, "timeouts only, this session only"
    assert out["outcome"] == OUTCOME_NEEDS_YOU, "an old row with no stored verdict is derived live"
    assert session_result(p.engine, "s-other")["unanswered_asks"] == 1
    assert session_outcome(p.engine, "s-other", SessionStatus.FAILED) == OUTCOME_NEEDS_YOU


def test_a_stored_outcome_wins_over_the_live_derivation(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    with session_scope(p.engine) as db:
        db.add(SessionRow(id="s-stored", task="t", status=SessionStatus.COMPLETED,
                          outcome=OUTCOME_WITH_FAILURES))
        db.commit()
    assert session_result(p.engine, "s-stored")["outcome"] == OUTCOME_WITH_FAILURES


# --------------------------------------------------------------------------- #
# A4/A5 — a real run whose only ask timed out.
# --------------------------------------------------------------------------- #
async def test_a_timed_out_ask_marks_the_session_needs_you_everywhere(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.3)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")

    done = await orch.run_session(sess.id)

    assert done.status is SessionStatus.COMPLETED, "the RUN ended cleanly…"
    assert done.outcome == OUTCOME_NEEDS_YOU, "…but the JOB is waiting on the user"
    assert orch.get_session(sess.id).outcome == OUTCOME_NEEDS_YOU, "persisted"

    result = rt.client.get(f"/sessions/{sess.id}/result").json()
    assert result["status"] == "completed"
    assert result["outcome"] == OUTCOME_NEEDS_YOU
    assert result["unanswered_asks"] == 1
    assert result["tools_failed"] == [{"tool": "shell", "count": 1}]

    detail = rt.client.get(f"/sessions/{sess.id}").json()["session"]
    assert detail["outcome"] == OUTCOME_NEEDS_YOU
    assert detail["waiting_on"] is None, "the ask is over; nothing is pending now"
    rows = rt.client.get("/sessions").json()["sessions"]
    mine = next(r for r in rows if r["id"] == sess.id)
    assert mine["outcome"] == OUTCOME_NEEDS_YOU and "waiting_on" in mine
    assert mine["status"] == "completed", "additive: the base row is untouched"


async def test_a_failed_mutating_call_marks_completed_with_failures(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    script(p, [call("edit_file", {"path": "missing.txt", "old": "a", "new": "b"}), final("Done.")])
    s = await orch.create_session("fix the typo", AgentType.BUILDER, origin="chat")

    row = await orch.run_session(s.id)

    assert row.status is SessionStatus.COMPLETED
    res = session_result(p.engine, s.id)
    assert res["tools_failed"] == [{"tool": "edit_file", "count": 1}]
    assert res["tools_failed_mutating"] == 1 and res["unanswered_asks"] == 0
    assert row.outcome == OUTCOME_WITH_FAILURES
    assert res["outcome"] == OUTCOME_WITH_FAILURES


async def test_a_failed_read_leaves_the_outcome_completed(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    script(p, [call("read_file", {"path": "missing.txt"}), final("Nothing to do.")])
    s = await orch.create_session("look around", AgentType.BUILDER, origin="chat")

    row = await orch.run_session(s.id)

    res = session_result(p.engine, s.id)
    assert res["tools_failed"] == [{"tool": "read_file", "count": 1}], "the read DID fail…"
    assert res["tools_failed_mutating"] == 0, "…but a failed read is a detour, not lost work"
    assert row.outcome == OUTCOME_COMPLETED and res["outcome"] == OUTCOME_COMPLETED


async def test_a_run_that_did_the_work_is_plainly_completed(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    script(p, [call("write_file", {"path": "RESULT.md", "content": "hello"}), final("Done.")])
    s = await orch.create_session("write it up", AgentType.BUILDER)

    row = await orch.run_session(s.id)

    assert row.status is SessionStatus.COMPLETED and row.outcome == OUTCOME_COMPLETED
    assert row.summary == "Done.", "a real write gets no honesty note"
    res = session_result(p.engine, s.id)
    assert res["files_created"] == ["RESULT.md"] and res["files_created_total"] == 1
    assert res["documents"] == [str((Path(s.workspace_path) / "RESULT.md").resolve())]
    assert res["revertable"] == 1 and did_nothing(res) is False
    assert res["outcome"] == OUTCOME_COMPLETED and res["unanswered_asks"] == 0


async def test_a_crashed_run_still_gets_an_honest_verdict(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    with session_scope(p.engine) as db:  # an ask the clock answered, then the crash
        db.add(_event(s.id, EventType.APPROVAL_RESOLVED, {"decision": "timeout"}, 1))
        db.commit()

    async def boom(session, agent_def):
        raise RuntimeError("provider blew up")

    orch.runtime.run = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await orch.run_session(s.id)

    row = orch.get_session(s.id)
    assert row.status is SessionStatus.FAILED and row.outcome == OUTCOME_NEEDS_YOU


# --------------------------------------------------------------------------- #
# RT5 — the hand-off bubble is checked against the ledger.
# --------------------------------------------------------------------------- #
async def test_the_agent_lane_flags_a_file_the_model_never_wrote(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    claim = "I saved the summary as report.docx in your folder."
    script(p, [final(claim)])
    s = await orch.create_session("summarize the notes", AgentType.BUILDER, origin="chat")

    row = await orch.run_session(s.id)

    assert row.status is SessionStatus.COMPLETED
    res = session_result(p.engine, s.id)
    assert res["files_created"] == [] and res["documents"] == [] and did_nothing(res)
    assert not (Path(s.workspace_path) / "report.docx").exists()
    note = _claimed_write_note(claim, [t["tool"] for t in res["tools_used"]])
    assert note, "the chat-lane checker flags this sentence"
    assert row.summary == claim + note, "and the agent lane now appends the SAME note"
    assert "nothing was written to disk" in row.summary and "report.docx" in row.summary
    assert orch.get_session(s.id).summary == row.summary, "persisted — the bubble reads the row"


# --------------------------------------------------------------------------- #
# A4 (route half) — every session row carries waiting_on.
# --------------------------------------------------------------------------- #
def test_session_rows_carry_waiting_on_from_the_approvals_registry(rt, monkeypatch):
    with session_scope(rt.platform.engine) as db:
        db.add(SessionRow(id="s-paused", task="rename", status=SessionStatus.ACTIVE))
        db.add(SessionRow(id="s-idle", task="rename", status=SessionStatus.ACTIVE))
        db.commit()

    def pending_for(session_id):
        if session_id == "s-paused":
            return [
                {"approval_id": "apr_first", "tool": "shell", "args": {"command": "mv a b"},
                 "requested_at": 1.0},
                {"approval_id": "apr_second", "tool": "rename_file", "args": {},
                 "requested_at": 2.0},
            ]
        return []

    monkeypatch.setattr(rt.platform.approvals, "pending_for", pending_for, raising=False)

    detail = rt.client.get("/sessions/s-paused").json()["session"]
    assert detail["waiting_on"] == {"approval_id": "apr_first", "tool": "shell"}
    assert "args" not in detail["waiting_on"], "arguments never ride a listing row"
    assert rt.client.get("/sessions/s-idle").json()["session"]["waiting_on"] is None
    rows = {r["id"]: r for r in rt.client.get("/sessions").json()["sessions"]}
    assert rows["s-paused"]["waiting_on"]["approval_id"] == "apr_first"
    assert rows["s-idle"]["waiting_on"] is None
    assert rows["s-paused"]["outcome"] is None, "an unfinished run has no verdict yet"

    # The seam is guarded: without ``pending_for`` (a build before the
    # approvals lane lands) every row answers None instead of raising.
    monkeypatch.delattr(rt.platform.approvals, "pending_for", raising=False)
    assert rt.client.get("/sessions/s-paused").json()["session"]["waiting_on"] is None
    assert rt.client.get("/sessions/s-paused").status_code == 200


async def test_waiting_on_reads_the_real_approvals_registry(rt):
    """The same derivation over the REAL seam (``ChatApprovals.pending_for``,
    added by the approvals lane in this release): file two asks for one
    session, and the row names the OLDEST; answering it moves on to the next;
    a chat-lane ask (no session id) is never attributed to a run."""
    approvals = rt.platform.approvals
    if not hasattr(approvals, "pending_for"):
        pytest.skip("ChatApprovals.pending_for not in this tree yet (approvals lane)")
    with session_scope(rt.platform.engine) as db:
        db.add(SessionRow(id="s-live", task="rename", status=SessionStatus.ACTIVE))
        db.commit()
    first_id, first_fut = approvals.request("shell", {"command": "mv a b"}, session_id="s-live")
    second_id, second_fut = approvals.request("rename_file", {}, session_id="s-live")
    approvals.request("shell", {"command": "ls"})  # chat lane: no session
    try:
        row = rt.client.get("/sessions/s-live").json()["session"]
        assert row["waiting_on"] == {"approval_id": first_id, "tool": "shell"}
        assert approvals.resolve(first_id, "once")
        approvals.pop(first_id)
        row = rt.client.get("/sessions/s-live").json()["session"]
        assert row["waiting_on"] == {"approval_id": second_id, "tool": "rename_file"}
    finally:
        for aid in list(approvals.pending_ids()):
            approvals.pop(aid)
    assert rt.client.get("/sessions/s-live").json()["session"]["waiting_on"] is None
