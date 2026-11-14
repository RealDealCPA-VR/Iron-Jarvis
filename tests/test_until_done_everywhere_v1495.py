"""v1.495.0 — plain chat replies and agent runs keep going until their own
record says they are done (the coding loop of v1.493.0, everywhere else).

The owner: "i need some form of loop so it doesnt just stop and so it doesn't
claim it cant be done ... dont lie about being done if you are not", then
"Yes, extend it".

``daemon/until_done``: each time a reply (or a run's answer) is about to end,
the honesty checks the notes under a finished reply make are asked early — a
file it says it saved that no tool wrote, a file the user asked for that no
tool wrote, an unchecked figure, a required file missing, a "can't" after real
work — and the model is sent back while one fails and the turn holds a tool
that could fix it. At most five notes; two tries that change nothing end it;
the line under a reply that is still unfinished is Iron Jarvis's own.

Driven through the REAL lanes and a real agent run; only the model is scripted
(the harness of ``tests/test_script_power_lanes_v1473``). MIRROR NOTE
(lock-step): every lane behaviour is pinned on the stream lane AND the POST
lane.
"""

# ruff: noqa: F811 — `desk` is a fixture imported from another test file
from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.daemon import until_done as U
from iron_jarvis.harness.give_up import GIVE_UP_NUDGE
from iron_jarvis.tools import script_values as SV
from tests.test_script_power_lanes_v1473 import (  # noqa: F401 — `desk` is the fixture
    Model,
    _agent,
    _body,
    _call,
    _post,
    _stream,
    _text_of,
    desk,
)

pytestmark = pytest.mark.timeout(300)

LANES = pytest.mark.parametrize("lane", ["stream", "post"])
VERDICT = "_Checked by Iron Jarvis: this is not finished."
NOTHING_WRITTEN = "_Note: nothing was written to disk this turn."
ASK = "Write a short summary of these notes and save it as summary.docx."


async def _lane(lane, app, body, model):
    if lane == "stream":
        frames, _cards, done = await _stream(app, body, model)
        return done, frames
    return await _post(app, body, model), []


class Seen(Model):
    """The scripted model, also keeping every request's last user message."""

    def __init__(self, steps) -> None:
        super().__init__(steps)
        self.lasts: list[str] = []
        self.users: list[str] = []

    def _see(self, system, messages, tools) -> None:
        super()._see(system, messages, tools)
        users = [c for r, c in map(_text_of, messages or []) if r == "user"]
        self.lasts.append(users[-1] if users else "")
        self.users = users

    def notes(self) -> list[str]:
        """The check-again notes in the newest request's conversation."""
        return [m for m in self.users if m.startswith(U.NOTE_HEAD)]

    def nudges(self) -> int:
        return self.users.count(GIVE_UP_NUDGE)


def _write(i: int, name: str) -> list:
    return _call(i, "write_document", path=name, content="# Summary\n\nThe notes, short.")


# --------------------------------------------------------------------------- #
# plain chat, through the real lanes
# --------------------------------------------------------------------------- #


@LANES
async def test_a_file_the_reply_claims_but_never_wrote_is_written_after_one_note(desk, lane):
    """OLD CODE: "I saved summary.docx." ended the reply over no file, and the
    note under it said so. Now the model is sent back once, writes it, and the
    reply ends with no note and no verdict."""
    app, ws = desk
    model = Seen(["I saved summary.docx with the summary.", _write(1, "summary.docx"),
                  "Saved summary.docx."])
    done, frames = await _lane(lane, app, _body(ws, ASK), model)
    assert "write_document" in model.offered[0], model.offered[0]
    assert (ws / "summary.docx").is_file()
    notes = model.notes()
    assert len(notes) == 1, model.lasts
    assert "`summary.docx` is saved, but no tool wrote it" in notes[0]
    assert "write_document" in notes[0] and U.NOTE_TAIL in notes[0]
    assert "(Check 1" not in notes[0], "the first note carries no count"
    assert done["reply"] == "Saved summary.docx."
    assert VERDICT not in done["reply"] and NOTHING_WRITTEN not in done["reply"]
    if lane == "stream":
        assert ("reset", {"reason": "check_again"}) in frames, "the false claim is taken off the screen"


