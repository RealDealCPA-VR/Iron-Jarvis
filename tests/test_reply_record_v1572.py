"""v1.572.0: a chat reply's usage row saves what the reply was SENT.

The extensions plan's baseline (``docs/EXTENSIONS-PLAN-2026-11-24.md``, E0)
could not say how much of a prompt the provider served from its cache, or
which tool definitions a reply was sent: neither was saved. A reply's usage
row (``AgentRun``, ``session_id="chat"``) now keeps both, on BOTH chat lanes:

* ``cache_read_input_tokens`` / ``cache_creation_input_tokens``: the
  provider's own counts, summed over the reply's model calls; None when no
  call reported one (never a made-up 0);
* ``tools_sent_json``: the NAMES of the tool definitions sent, and
  ``tool_spec_tokens``: their estimated size summed over the calls.

Nothing on screen changes. Through the real app, the real lanes and the real
end-row write; only the model is a stand-in.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, OpenModelCall
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import call_ledger, pricing
from iron_jarvis.eval.pricing import UsageTally
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

LANES = ("/chat", "/chat/stream")
CACHED = {"input_tokens": 900, "output_tokens": 20,
          "cache_read_input_tokens": 600, "cache_creation_input_tokens": 100}
PLAIN = {"input_tokens": 900, "output_tokens": 20}
SRC = Path(chat_turn.__file__).resolve().parent


def _stand_in(platform, usage: dict) -> list:
    """The model answers in words with *usage*; returns the list that fills
    with the tool definitions each call was really sent."""
    seen: list = []

    class _Answer(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append(list(tools or []))
            return LLMResponse(text="Here is the report.", usage=dict(usage))

    platform.providers.register("mock", lambda model=None: _Answer())
    return seen


def _row(engine, thread: str) -> AgentRun:
    with session_scope(engine) as db:
        rows = [r for r in db.exec(select(AgentRun)).all()
                if r.session_id == "chat" and r.thread_id == thread]
    assert len(rows) == 1, rows
    return rows[0]


def _ask(app, lane: str, thread: str) -> None:
    body = {"messages": [{"role": "user", "content": "write the report"}],
            "thread_id": thread, "tools": ["read_file", "list_files"]}
    r = TestClient(app).post(lane, json=body)
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Both lanes, through the real end row
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", LANES)
def test_the_row_saves_the_cache_counts_and_the_tools_the_model_was_sent(tmp_path, lane):
    app = create_app(str(tmp_path))
    seen = _stand_in(app.state.platform, CACHED)
    _ask(app, lane, "t-sent")
    row = _row(app.state.platform.engine, "t-sent")

    assert (row.cache_read_input_tokens, row.cache_creation_input_tokens) == (600, 100)
    assert row.input_tokens == 900, "the cache counts are parts of the input, not added to it"

    assert len(seen) == 1 and seen[0], "the stand-in model was sent tool definitions once"
    sent_names = [t["name"] for t in seen[0]]
    assert {"read_file", "list_files"} <= set(sent_names)
    # The row names exactly what the model was really sent, in that order.
    assert json.loads(row.tools_sent_json) == sent_names
    assert row.tool_spec_tokens == chat_turn._tool_spec_tokens(seen[0]) > 0
    # Names only: no definition text is kept.
    assert seen[0][0]["description"] not in row.tools_sent_json


@pytest.mark.parametrize("lane", LANES)
def test_no_cache_figure_reported_is_saved_as_not_known_never_zero(tmp_path, lane):
    app = create_app(str(tmp_path))
    _stand_in(app.state.platform, PLAIN)
    _ask(app, lane, "t-plain")
    row = _row(app.state.platform.engine, "t-plain")
    assert row.cache_read_input_tokens is None and row.cache_creation_input_tokens is None
    assert json.loads(row.tools_sent_json), "the tools are saved whatever the provider reports"


def test_every_lane_row_that_closes_the_ledger_also_saves_what_was_sent():
    """The lanes write their end row from more than one place (the answer, a
    cancel, a failure). Each one that hands over the turn's ledger hands over
    its tally too, on both lanes."""
    for name in ("chat_turn.py", "routes/chat.py"):
        src = (SRC / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        rows = src.count("ledger=_tally.ledger,  # v1.529.0 (lock-step)")
        assert rows >= 1, name
        assert src.count("tally=_tally,  # v1.572.0 (lock-step)") == rows, name
    both = [(SRC / n).read_text(encoding="utf-8") for n in ("chat_turn.py", "routes/chat.py")]
    assert "note_tools_sent(_tally, d, tool_specs, provider_choice, model_choice)" in both[0]
    assert "_chat_turn.note_tools_sent(" in both[1]


# --------------------------------------------------------------------------- #
# The tally
# --------------------------------------------------------------------------- #
def test_the_tally_keeps_names_in_order_once_and_sums_the_size():
    t = UsageTally()
    assert _sent(t) == {}, "nothing noted is 'not recorded', not 'sent none'"
    t.note_tools([{"name": "read_file", "description": "secret words"}, {"name": "shell"}], 300)
    t.note_tools([{"name": "shell"}, {"type": "function", "function": {"name": "find"}}], 250)
    t.note_tools(["plain_name", "", None], 0)
    assert list(t.tools_sent) == ["read_file", "shell", "find", "plain_name"]
    assert t.tool_spec_tokens == 550
    assert _sent(t) == {"tools_sent_json": '["read_file", "shell", "find", "plain_name"]',
                        "tool_spec_tokens": 550}
    # The receipt the user reads is unchanged.
    assert set(t.as_dict()) == {"input_tokens", "output_tokens"}


def test_a_reply_sent_no_tools_is_a_known_empty_list():
    t = UsageTally()
    t.note_tools([], 0)
    assert _sent(t) == {"tools_sent_json": "[]", "tool_spec_tokens": 0}


def test_the_list_of_names_is_bounded_and_a_bad_value_never_raises():
    t = UsageTally()
    t.note_tools([{"name": f"tool_{i}"} for i in range(pricing.TOOLS_SENT_MAX + 50)], 10)
    t.note_tools([{"name": "x" * 5000}], "not a number")  # type: ignore[arg-type]
    t.note_tools(object(), 5)  # type: ignore[arg-type]
    assert len(t.tools_sent) == pricing.TOOLS_SENT_MAX
    assert max(len(n) for n in t.tools_sent) <= pricing.TOOL_NAME_MAX
    assert chat_turn._sent_fields(object()) == {} and chat_turn._sent_fields(None) == {}


def _sent(tally) -> dict:
    return chat_turn._sent_fields(tally)


# --------------------------------------------------------------------------- #
# A reply cut off by a stop, and a database from before the columns
# --------------------------------------------------------------------------- #
def _cut_off(tmp_path, monkeypatch, usage: dict, thread: str) -> AgentRun:
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    _stand_in(app.state.platform, usage)
    monkeypatch.setattr(chat_turn, "_write_chat_usage", lambda *a, **k: None)  # the kill
    _ask(app, "/chat/stream", thread)
    assert call_ledger.drain()
    assert len(_open(engine)) == 1
    monkeypatch.setattr(call_ledger, "PROCESS_ID", "the-next-start")
    assert call_ledger.settle_open_calls(engine) == 1
    return _row(engine, thread)


def _open(engine) -> list:
    with session_scope(engine) as db:
        return list(db.exec(select(OpenModelCall)).all())


def test_a_reply_cut_off_by_a_stop_keeps_the_cache_counts_its_calls_carried(tmp_path, monkeypatch):
    row = _cut_off(tmp_path, monkeypatch, CACHED, "t-cut")
    assert row.state == AgentState.FAILED and row.result == call_ledger.CUT_OFF_WORDS
    assert (row.cache_read_input_tokens, row.cache_creation_input_tokens) == (600, 100)
    assert row.tools_sent_json is None and row.tool_spec_tokens is None  # died with the reply


def test_a_cut_off_reply_with_no_cache_figure_stays_not_known(tmp_path, monkeypatch):
    row = _cut_off(tmp_path, monkeypatch, PLAIN, "t-cut-plain")
    assert row.cache_read_input_tokens is None and row.cache_creation_input_tokens is None


NEW_COLUMNS = ("cache_read_input_tokens", "cache_creation_input_tokens",
               "tools_sent_json", "tool_spec_tokens")


def test_an_existing_database_gains_the_columns_and_its_old_rows_read_not_known(tmp_path):
    p = build_platform(str(tmp_path))
    with session_scope(p.engine) as db:
        db.add(AgentRun(id="run_old", session_id="chat", thread_id="t-old", provider="mock",
                        model="m", state=AgentState.COMPLETED, input_tokens=5,
                        created_at=datetime.now(timezone.utc)))
        db.commit()
    with p.engine.connect() as conn:  # the database as v1.571.1 left it
        for col in NEW_COLUMNS:
            conn.execute(text(f'ALTER TABLE "agentrun" DROP COLUMN "{col}"'))
        conn.commit()
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("agentrun")')).all()}
    assert not set(NEW_COLUMNS) & cols and "input_tokens" in cols
    p.engine.dispose()

    p2 = build_platform(str(tmp_path))  # the next start
    with p2.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("agentrun")')).all()}
    assert set(NEW_COLUMNS) <= cols, "the start did not add the new columns"
    old = _row(p2.engine, "t-old")
    assert old.input_tokens == 5
    assert [getattr(old, c) for c in NEW_COLUMNS] == [None, None, None, None]
