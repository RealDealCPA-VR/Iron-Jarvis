"""Fourth fix plan, group 27 (CS6/DT4), the coding-reply half: a stopped
coding reply says WHO stopped it.

A reply stopped by Iron Jarvis at its time limit, by Emergency stop, or by a
stop whose author was not recorded used to read "You stopped this reply", and
"Continue from where it stopped" told the model "The user stopped your reply
here". Now:

* the line the start-up pass writes for such a reply (``keep_stopped_reply``)
  carries the words for its author and ``stoppedBy``;
* "Continue from where it stopped" tells the model the author's note (and the
  hidden question says "I stopped" only when the user did);
* the next turn's model reads the stop by its author (``reply_end_note``);
* the user's own Stop keeps today's words byte for byte;
* NO stopped reply is ever carried on by itself, whoever stopped it (G9);
* a stopped attempts turn's done frame and line say who stopped it.

Driven through the REAL app factory, the real build routes, the real reply
watch (``routes/build.recorded_turn``), the real start-up resume pass and the
real stop record (``durable_turns.write_stop_mark`` / ``stop_fact``); only the
models are scripted doubles. No value is shaped like a credential.
"""

# ruff: noqa: F811 — the home and work fixtures are imported from the session tests
from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.harness import sessions as S
from tests.test_build_attempts_v1397 import LOCAL, World, make_calc_repo
from tests.test_build_resume_v1391 import (  # noqa: F401 — fixtures
    _cut_off_mid_reply,
    _reboot,
    _write,
    home,
    work,
)
from tests.test_build_sessions_v1367 import Script, asgi, frames_of, messages_of, new_session, open_turn, wire
from tests.test_fourth_review_reply_end_v1534 import _day_attempts
from tests.test_fourth_review_reply_end_v1534 import wire as wire_attempts
from tests.test_third_plan_g21_night_attempts import _isolated  # noqa: F401 — the autouse fixture

AUTHORS = ["you", "time_limit", "emergency", ""]
IDS = ["you", "time-limit", "emergency", "unknown"]

#: The words, as the plan's table gives them (sent as written).
LINE_WORDS = {
    "you": "You stopped this reply. Iron Jarvis did not carry it on by itself.",
    "time_limit": "Iron Jarvis stopped this reply at its time limit and did not carry it on by itself.",
    "emergency": "This reply was stopped by Emergency stop. Iron Jarvis did not carry it on by itself.",
    "": "This reply was stopped before it finished. Iron Jarvis did not carry it on by itself.",
}
NOTE_OPENINGS = {
    "you": "[The user stopped your reply here, and has now asked you to continue it. ",
    "time_limit": "[Iron Jarvis stopped your reply here because it reached its time limit, and the user has"
                  " now asked you to continue it. ",
    "emergency": "[Your reply was stopped here by Emergency stop, and the user has now asked you to"
                 " continue it. ",
    "": "[Your reply was stopped here before it finished, and the user has now asked you to continue it. ",
}
#: Today's note for the user's own Stop, byte for byte (third plan G19).
TODAYS_NOTE = (
    "[The user stopped your reply here, and has now asked you to continue it. Your work so far is"
    " above, exactly as it was. A tool call that was cut off says so in its result: a call that only"
    " reads is safe to run again; for anything else, check whether it took effect before doing it"
    " again. Carry on with the reply from where you stopped, and do not repeat work that is already"
    " done.]"
)


def _line(app, sid: str, turn_id: str) -> dict:
    return next(m for m in messages_of(app, sid) if m["role"] == "assistant" and m.get("turnId") == turn_id)


async def _stopped_then_restarted(home, work, who: str):
    """A coding reply that wrote two files is stopped by *who* (the mark the
    real stop paths write on its record), and Iron Jarvis closes before the
    reply has wound down. Then the next start, and its resume pass."""
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    assert durable_turns.write_stop_mark(first.state.platform.engine, "bt-1", utcnow(), by=who)
    second = _reboot(home)
    script = wire(second, Script(["Never said."]))
    carried = await build_routes.resume_after_restart(second.state.d)
    return second, sid, script, carried