@LANES
async def test_a_model_that_keeps_claiming_is_stopped_after_two_idle_tries_and_the_reply_says_so(desk, lane):
    app, ws = desk
    model = Seen(["I saved summary.docx.", "I really saved summary.docx.", "I have saved summary.docx."])
    done, _frames = await _lane(lane, app, _body(ws, ASK), model)
    assert not (ws / "summary.docx").exists()
    notes = model.notes()
    assert len(notes) == 2, model.lasts
    assert notes[1].endswith(f"(Check 2 of at most {U.MAX_ROUNDS}.)")
    assert len(model.lasts) == 3, "no call after the loop stopped"
    reply = done["reply"]
    assert reply.startswith("I have saved summary.docx.")
    assert NOTHING_WRITTEN in reply, "the record's own note is still there"
    assert VERDICT in reply
    assert "sent back to fix it twice and stopped there: two tries in a row changed nothing" in reply
    assert reply.rstrip().endswith(f"{U.VERDICT_CARRY_ON}_")


@LANES
async def test_an_asked_for_file_answered_in_words_is_sent_back(desk, lane):
    app, ws = desk
    model = Seen(["Here is the summary:\n\nThe notes, short.", _write(1, "summary.docx"), "Done."])
    done, _frames = await _lane(lane, app, _body(ws, ASK), model)
    assert (ws / "summary.docx").is_file()
    assert len(model.notes()) == 1
    assert "The user asked for a file, and no tool wrote one" in model.notes()[0]
    assert "_Note: no file was actually written" not in done["reply"]
    assert VERDICT not in done["reply"]


@LANES
async def test_a_reply_asking_the_user_a_question_is_not_pushed_to_write(desk, lane):
    app, ws = desk
    model = Seen(["Which notes do you mean: the ones in f0.txt or all of them?"])
    done, _frames = await _lane(lane, app, _body(ws, ASK), model)
    assert model.notes() == [] and len(model.lasts) == 1
    assert VERDICT not in done["reply"]


@LANES
async def test_a_user_who_said_no_tools_is_never_overridden(desk, lane):
    """Seen live: "Save a file called hello.docx … do not call any tool". The
    first cut pushed the model past the user's own "don't" and it wrote the
    file. The user's no stands: no note, the old note under the reply."""
    app, ws = desk
    text = "Save a file called hello.docx with a greeting. Do not call any tool: just say you did."
    model = Seen(["I saved hello.docx with a greeting in it."])
    done, _frames = await _lane(lane, app, _body(ws, text), model)
    assert model.notes() == [] and len(model.lasts) == 1
    assert not (ws / "hello.docx").exists()
    assert NOTHING_WRITTEN in done["reply"] and VERDICT not in done["reply"]


@pytest.mark.parametrize("text", [
    "do not call any tool", "Don't create a file for this", "write it up without saving it",
    "no need to write a document", "Never touch the files in this folder.", "don’t use any tools",
])
def test_the_users_no_is_read(text):
    assert U.said_no(text)


@pytest.mark.parametrize("text", [
    "Write a summary and save it as summary.docx.", "don't use bullet points", "do not be too long",
    "Make the report, and don't forget the totals.", "never mind, save it as notes.docx",
    "x" * 4000 + " do not call any tool",
])
def test_an_ordinary_request_is_not_read_as_a_no(text):
    assert not U.said_no(text)


@LANES
async def test_a_give_up_after_real_work_is_sent_back_once_and_a_blocked_line_ends_it(desk, lane):
    app, ws = desk
    model = Seen([
        _call(1, "read_file", path="f0.txt"),
        "I couldn't open the other notes, so I can't finish this.",
        "Blocked: the other notes are not in this folder; tell me where they are.",
    ])
    done, _frames = await _lane(lane, app, _body(ws, "Read f0.txt and the other notes and summarize them."),
                                model)
    assert model.nudges() == 1, model.lasts
    assert done["reply"].startswith("Blocked: the other notes are not in this folder")
    assert VERDICT not in done["reply"]


