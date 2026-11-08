"""Readiness 0j.5 item 17 (daemon part) — the unwatched-turn guard.

``daemon/durable_turns.stop_predicate`` stops a CHAT turn no window has shown
for ``ORPHAN_STOP_S`` (an hour). Two holes:

* it stopped a turn PARKED ON A CARD — an approval waits with no clock until
  its person answers, and an hour away (lunch, a meeting) cost the whole turn;
  an app's question (``mcp_elicitation``) and a model request too;
* it stopped silently: the turn ended "stopped" with no word of why.

Now a turn parked on any card is never an orphan (and the time it spent parked
does not count afterwards), and a turn the guard does stop ends with an error
frame — on its record too — saying why, in the page's title/fix words.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.daemon import durable_turns as dt


def _approval(aid: str = "apr_1") -> str:
    return dt.sse("approval", {"id": aid, "tool": "shell"})


def test_a_turn_parked_on_an_approval_is_never_an_orphan():
    lg = dt.TurnLog("t")
    pred = dt.stop_predicate(lambda: lg)
    lg.add(_approval())
    lg._last_seen -= dt.ORPHAN_STOP_S * 3
    assert pred() is False
    assert lg.stop_why == ""


@pytest.mark.parametrize("event", ["mcp_elicitation", "mcp_sampling", "ask_user"])
def test_a_turn_parked_on_any_other_card_is_never_an_orphan(event):
    lg = dt.TurnLog("t")
    pred = dt.stop_predicate(lambda: lg)
    lg.add(dt.sse(event, {"id": "mcp_1"}))
    lg._last_seen -= dt.ORPHAN_STOP_S * 3
    assert pred() is False
    lg.add(dt.sse("mcp_resolved", {"id": "mcp_1", "kind": "elicitation", "outcome": "accept"}))
    # the hours parked on the card do not count once it closes
    assert pred() is False


def test_time_parked_on_an_answered_card_does_not_count():
    lg = dt.TurnLog("t")
    pred = dt.stop_predicate(lambda: lg)
    lg.add(_approval())
    lg._last_seen -= dt.ORPHAN_STOP_S * 3
    lg.add(dt.sse("approval_resolved", {"id": "apr_1", "decision": "once"}))
    assert pred() is False
    lg._last_seen -= dt.ORPHAN_STOP_S + 1
    assert pred() is True


def test_an_unwatched_turn_with_no_card_is_still_stopped_and_says_why():
    lg = dt.TurnLog("t")
    pred = dt.stop_predicate(lambda: lg)
    lg._last_seen -= dt.ORPHAN_STOP_S + 1
    assert pred() is True
    assert lg.stop_why == dt.ORPHAN_STOP_DETAIL
    assert "hour" in dt.ORPHAN_STOP_DETAIL


async def test_the_stopped_turn_ends_with_its_reason_on_the_frame_and_the_record(tmp_path):
    engine = open_db(tmp_path / "ij.db")
    logs = dt.TurnLogs(engine)
    holder: dict = {}

    async def lane():
        yield dt.sse("token", {"text": "Working on it"})
        holder["log"]._last_seen -= dt.ORPHAN_STOP_S + 1
        if dt.stop_predicate(lambda: holder.get("log"))():
            return  # the lane returns at its round boundary, as routes/chat does
        yield dt.sse("done", {"reply": "never"})

    lg = logs.start("turn_o1", "chat_x", lane())
    holder["log"] = lg
    await asyncio.wait_for(lg.task, 10)
    assert lg.status == "stopped"
    event, data = lg.frames[-1]
    assert event == "error"
    frame = json.loads(data)
    assert frame["title"] == dt.ORPHAN_STOP_TITLE
    assert frame["fix"] == dt.ORPHAN_STOP_FIX
    assert frame["detail"] == dt.ORPHAN_STOP_DETAIL
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, "turn_o1")
        assert row.status == "stopped"
        assert dt.ORPHAN_STOP_TITLE in row.frames_json


async def test_a_turn_stopped_by_the_user_carries_no_unwatched_words(tmp_path):
    engine = open_db(tmp_path / "ij.db")
    logs = dt.TurnLogs(engine)

    async def lane():
        yield dt.sse("token", {"text": "Working"})

    lg = logs.start("turn_o2", "chat_x", lane())
    await asyncio.wait_for(lg.task, 10)
    assert lg.status == "stopped"
    assert lg.frames[-1][0] != "error"
