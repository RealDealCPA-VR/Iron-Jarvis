"""Fourth fix plan, group 32, PG4: a finished reply whose usage row could not
be saved is never later recorded as "cut off when Iron Jarvis stopped".

A chat reply's calls are kept as open records until its end row deletes them
in the same transaction (v1.529.0). When that end row fails (the database is
busy), the records stayed, and the next start folded them into a FAILED row
with the cut-off words, although the reply had ended. Now the failed end row
marks the reply's records ``ended`` (``TurnLedger.mark_ended``, inside the
same hold of the ledger, never taking its lock a second time), and the next
start writes the reply's real state with :data:`ENDED_UNSAVED_WORDS`.

Through the real app, the real lanes, the real end-row write and the real
start-up step; only the model is a stand-in and the end row's transaction is
made to fail.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, OpenModelCall
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import call_ledger
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

ANSWER = {"input_tokens": 700, "output_tokens": 23}


class _Answer(MockLLMAdapter):
    async def complete(self, *, system, messages, tools, **kw):
        return LLMResponse(text="Here is the report.", usage=dict(ANSWER))


def _stand_in(platform) -> None:
    platform.providers.register("mock", lambda model=None: _Answer())


def _rows(engine, model):
    with session_scope(engine) as db:
        return list(db.exec(select(model)).all())


def _chat_rows(engine, thread: str):
    return [r for r in _rows(engine, AgentRun) if r.session_id == "chat" and r.thread_id == thread]


def _another_start(monkeypatch) -> None:
    monkeypatch.setattr(call_ledger, "PROCESS_ID", "the-next-start")


def _end_row_fails(monkeypatch) -> None:
    """The end row's transaction fails (the database is busy): the delete it
    runs inside that transaction raises, so the row is rolled back."""
    def busy(self, db):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(call_ledger.TurnLedger, "close_in", busy)


# --------------------------------------------------------------------------- #
# The finding: an end row that failed reads "ended", never "cut off"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["/chat", "/chat/stream"])
def test_a_finished_reply_whose_usage_row_failed_is_settled_as_ended_not_cut_off(
    tmp_path, monkeypatch, lane
):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    _stand_in(app.state.platform)
    _end_row_fails(monkeypatch)
    body = {"messages": [{"role": "user", "content": "write the report"}], "thread_id": "t-ended"}
    r = TestClient(app).post(lane, json=body)
    assert r.status_code == 200, r.text
    assert call_ledger.drain()
    assert _chat_rows(engine, "t-ended") == [], "the end row really failed"
    kept = [getattr(k, "ended", "(no column)") for k in _rows(engine, OpenModelCall)]

    _another_start(monkeypatch)
    assert call_ledger.settle_open_calls(engine) == 1
    rows = _chat_rows(engine, "t-ended")
    assert len(rows) == 1
    row = rows[0]
    assert row.result != call_ledger.CUT_OFF_WORDS and "cut off" not in row.result, row.result
    assert row.result == call_ledger.ENDED_UNSAVED_WORDS
    assert kept == [AgentState.COMPLETED.value], kept
    assert row.state == AgentState.COMPLETED
    assert (row.input_tokens, row.output_tokens, row.steps) == (
        ANSWER["input_tokens"], ANSWER["output_tokens"], 1)
    assert _rows(engine, OpenModelCall) == []
    assert call_ledger.settle_open_calls(engine) == 0  # a second start adds nothing


def test_a_reply_really_cut_off_still_reads_cut_off(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    _stand_in(app.state.platform)
    # The kill: the reply's end row never ran at all.
    monkeypatch.setattr(chat_turn, "_write_chat_usage", lambda *a, **k: None)
    body = {"messages": [{"role": "user", "content": "write the report"}], "thread_id": "t-cut"}
    assert TestClient(app).post("/chat/stream", json=body).status_code == 200
    assert call_ledger.drain()
    assert [k.ended for k in _rows(engine, OpenModelCall)] == [None]
    _another_start(monkeypatch)
    assert call_ledger.settle_open_calls(engine) == 1
    (row,) = _chat_rows(engine, "t-cut")
    assert row.state == AgentState.FAILED and row.result == call_ledger.CUT_OFF_WORDS


# --------------------------------------------------------------------------- #
# The lock: the mark runs inside the end row's hold and never takes it again
# --------------------------------------------------------------------------- #
def _end_row(p, tally, thread: str) -> threading.Thread:
    d = type("D", (), {"platform": p})()
    fields = {"provider": "openai", "model": "gpt-4o", "state": AgentState.COMPLETED, "steps": 1,
              "input_tokens": 50, "output_tokens": 5, "cost_usd": tally.cost_usd}
    worker = threading.Thread(
        target=chat_turn._write_chat_usage, args=(d, fields), daemon=True,
        kwargs={"thread": thread, "turn": "", "turn_id": "turn-" + thread, "tool_session": "",
                "ledger": tally.ledger},
    )
    worker.start()
    return worker


def test_the_failed_end_row_marks_its_calls_without_a_deadlock(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path))
    tally = call_ledger.JournaledTally(p.engine, thread_id="t-lock", turn_id="turn-lock")
    tally.add("openai", "gpt-4o", {"input_tokens": 50, "output_tokens": 5})
    assert call_ledger.drain()  # the open record is written
    _end_row_fails(monkeypatch)
    worker = _end_row(p, tally, "t-lock")
    worker.join(timeout=10)
    assert not worker.is_alive(), "the end row's write never finished (the ledger's lock was taken twice)"
    assert [k.ended for k in _rows(p.engine, OpenModelCall)] == ["completed"]
    assert tally.ledger.lock.acquire(timeout=5), "the ledger was left held"
    tally.ledger.lock.release()


def test_a_call_written_after_the_failed_end_row_still_counts_and_reads_ended(tmp_path, monkeypatch):
    """The end row failed, so it took no call with it: a call still waiting
    for the worker lands afterwards (never lost) and carries the mark."""
    p = build_platform(str(tmp_path))
    gate = threading.Event()
    call_ledger._submit(gate.wait, 10)  # hold the one worker
    tally = call_ledger.JournaledTally(p.engine, thread_id="t-late", turn_id="turn-late")
    tally.add("openai", "gpt-4o", {"input_tokens": 50, "output_tokens": 5})
    _end_row_fails(monkeypatch)
    worker = _end_row(p, tally, "t-late")
    worker.join(timeout=10)
    assert not worker.is_alive()
    gate.set()
    assert call_ledger.drain()
    assert [(k.input_tokens, k.ended) for k in _rows(p.engine, OpenModelCall)] == [(50, "completed")]
    _another_start(monkeypatch)
    assert call_ledger.settle_open_calls(p.engine) == 1
    (row,) = _chat_rows(p.engine, "t-late")
    assert row.input_tokens == 50 and row.result == call_ledger.ENDED_UNSAVED_WORDS


# --------------------------------------------------------------------------- #
# The owner's database takes the new column at start
# --------------------------------------------------------------------------- #
def test_an_existing_database_gains_the_column_at_start_and_settles_with_it(tmp_path):
    p = build_platform(str(tmp_path))
    with p.engine.connect() as conn:  # the database as v1.540.0 left it
        conn.execute(text('ALTER TABLE "openmodelcall" DROP COLUMN "ended"'))
        conn.commit()
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("openmodelcall")')).all()}
    assert "ended" not in cols and "owner" in cols
    p.engine.dispose()

    p2 = build_platform(str(tmp_path))  # the next start
    with p2.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("openmodelcall")')).all()}
    assert "ended" in cols, "the start did not add the new column to an existing database"
    with session_scope(p2.engine) as db:
        for owner, ended in (("a-failed-reply", "failed"), ("an-old-record", None)):
            db.add(OpenModelCall(
                owner=owner, process="an-earlier-daemon", session_id="chat", thread_id="t-" + owner,
                provider="mock", model="m", input_tokens=100, output_tokens=1, ended=ended,
                started_at=datetime.now(timezone.utc),
            ))
        db.commit()
    assert call_ledger.settle_open_calls(p2.engine) == 2
    (failed,) = _chat_rows(p2.engine, "t-a-failed-reply")
    assert failed.state == AgentState.FAILED and failed.result == call_ledger.ENDED_UNSAVED_WORDS
    (old,) = _chat_rows(p2.engine, "t-an-old-record")
    assert old.state == AgentState.FAILED and old.result == call_ledger.CUT_OFF_WORDS
