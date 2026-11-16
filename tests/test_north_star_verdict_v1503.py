"""North-star plan, group 3 (v1.503.0): ONE verdict helper, a run that did
nothing, and the false-save check on writes that landed.

* 3.1 ``outcome.run_ending``: a finished session's TRUE ending read from its
  ledger verdict (``Session.outcome``) — status, outcome, ok, the badge word
  (``dashboard/lib/statusWords.ts``) and one plain sentence.
* 3.11 an UNATTENDED run (a schedule, a queued job, a phone task) that made
  no tool call is not "done": its verdict is ``did_nothing`` and the phone
  and the Schedules row say so. A chat-like attended run that only answered,
  and a workflow step (whose text feeds the next step), keep "completed".
* 3.7 the claimed-write check at finalize is fed only the calls that
  SUCCEEDED in this run: a declined ``write_file`` followed by "I saved
  report.docx" is marked; a real save is not.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator, _completion_payload
from iron_jarvis.agents.outcome import (
    OUTCOME_COMPLETED,
    OUTCOME_DID_NOTHING,
    OUTCOME_NEEDS_YOU,
    OUTCOME_WITH_FAILURES,
    derive_outcome,
    run_ending,
    session_result,
)
from iron_jarvis.comm import notifier as N
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentState, SessionStatus, ToolInvocation
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.platform import build_platform
from iron_jarvis.scheduling import run_outcome as RO

_BASE = dt.datetime(2026, 11, 15, 9, 0, 0)


def _rows(p, sid, origin, calls, *, run_id="r1"):
    """A finished session with *calls*: (tool, ok, reversibility, output)."""
    with session_scope(p.engine) as db:
        db.add(SessionRow(id=sid, task="t", status=SessionStatus.COMPLETED, origin=origin,
                          workspace_path=""))
        for i, (tool, ok, rev, out) in enumerate(calls):
            db.add(ToolInvocation(session_id=sid, agent_run_id=run_id, tool=tool, ok=ok,
                                  reversibility=rev, output=out,
                                  created_at=_BASE + dt.timedelta(seconds=i)))
        db.commit()
    with session_scope(p.engine) as db:
        return db.get(SessionRow, sid)


# --------------------------------------------------------------- 3.1 helper --


@pytest.mark.parametrize(
    ("status", "outcome", "ok", "words"),
    [
        ("completed", OUTCOME_COMPLETED, True, "Done"),
        ("completed", None, True, "Done"),  # no verdict to read (an old row)
        ("completed", OUTCOME_NEEDS_YOU, False, "Needs you"),
        ("completed", OUTCOME_WITH_FAILURES, False, "Part failed"),
        ("completed", OUTCOME_DID_NOTHING, False, "Did nothing"),
        ("failed", None, False, "Failed"),
        ("failed", OUTCOME_NEEDS_YOU, False, "Failed"),  # a failed run says Failed
        ("cancelled", None, False, "Stopped"),
        (SessionStatus.COMPLETED, OUTCOME_NEEDS_YOU, False, "Needs you"),  # an enum reads too
    ],
)
def test_run_ending_reads_the_verdict_not_the_status(status, outcome, ok, words):
    end = run_ending(status, outcome)
    assert end.ok is ok
    assert end.words == words
    assert end.status == str(getattr(status, "value", status))
    assert end.outcome == outcome
    if ok:
        assert end.reason == ""
    else:
        assert end.reason  # a shortfall always says why, in a sentence
        assert end.reason.endswith(".")


def test_run_ending_reasons_are_the_schedule_rows_own_words():
    """One set of sentences: the Schedules row's leads (run_outcome)."""
    assert run_ending("completed", OUTCOME_NEEDS_YOU).reason == RO.NEEDS_YOU_LEAD
    assert run_ending("completed", OUTCOME_WITH_FAILURES).reason == RO.PARTIAL_LEAD
    assert run_ending("completed", OUTCOME_DID_NOTHING).reason == RO.DID_NOTHING_LEAD


def test_run_ending_of_a_session_object():
    row = SimpleNamespace(status=SessionStatus.COMPLETED, outcome=OUTCOME_WITH_FAILURES)
    assert run_ending(row).words == "Part failed" and run_ending(row).ok is False


# ------------------------------------------------------- 3.11 did nothing --


@pytest.mark.parametrize(
    "origin",
    ["schedule:nightly", "assignment:asg_1", "goal:goal_1", "reflex:inbox", "autonomy"],
)
def test_an_unattended_run_with_no_tool_call_did_nothing(origin):
    result = {"found": True, "status": "completed", "tools_used": [], "unanswered_asks": 0}
    assert derive_outcome(result, origin=origin) == OUTCOME_DID_NOTHING


@pytest.mark.parametrize(
    "origin",
    # Attended: a person is reading the answer (chat, a mission, a project
    # task, an untagged run a person started); a workflow step's text feeds
    # the next step; a self-development run was started by a person. A phone
    # task and a task from another computer answer the person or chat that
    # asked: a question answered in words is the job, and no bell rings for it.
    [None, "", "chat", "job:mission", "job:mission-member", "project:p1", "user",
     "workflow:weekly", "self_dev", "comm:telegram", "remote:office-pc"],
)
def test_an_attended_run_that_only_answered_is_still_done(origin):
    result = {"found": True, "status": "completed", "tools_used": [], "unanswered_asks": 0}
    assert derive_outcome(result, origin=origin) == OUTCOME_COMPLETED