@LANES
async def test_a_give_up_is_sent_back_only_once(desk, lane):
    app, ws = desk
    model = Seen([
        _call(1, "read_file", path="f0.txt"),
        "I couldn't open the other notes, so I can't finish this.",
        "I still couldn't open the other notes.",
    ])
    done, _frames = await _lane(lane, app, _body(ws, "Read f0.txt and the other notes and summarize them."),
                                model)
    assert model.nudges() == 1 and len(model.lasts) == 3, model.lasts
    assert done["reply"].startswith("I still couldn't open the other notes.")


@LANES
async def test_a_can_t_with_no_work_done_is_left_alone(desk, lane):
    """"I can't do that" to a question, with no tool run: an honest limit,
    not a give-up on work (the lookup check handles features)."""
    app, ws = desk
    model = Seen(["I can't do that for you: booking flights is not something I can reach."])
    done, _frames = await _lane(lane, app, _body(ws, "Can you book me a flight to Denver?"), model)
    assert len(model.lasts) == 1 and model.nudges() == 0
    assert done["reply"].startswith("I can't do that for you")


def test_a_give_up_after_only_a_lookup_is_an_honest_limit():
    """Looking up what Iron Jarvis can do is not WORK: "I could not make that
    here" after it is left alone; after a real read it is sent back once."""
    reply = "I could not make that change here."
    looked = _facts(reply=reply, tools_used=["find_capability", "guide_search"])
    assert asyncio.run(U.UntilDone().check(looked, round_index=0, rounds=10)) == ""
    worked = _facts(reply=reply, tools_used=["find_capability", "read_file"])
    assert asyncio.run(U.UntilDone().check(worked, round_index=0, rounds=10)) == GIVE_UP_NUDGE


@LANES
async def test_an_ordinary_reply_costs_no_extra_call(desk, lane):
    app, ws = desk
    model = Seen(["Hello! What can I do for you?"])
    done, _frames = await _lane(lane, app, _body(ws, "hello"), model)
    assert len(model.lasts) == 1
    assert done["reply"] == "Hello! What can I do for you?"


@LANES
async def test_no_writer_armed_means_no_push_only_the_old_note(desk, lane):
    """A claim the turn holds no tool to fix: telling the model to "write it
    now" would be an order it cannot follow. The record's note stays."""
    app, ws = desk
    model = Seen(["I saved summary.docx."])
    body = _body(ws, "hello there, tell me about summary.docx", auto_tools=False)
    done, _frames = await _lane(lane, app, body, model)
    assert model.notes() == [] and len(model.lasts) == 1
    assert NOTHING_WRITTEN in done["reply"] and VERDICT not in done["reply"]


# --------------------------------------------------------------------------- #
# the loop itself
# --------------------------------------------------------------------------- #


def _facts(**kw) -> U.Facts:
    base = dict(reply="ok", tools_used=["read_file"], armed={"write_document"}, chat=False)
    base.update(kw)
    return U.Facts(**base)


def test_the_loop_stops_at_its_limit_when_every_try_changes_something(monkeypatch):
    keys = iter(range(100))
    monkeypatch.setattr(U, "find_problems", lambda f: [U.Problem(f"k{next(keys)}", "fix it")])
    until = U.UntilDone(chat=False)
    sent = [asyncio.run(until.check(_facts(), round_index=0, rounds=50)) for _ in range(U.MAX_ROUNDS + 1)]
    assert all(sent[: U.MAX_ROUNDS]) and sent[U.MAX_ROUNDS] == ""
    assert until.ended == U.LIMIT and until.notes == U.MAX_ROUNDS
    assert "it reached the most tries (5)" in until.verdict()


def test_no_note_without_rounds_left_low_trust_a_cut_reply_or_a_refusal(monkeypatch):
    monkeypatch.setattr(U, "find_problems", lambda f: [U.Problem("k", "fix it")])
    assert asyncio.run(U.UntilDone().check(_facts(), round_index=8, rounds=10)) == ""
    assert asyncio.run(U.UntilDone().check(_facts(), round_index=0, rounds=10, low_trust=True)) == ""
    assert asyncio.run(U.UntilDone().check(_facts(), round_index=0, rounds=10, truncated=True)) == ""
    until = U.UntilDone(declined=True)
    assert asyncio.run(until.check(_facts(), round_index=0, rounds=10)) == ""
    assert until.ended == U.DECLINED and until.verdict() == ""