# --------------------------------------------------------------------------- #
# the line, the Continue note, and G9 — for every author
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("who", AUTHORS, ids=IDS)
async def test_a_stopped_coding_reply_says_who_stopped_it_and_is_never_carried_on(home, work, who):
    app, sid, script, carried = await _stopped_then_restarted(home, work, who)
    # G9: whoever stopped it, nothing carried it on by itself.
    assert carried == [] and script.calls == [], "a stopped reply was carried on by itself"
    assert not any(m.get("continuation") for m in messages_of(app, sid))
    engine = app.state.platform.engine
    assert not RR.has_record(engine, "bt-1"), "the start-up pass could see it again"

    line = _line(app, sid, "bt-1")
    assert line["error"] == LINE_WORDS[who]
    assert line["stoppedBy"] == who
    assert line["interrupted"] is True and line["canContinue"] is True
    # The page reads the same line.
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert status == 200
    shown = next(m for m in view["messages"] if m["role"] == "assistant" and m.get("turnId") == "bt-1")
    assert shown["error"] == LINE_WORDS[who] and shown["stoppedBy"] == who

    # The user's own press: "Continue from where it stopped".
    script = wire(app, Script([_write("c3", "three.txt", "3"), "All three are there."]))
    status, raw = await open_turn(app, sid, {"text": "", "continue_turn": "bt-1", "turn_id": "bt-go"},
                                  timeout=60)
    assert status == 200 and "event: done" in raw, raw[-400:]
    note = script.calls[0]["messages"][-1].content
    assert note == build_routes.STOPPED_RESUME_NOTES[who]
    assert note.startswith(NOTE_OPENINGS[who]), note
    q = next(m for m in messages_of(app, sid) if m["role"] == "user" and m.get("continuedFrom") == "bt-1")
    assert q["content"] == (
        "Continue the reply I stopped, from where it stopped." if who == "you"
        else "Continue the stopped reply, from where it stopped."
    )


async def test_the_users_own_stop_keeps_todays_words_byte_for_byte(home, work):
    app, sid, script, carried = await _stopped_then_restarted(home, work, "you")
    assert carried == [] and script.calls == []
    line = _line(app, sid, "bt-1")
    assert line["error"] == "You stopped this reply. Iron Jarvis did not carry it on by itself."
    assert line["error"] == S.STOPPED_LINE
    assert build_routes.STOPPED_RESUME_NOTE == TODAYS_NOTE
    assert build_routes.STOPPED_RESUME_NOTES["you"] == TODAYS_NOTE
    assert build_routes.STOPPED_RESUME_QUESTION == "Continue the reply I stopped, from where it stopped."
    assert build_routes.STOPPED_REFUSALS["you"] == build_routes.STOPPED_REFUSAL == "you stopped it"


async def test_a_record_older_than_the_author_reads_as_not_recorded(home, work):
    """A stop written before the column existed has no author: its line says
    the neutral, true words — never "You stopped" — and it is still never
    carried on."""
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    engine = first.state.platform.engine
    assert durable_turns.write_stop_mark(engine, "bt-1", utcnow(), by="you")
    with session_scope(engine) as db:
        rec = db.get(ChatTurnRecord, "bt-1")
        rec.stop_author = None
        db.add(rec)
        db.commit()
    second = _reboot(home)
    script = wire(second, Script(["Never said."]))
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert script.calls == []
    line = _line(second, sid, "bt-1")
    assert line["error"] == LINE_WORDS[""] and line["stoppedBy"] == ""


# --------------------------------------------------------------------------- #
# the next turn's model reads the stop by who made it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("who", AUTHORS, ids=IDS)
def test_the_next_turns_model_reads_who_stopped_the_reply(who):
    m = {"role": "assistant", "content": "", "interrupted": True, "error": S.stopped_line(who), "stoppedBy": who}
    note = S.reply_end_note(m)
    assert S.REPLY_STOPPED_BY[who] in note, note
    assert S.REPLY_ERROR not in note, "a stopped reply read as an error"
    if who != "you":
        assert S.REPLY_CUT_OFF not in note, note
    # The line's words alone (no key, e.g. a line kept before it) read the same.
    del m["stoppedBy"]
    assert S.reply_end_note(m) == note


def test_the_users_stop_is_read_as_today():
    note = S.reply_end_note({"role": "assistant", "content": "", "interrupted": True, "error": S.STOPPED_LINE})
    assert note == f"[{S.REPLY_END_LEAD}{S.REPLY_CUT_OFF}; {S.REPLY_NO_WORDS}.]"


# --------------------------------------------------------------------------- #
# a stopped attempts turn says who stopped it
# --------------------------------------------------------------------------- #


async def test_an_attempts_turn_stopped_by_emergency_stop_says_so(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    began = asyncio.Event()

    async def hang(_world):
        began.set()
        await asyncio.Event().wait()

    wire_attempts(app, monkeypatch, World({"ollama": [hang]}))
    sid = await _day_attempts(app, repo)
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "write the notes file", "turn_id": "att-em", "try_on": [{"provider": LOCAL[0], "model": LOCAL[1]}],
    }, timeout=120.0))
    await asyncio.wait_for(began.wait(), 60)
    assert TURNS.stop("att-em", by="emergency")
    status, raw = await task
    assert status == 200
    frames = frames_of(raw)
    assert frames[-1][0] == "done", frames[-3:]
    assert frames[-1][1].get("stopped") is True and frames[-1][1].get("stoppedBy") == "emergency", frames[-1][1]
    line = _line(app, sid, "att-em")
    assert line.get("stoppedBy") == "emergency", line