def test_a_run_that_used_a_tool_or_was_asked_is_not_did_nothing():
    used = {"found": True, "status": "completed", "tools_used": [{"tool": "read_file", "count": 1}]}
    assert derive_outcome(used, origin="schedule:x") == OUTCOME_COMPLETED
    asked = {"found": True, "status": "completed", "tools_used": [], "unanswered_asks": 1}
    assert derive_outcome(asked, origin="schedule:x") == OUTCOME_NEEDS_YOU
    failed = {"found": True, "status": "failed", "tools_used": []}
    assert derive_outcome(failed, origin="schedule:x") is None


def test_finalize_stores_did_nothing_for_a_schedule_run_with_an_empty_ledger(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    row = _rows(p, "s1", "schedule:morning", [])
    assert orch._settle_finished_run(row, "", SessionStatus.COMPLETED)[1] == OUTCOME_DID_NOTHING
    # A chat-started run that only answered is done.
    row = _rows(p, "c1", "chat", [])
    assert orch._settle_finished_run(row, "", SessionStatus.COMPLETED)[1] == OUTCOME_COMPLETED


def test_session_result_derives_it_live_for_a_row_with_no_stored_verdict(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _rows(p, "s2", "assignment:asg_9", [])
    assert session_result(p.engine, "s2")["outcome"] == OUTCOME_DID_NOTHING


def test_the_schedule_row_and_its_notice_say_it_did_nothing():
    status = RO.fire_status(OUTCOME_DID_NOTHING)
    assert status == RO.STATUS_DID_NOTHING
    assert RO.delivery_head(status) == "finished without doing anything"
    detail = RO.short_detail(status, "Here is a poem about autumn.")
    assert detail.startswith(RO.DID_NOTHING_LEAD)
    assert "Here is a poem about autumn." in detail
    # Not a failure and not counted as "needs a look" (the dashboard's twin
    # list is pinned equal to these).
    assert status not in RO.SHORT_STATUSES


def test_the_phone_line_says_it_did_nothing():
    line = N._session_line({"status": "completed", "outcome": OUTCOME_DID_NOTHING,
                            "origin": "schedule:morning", "summary": "Hello."})
    assert line.startswith("Your schedule morning finished without doing anything. Check what it said")
    # A run that did its job still reads "finished".
    done = N._session_line({"status": "completed", "outcome": OUTCOME_COMPLETED,
                            "origin": "schedule:morning", "summary": "Hello."})
    assert done.startswith("Your schedule morning finished —")


def test_completion_payload_carries_the_verdict():
    s = SimpleNamespace(status=SessionStatus.COMPLETED, summary="x", origin="schedule:a",
                        project_id=None, outcome=OUTCOME_DID_NOTHING)
    assert _completion_payload(s)["outcome"] == OUTCOME_DID_NOTHING


# ------------------------------------------- 3.7 writes that really landed --

_DECLINED = "Permission denied: the user declined write_file."


async def _settle_child(p, sid, result_text):
    orch = Orchestrator(p)
    with session_scope(p.engine) as db:
        child = db.get(SessionRow, sid)
    run = SimpleNamespace(
        id="r1", state=AgentState.COMPLETED, result=result_text, provider="mock",
        model="m", input_tokens=0, output_tokens=0, cost_usd=0.0,
    )
    stopped = await orch.settle_child(child, run)
    assert stopped is False
    return child


async def test_a_declined_write_followed_by_a_save_claim_is_marked(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _rows(p, "d1", None, [("write_file", False, "reversible", _DECLINED)])
    child = await _settle_child(p, "d1", "Done. I saved report.docx in your folder.")
    assert child.summary.startswith("Done. I saved report.docx in your folder.")
    assert len(child.summary) > len("Done. I saved report.docx in your folder."), child.summary


async def test_a_real_save_is_not_marked(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _rows(p, "d2", None, [("write_file", True, "reversible", "Wrote report.docx")])
    child = await _settle_child(p, "d2", "Done. I saved report.docx in your folder.")
    assert child.summary == "Done. I saved report.docx in your folder."


async def test_a_run_that_says_it_sent_something_it_never_sent_is_marked(tmp_path):
    """v1.503.0 (north-star 3.10): a finished run's summary is judged against
    its own ledger for a claimed send, as a chat reply is. The run read one
    file and sent nothing."""
    p = build_platform(str(tmp_path / "home"))
    _rows(p, "s1", None, [("read_file", True, "readonly", "text")])
    said = "I emailed the summary to the client."
    child = await _settle_child(p, "s1", said)
    assert child.summary.startswith(said)
    assert "No message was sent in this run." in child.summary


async def test_a_run_whose_send_tool_succeeded_is_not_marked(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _rows(p, "s2", None, [("notify", True, "irreversible", "sent")])
    said = "I sent the summary to your phone."
    child = await _settle_child(p, "s2", said)
    assert child.summary == said


def test_settle_hands_on_only_the_calls_that_succeeded_in_this_run(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    row = _rows(p, "d3", None, [
        ("write_file", False, "reversible", _DECLINED),
        ("read_file", True, "readonly", "text"),
    ])
    # An earlier run of the same session that DID write is not this run's work.
    with session_scope(p.engine) as db:
        db.add(ToolInvocation(session_id="d3", agent_run_id="r0", tool="edit_file", ok=True,
                              reversibility="reversible", created_at=_BASE))
        db.commit()
    tools, _outcome = orch._settle_finished_run(row, "r1", SessionStatus.COMPLETED)
    assert tools == ["read_file"]
    # An id-less run (a test double) reads the whole session's successes.
    tools_all, _ = orch._settle_finished_run(row, "", SessionStatus.COMPLETED)
    assert sorted(tools_all) == ["edit_file", "read_file"]