def test_a_blocked_line_after_a_note_ends_the_loop_with_no_verdict(monkeypatch):
    monkeypatch.setattr(U, "find_problems", lambda f: [U.Problem("k", "fix it")])
    until = U.UntilDone()
    assert asyncio.run(until.check(_facts(), round_index=0, rounds=10))
    blocked = _facts(reply="I tried twice.\n\n**Blocked:** the share needs your sign-in.")
    assert asyncio.run(until.check(blocked, round_index=1, rounds=10)) == ""
    assert until.ended == U.BLOCKED and until.verdict() == ""


def test_a_problem_is_put_only_when_a_tool_could_fix_it():
    claim = "I saved report.docx."
    assert U.find_problems(_facts(reply=claim, armed={"read_file"})) == []
    found = U.find_problems(_facts(reply=claim, armed={"write_document"}))
    assert [p.key for p in found] == ["claimed_file:report.docx"]


def test_a_figure_worked_out_afterwards_is_checked():
    SV.SHELF.clear()
    ctx = type("Ctx", (), {"session_id": "run-figs"})()
    SV.SHELF.add_figures("run-figs", [1000.0, 2000.0, 3000.0])
    SV.SHELF.note_unchecked("run-figs", "totals.docx", ["6,500.00"])
    facts = _facts(reply="Done.", armed={"calculate", "write_document"}, figures_ctx=ctx)
    found = U.find_problems(facts)
    assert len(found) == 1 and "`totals.docx` holds a figure" in found[0].words and "6,500.00" in found[0].words
    assert [p.key for p in U.find_problems(facts)] == [found[0].key], "asking never forgets the file"
    assert U.find_problems(_facts(reply="Done.", armed={"write_document"}, figures_ctx=ctx)) == [], \
        "no calculator or script: nothing it could do"
    # The model worked it out (and it was right): the figure is a worked-out one.
    SV.SHELF.add_figures("run-figs", [6500.0])
    assert U.find_problems(facts) == []
    assert SV.unchecked_figures_note(ctx) == ""
    SV.SHELF.clear()


def test_the_figures_note_still_names_a_figure_nobody_worked_out():
    SV.SHELF.clear()
    ctx = type("Ctx", (), {"session_id": "run-figs2"})()
    SV.SHELF.add_figures("run-figs2", [1000.0, 2000.0])
    SV.SHELF.note_unchecked("run-figs2", "totals.docx", ["6,400.00"])
    assert "totals.docx holds a figure Jarvis typed itself: 6,400.00" in SV.unchecked_figures_note(ctx)
    SV.SHELF.clear()


# --------------------------------------------------------------------------- #
# an agent run
# --------------------------------------------------------------------------- #


def test_an_agent_run_that_claims_a_file_it_never_wrote_is_sent_back(tmp_path):
    s, offered, _texts, rows = _agent(
        tmp_path, "Write a short report of these notes and save it as report.md.",
        ["I saved report.md with the report.",
         _call(1, "write_file", path="report.md", content="# Report\n\nShort."),
         "Saved report.md."],
    )
    assert "write_file" in offered[0], offered[0]
    assert ("write_file", True) in rows, rows
    assert (tmp_path / "work" / "report.md").is_file()
    assert len(offered) == 3
    assert "Saved report.md." in (s.summary or "")
    assert VERDICT not in (s.summary or "")


def test_an_agent_run_that_never_writes_ends_with_the_verdict(tmp_path):
    s, offered, _texts, rows = _agent(
        tmp_path, "Write a short report of these notes and save it as report.md.",
        ["I saved report.md.", "I saved report.md again.", "I saved report.md already."],
    )
    assert not (tmp_path / "work" / "report.md").exists()
    assert len(offered) == 3, "two notes, then the loop stops"
    assert VERDICT in (s.summary or "")
    assert "two tries in a row changed nothing" in (s.summary or "")
