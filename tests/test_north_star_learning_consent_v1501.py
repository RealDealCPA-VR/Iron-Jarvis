"""v1.501.0 north-star plan, Group 1: learning consent (1.1, 1.5, 1.6).

* 1.1 ``recall_lessons`` never returns a task note (``reflection``) — a note
  about one client's run is never recalled into another client's chat (a
  reflection row has no folder the wall could judge, so it is left out, as the
  prompt already leaves it out).
* 1.5 (decision D2) a preference an AGENT RUN guessed waits in Improvements
  as a suggestion (``proposed``, origin ``noticed``, the run as evidence) and
  reaches no prompt until the owner keeps it; a chat turn's call is unchanged
  (kept at once, origin ``said``).
* 1.6 (decision D3) lessons a model condensed from task notes wait as
  suggestions naming the runs they came from; the notes are kept until the
  owner answers (kept: they go; declined: they stay, and are not condensed
  again).
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from sqlmodel import select

import iron_jarvis.learning.models  # noqa: F401  (register tables before init_db)
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.events import EventBus
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.learning import preferences as prefs
from iron_jarvis.learning.engine import LearningEngine
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.learning.tools import RecallLessonsTool, RememberPreferenceTool
from iron_jarvis.tools.base import ToolContext

collect = importlib.import_module("iron_jarvis.inbox.collect")


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def learning(engine):
    return LearningEngine(engine)


def _ctx(engine, tmp_path, session_id: str) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id=session_id, agent_run_id=session_id,
        config=None, event_bus=EventBus(), engine=engine,
    )


def _run(engine, task: str = "Tidy the March invoices") -> str:
    sess = Session(task=task, agent_type=AgentType.BUILDER, status=SessionStatus.ACTIVE)
    sid = sess.id
    with session_scope(engine) as db:
        db.add(sess)
        db.commit()
    return sid


def _rows(engine) -> list[LessonRecord]:
    with session_scope(engine) as db:
        rows = list(db.exec(select(LessonRecord)))
        for r in rows:
            db.expunge(r)
        return rows


# --------------------------------------------------------------------------- #
# 1.1 recall_lessons leaves out task notes
# --------------------------------------------------------------------------- #


async def test_recall_lessons_returns_no_task_note_from_another_clients_run(learning, engine, tmp_path):
    # A run in client A's folder left a task note naming A's work.
    learning.reflect("sess_a", task="Reconcile Hartleys ledger", summary="Hartleys owe 4,210 on the March bill", ok=True)
    learning.note_preference("Prefers short answers")
    res = await RecallLessonsTool(learning).execute({}, _ctx(engine, tmp_path, "sess_b"))
    assert res.ok
    assert "Hartleys" not in res.output
    assert all(r["source"] != "reflection" for r in res.data["lessons"])
    assert "Prefers short answers" in res.output  # the owner's preference is still recalled


# --------------------------------------------------------------------------- #
# 1.5 a guessed preference is a suggestion (D2)
# --------------------------------------------------------------------------- #


async def test_an_agent_runs_preference_waits_as_a_suggestion_and_reaches_no_prompt(learning, engine, tmp_path):
    sid = _run(engine)
    res = await RememberPreferenceTool(learning).execute(
        {"text": "Prefers invoices sorted by due date"}, _ctx(engine, tmp_path, sid)
    )
    assert res.ok
    # The model is told the truth: suggested, waiting for the owner.
    assert "remembered" not in res.output.lower()
    assert "suggest" in res.output.lower() and "Improvements" in res.output
    assert res.data["status"] == "proposed"
    [row] = _rows(engine)
    assert (row.status, row.origin, row.source) == ("proposed", "noticed", "preference")
    ev = prefs.evidence_of(row)
    assert ev and ev[0]["where"] == "run" and ev[0]["link"] == f"/sessions/{sid}"
    assert "March invoices" in ev[0]["quote"]  # what the run was asked
    # No prompt reads it until the owner keeps it.
    assert "sorted by due date" not in learning.apply_to_prompt("BASE")
    assert learning.lessons(scope="user") == []
    # It waits in Improvements (and on the Memory page), worded as noticed.
    assert [s["id"] for s in prefs.list_preferences(engine)["suggested"]] == [row.id]
    item = collect.preference_item(prefs.pref_view(row))
    assert item["evidence"] and all("You said" not in line for line in item["evidence"])
    assert "agent run" in item["evidence"][0]
    # Kept by the owner, it reaches the prompt.
    prefs.keep(engine, row.id)
    assert "sorted by due date" in learning.apply_to_prompt("BASE")


@pytest.mark.parametrize("session_id", ["chat", "build:thread-1"])
async def test_a_chat_turns_preference_is_kept_at_once_as_before(learning, engine, tmp_path, session_id):
    res = await RememberPreferenceTool(learning).execute(
        {"text": "Prefers short answers"}, _ctx(engine, tmp_path, session_id)
    )
    assert res.ok
    assert res.output == "remembered preference: Prefers short answers"
    assert res.data["text"] == "Prefers short answers"
    [row] = _rows(engine)
    assert (row.status, row.origin, row.weight) == ("confirmed", "said", 5)
    assert "Prefers short answers" in learning.apply_to_prompt("BASE")


async def test_an_agent_runs_suggestion_is_not_minted_twice_or_past_the_open_limit(learning, engine, tmp_path):
    sid = _run(engine)
    tool = RememberPreferenceTool(learning)
    ctx = _ctx(engine, tmp_path, sid)
    first = await tool.execute({"text": "Prefers numbered steps"}, ctx)
    again = await tool.execute({"text": "Prefers numbered steps"}, ctx)
    assert first.ok and again.ok
    assert "already" in again.output.lower()
    assert len(_rows(engine)) == 1
    # A sentence the owner already said is not suggested again.
    learning.note_preference("Never uses emojis")
    said = await tool.execute({"text": "Never uses emojis"}, ctx)
    assert said.ok and "already" in said.output.lower()
    # The open limit holds for run suggestions too; the result says so.
    await tool.execute({"text": "Prefers tables for figures"}, ctx)
    await tool.execute({"text": "Prefers British spelling"}, ctx)
    full = await tool.execute({"text": "Prefers plain fonts in letters"}, ctx)
    assert full.ok is False and "wait" in (full.error or "")
    assert sum(r.status == "proposed" for r in _rows(engine)) == prefs.MAX_OPEN
    # An empty sentence is never a suggestion.
    empty = await tool.execute({"text": "  "}, ctx)
    assert empty.ok is False and all(r.text.strip() for r in _rows(engine))


def test_the_agent_line_and_the_tool_say_a_guess_is_a_suggestion():
    from iron_jarvis.agents import types

    assert "so you do it that way next time" not in types._VOICE
    assert "remember_preference" in types._VOICE and "suggest" in types._VOICE.lower()
    desc = RememberPreferenceTool.description.lower()
    assert "suggest" in desc and "keeps" in desc


# --------------------------------------------------------------------------- #
# 1.6 condensed lessons are suggestions (D3)
# --------------------------------------------------------------------------- #


def _seed_notes(learning, n: int = 6) -> None:
    for i in range(n):
        learning.reflect(f"sess_{i}", task=f"task {i}", summary=f"note {i}", ok=True)


async def _fake_complete(prompt: str) -> str:
    return json.dumps(["Break work into small verified steps.", "State assumptions up front."])


def test_condensed_lessons_wait_with_their_runs_as_evidence_and_keep_the_notes(learning, engine):
    _seed_notes(learning)
    res = asyncio.run(learning.distill(_fake_complete))
    assert res["distilled"] == 2 and res["removed"] == 0
    assert "Improvements" in res["note"]
    rows = _rows(engine)
    distilled = [r for r in rows if r.source == "distilled"]
    assert len(distilled) == 2
    assert all((r.status, r.origin) == ("proposed", "noticed") for r in distilled)
    links = {e.get("link") for e in json.loads(distilled[0].evidence_json)}
    assert {f"/sessions/sess_{i}" for i in range(6)} <= links
    # The notes are kept while the owner has not answered.
    assert learning.raw_reflection_count() == 0  # all six are named by a waiting suggestion
    assert sum(r.source == "reflection" for r in rows) == 6
    out = learning.apply_to_prompt("BASE")
    assert "small verified steps" not in out
    # Both wait in Improvements.
    assert {s["id"] for s in prefs.list_preferences(engine)["suggested"]} == {r.id for r in distilled}
    item = collect.preference_item(prefs.pref_view(distilled[0]))
    assert all("You said" not in line for line in item["evidence"])
    # A second pass while they wait condenses nothing.
    again = asyncio.run(learning.distill(_fake_complete))
    assert again["distilled"] == 0
    # Kept: the notes it replaced go once no sibling waits.
    keep_row, decline_row = distilled
    prefs.keep(engine, keep_row.id)
    assert sum(r.source == "reflection" for r in _rows(engine)) == 6  # the other still waits
    prefs.decline(engine, decline_row.id)
    assert sum(r.source == "reflection" for r in _rows(engine)) == 0
    assert "small verified steps" in learning.apply_to_prompt("BASE") or "assumptions" in learning.apply_to_prompt("BASE")


def test_declined_condensed_lessons_leave_the_notes_and_are_not_asked_again(learning, engine):
    _seed_notes(learning)
    asyncio.run(learning.distill(_fake_complete))
    for r in _rows(engine):
        if r.source == "distilled":
            prefs.decline(engine, r.id)
    assert sum(r.source == "reflection" for r in _rows(engine)) == 6
    # The declined notes are never condensed again.
    res = asyncio.run(learning.distill(_fake_complete))
    assert res["distilled"] == 0
    # Dedup never removes a note a condensed suggestion names.
    learning.reflect("sess_x", task="task 0", summary="note again", ok=True)
    assert learning.dedup() == 0
