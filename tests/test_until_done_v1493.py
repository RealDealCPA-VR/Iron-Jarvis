"""v1.493.0 — a coding reply keeps going until its own check says it is done.

The owner: "i need some form of loop so it doesnt just stop and so it doesn't
claim it cant be done … and dont lie about being done if you are not."

THE CHECK-AGAIN LOOP (``harness/finish``): after the model's answer to the
finish note, the reply used to end whatever that answer said. Now Iron Jarvis
looks once more each time the model ends: it runs the tests again when a file
changed since their last run, and sends the model back while they fail, a
point is "not met" or a file the request lists is not there. It stops when all
of that checks out, at its limit, when two endings in a row changed nothing,
or when the reply has no steps left. THE LINE UNDER THE REPLY comes from that
check, never from the model's words. A GIVE-UP ending ("this can't be done")
is sent back once (``harness/give_up``).

Driven through the REAL lanes on a real platform (a test command really
runs); only the model is scripted — the harness of
``tests/test_finish_lanes_v1386``. MIRROR NOTE (lock-step): each lane
behaviour is pinned on the stream lane and the POST lane.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.deliverables.scope import required_files
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import give_up as G
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — `env` is the fixture
    NOTE_HEAD,
    SESSION,
    TEST_CMD,
    Model,
    _body,
    _finish_rows,
    _lane,
    _shell,
    _stream,
    _write,
    env,
)

LANES = pytest.mark.parametrize("lane", ["stream", "post"])
VERDICT = "_Checked by Iron Jarvis:"


def _edit(old: str, new: str, cid: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="edit_file", arguments={"path": "app.py", "old": old, "new": new})]


def _last_note(model: Model) -> str:
    return str(model.calls[-1]["messages"][-1].content)


def _x(ws: Path) -> bytes:
    return (ws / "app.py").read_bytes().replace(b"\r\n", b"\n")


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


@LANES
async def test_a_fix_nobody_tested_is_tested_by_iron_jarvis_and_the_reply_ends(env, lane, monkeypatch):
    """The check's tests failed; the model fixed the file and ended WITHOUT
    running them again. OLD CODE: the reply ended there, "tests failed" on
    its record. Now Iron Jarvis runs them again itself; they pass, and the
    reply ends with no further model call."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("The test fails: fixing it.", _edit("X = 11", "X = 2", "fix")), "Fixed.",
    ])
    done, frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert _x(ws) == b"X = 2\n"
    assert len(model.notes()) == 1, "a passing re-run needs no second note"
    assert not model.moves and len(model.calls) == 5, "no model call after the reply's last words"
    fin = done["finish"]
    assert fin["tests"] == "failed" and fin["after"] == "passed"
    assert fin["again"] == {"rounds": 0, "ended": "done", "open": 0}
    assert done["reply"] == "Done: X is set.\n\nFixed."
    assert VERDICT not in done["reply"]
    # The re-run is the check's own call: same ledger, its own id.
    assert [(r.tool, r.ok) for r in _finish_rows(platform)] == [("shell", False), ("shell", True)]
    if lane == "stream":
        mine = [d for e, d in frames if e == "tool_call" and str(d.get("id", "")).startswith(F.CALL_ID_PREFIX)]
        assert {d["id"] for d in mine} == {f"{F.CALL_ID_PREFIX}1", f"{F.CALL_ID_PREFIX}2"}
        assert all(d.get("check") == "finish" for d in mine)


@LANES
async def test_tests_that_still_fail_send_the_model_back_until_they_pass(env, lane, monkeypatch):
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")

    def second_try(model: Model):
        note = _last_note(model)
        assert note.startswith(NOTE_HEAD + " Check again (1 of at most 5).")
        assert f"`{TEST_CMD}` FAILED (exit code 1) when Iron Jarvis ran it again after your last change." in note
        assert "AssertionError" in note, "the re-run's own output is in the note"
        assert F.AGAIN_KEEP_GOING in note and F.AGAIN_TESTS_ASK in note
        assert F.OUTSIDE_FORM in F.AGAIN_TESTS_ASK
        assert "## Points you said are not met" not in note
        return ("Trying the right value.", _edit("X = 12", "X = 2", "fix2"))

    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("The test fails: fixing it.", _edit("X = 11", "X = 12", "fix1")), "Fixed.",
        second_try, "Now it is fixed.",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert _x(ws) == b"X = 2\n"
    assert len(model.notes()) == 2
    fin = done["finish"]
    assert fin["tests"] == "failed" and fin["after"] == "passed"
    assert fin["again"] == {"rounds": 1, "ended": "done", "open": 0}
    assert [r.ok for r in _finish_rows(platform)] == [False, False, True]
    # What the model wrote at each stop that came after a change, once each.
    assert done["reply"] == "Done: X is set.\n\nFixed.\n\nNow it is fixed."
    assert VERDICT not in done["reply"]


@LANES
async def test_a_model_that_changes_nothing_is_sent_back_once_and_the_reply_says_not_finished(env, lane, monkeypatch):
    """Going in circles: the tests fail, the model answers and changes
    nothing, is sent back once, changes nothing again — the loop stops, and
    the line under the reply is Iron Jarvis's, whatever the model wrote."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")

    def shrug(model: Model):
        note = _last_note(model)
        assert note.startswith(NOTE_HEAD + " Check again (1 of at most 5).")
        assert f"The tests (`{TEST_CMD}`) failed when they were last run, and no file was changed since." in note
        return "All done, everything works."

    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        "The tests fail, but that is fine.", shrug,
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 2 and not model.moves
    fin = done["finish"]
    assert fin["tests"] == "failed" and fin["after"] == ""
    assert fin["again"] == {"rounds": 1, "ended": "stuck", "open": 0}
    assert len(_finish_rows(platform)) == 1, "nothing changed: the tests were not run again"
    assert done["reply"].endswith(
        f"\n\n{VERDICT} the tests (`{TEST_CMD}`) still fail. Jarvis sent it "
        "back to work and stopped when two tries in a row changed nothing. Tell it to keep going "
        "and it will carry on._"
    )
    assert "All done, everything works." in done["reply"], "the model's words stay; the verdict is Jarvis's"


@LANES
async def test_failures_the_user_asked_to_leave_are_never_pushed(env, lane, monkeypatch):
    """THE USER'S REQUEST IS THE STANDARD, not the test suite. Seen live:
    "only fix add(), leave the rest to me" — the other tests rightly still
    failed and the first cut of the loop sent the model back three times.
    The model says the failures are outside the request; the reply ends with
    no push, and the line under it states the fact and whose claim it is."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")

    def answer(model: Model):
        note = _last_note(model)
        assert "FAILED (exit code 1)." in note
        assert F.OUTSIDE_ASK in note, "the first note offers the form when its tests failed"
        return "1. met: I did the part you asked for.\nOutside the request: the failing test is about X, which you said to leave."

    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: only the part you asked for.",
        answer, "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 1 and model.moves == ["never reached"]
    assert done["finish"]["again"] == {"rounds": 0, "ended": "outside", "open": 0}
    assert done["reply"].endswith(
        f"\n\n{VERDICT} the tests (`{TEST_CMD}`) still fail; the reply says those failures are "
        "outside what you asked for._"
    )
    assert "keep going" not in done["reply"]


@LANES
async def test_a_command_that_only_may_have_changed_a_file_is_not_progress(env, lane, monkeypatch):
    """Seen live: after the note the model only ran commands (a test run
    through a pipe), which "may change a file" — each one reset the
    going-in-circles count and the loop pushed three times. Now such a command
    only makes the verdict stale: the tests are run again, they say the same
    thing, and that is NOT progress."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")
    look = 'python -c "print(1)"'
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("Looking.", _shell(look, "l1")), "It should be fine.",
        ("Looking again.", _shell(look, "l2")), "Really, it is fine.",
        "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 2 and model.moves == ["never reached"]
    assert done["finish"]["again"] == {"rounds": 1, "ended": "stuck", "open": 0}
    assert [r.ok for r in _finish_rows(platform)] == [False, False, False], "re-run after each stale ending"


@LANES
async def test_a_fix_made_through_commands_counts_when_the_tests_say_something_new(env, lane, monkeypatch):
    """A model that edits through the shell writes no journal row. Its
    progress is read off the tests: a re-run that says something NEW (another
    failure) keeps the loop going; it is never cut off as going in circles."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")

    def put(value: int, cid: str) -> list[ToolCall]:
        return _shell(f"python -c \"open('app.py','w').write('X = {value}\\n')\"", cid)

    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("Fixing.", put(12, "p1")), "Fixed.",
        ("Fixing.", put(13, "p2")), "Fixed again.",
        ("Fixing.", put(2, "p3")), "Fixed for real.",
        "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert _x(ws) == b"X = 2\n" and model.moves == ["never reached"]
    assert done["finish"]["again"] == {"rounds": 2, "ended": "done", "open": 0}
    assert done["finish"]["after"] == "passed" and VERDICT not in done["reply"]


def test_what_a_test_run_said_ignores_timings_and_says_outside_is_read_at_a_line_start():
    class Run:
        def __init__(self, output, verdict="failed", exit_code=1):
            self.output, self.verdict, self.exit_code = output, verdict, exit_code

    a = F.run_signature([Run("Ran 1 test in 0.002s\nFAILED (failures=1) at 0x7f3a")])
    b = F.run_signature([Run("Ran 1 test in 0.013s\nFAILED (failures=1) at 0x11bc")])
    c = F.run_signature([Run("Ran 1 test in 0.002s\nFAILED (failures=2) at 0x7f3a")])
    assert a == b and a != c
    assert a != F.run_signature([Run("Ran 1 test in 0.002s\nFAILED (failures=1) at 0x7f3a", "passed", 0)])
    for yes in ("Outside the request: mean() was to stay.", "1. met\n- **Outside the request:** two tests",
                "  outside the request : the rest"):
        assert F.says_outside(yes), yes
    for no in ("This is outside the request: no.", "Outside the requested range", "", "outside the request"):
        assert not F.says_outside(no), no


@LANES
async def test_the_loop_stops_at_its_limit(env, lane, monkeypatch):
    platform, ws, wire = env
    monkeypatch.setattr(F, "AGAIN_MAX_ROUNDS", 2)
    (ws / "app.py").write_bytes(b"X = 0\n")
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("Fixing.", _edit("X = 11", "X = 12", "f1")), "Try one.",
        ("Fixing.", _edit("X = 12", "X = 13", "f2")), "Try two.",
        ("Fixing.", _edit("X = 13", "X = 14", "f3")), "Try three.",
        "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 3, "the first note and two check-again notes"
    assert model.moves == ["never reached"]
    fin = done["finish"]
    assert fin["again"] == {"rounds": 2, "ended": "limit", "open": 0}
    assert fin["after"] == "failed"
    assert "Jarvis sent it back to work 2 times and stopped there." in done["reply"]
    assert f"{VERDICT} the tests (`{TEST_CMD}`) still fail." in done["reply"]


@LANES
async def test_a_point_out_of_reach_is_answered_blocked_and_said_under_the_reply(env, lane, monkeypatch):
    platform, ws, wire = env

    def blocked(model: Model):
        note = _last_note(model)
        assert "## Points you said are not met\n1. set X to 2 in app.py and run the tests" in note
        assert F.AGAIN_POINTS_ASK in note and "## Tests" not in note
        return "1. blocked: the tests need your VPN sign-in; connect it and tell me to carry on."

    model = wire([
        _write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.",
        "1. not met: I could not run the remote tests.", blocked,
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 2 and not model.moves
    fin = done["finish"]
    assert fin["tests"] == "passed" and fin["unmet"] == 1
    assert fin["again"] == {"rounds": 1, "ended": "blocked", "open": 1}
    # The model's "blocked" line is what the user must read: it stays.
    assert "1. blocked: the tests need your VPN sign-in" in done["reply"]
    assert done["reply"].endswith(
        f"{VERDICT} this is not finished. 1 point of your request is not done. Tell it to keep "
        "going and it will carry on._"
    )


@LANES
async def test_a_point_fixed_and_never_confirmed_is_asked_once_and_then_not_claimed(env, lane, monkeypatch):
    """The model said "not met", changed a file, and ignored the question
    about it: nothing says the point is still open, so it is not claimed
    (and the reply is not called unfinished)."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("1. not met: X is 11.", _edit("X = 11", "X = 2", "fix")), _shell(TEST_CMD, "s2"), "Fixed.",
        "Nothing more to do.",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 2 and not model.moves
    assert done["finish"]["again"] == {"rounds": 1, "ended": "done", "open": 0}
    assert VERDICT not in done["reply"]


@LANES
async def test_a_reply_with_no_step_left_ends_and_says_so(env, lane, monkeypatch):
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        "The tests fail.", "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch, round_budget=5)
    assert len(model.notes()) == 1 and model.moves == ["never reached"]
    assert done["finish"]["again"] == {"rounds": 0, "ended": "steps", "open": 0}
    assert "It ran out of steps for this reply." in done["reply"]


@LANES
async def test_a_folder_can_turn_the_loop_off(env, lane, monkeypatch):
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")
    cfg = ws / ".ironjarvis" / "harness"
    cfg.mkdir(parents=True)
    (cfg / "finish.toml").write_text("again = false\n", encoding="utf-8")
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        "The tests fail, but that is fine.", "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 1 and model.moves == ["never reached"]
    assert "again" not in done["finish"] and VERDICT not in done["reply"]
    assert done["reply"] == "Done: X is set.\n\nThe tests fail, but that is fine."


@LANES
async def test_a_reply_whose_check_is_clean_is_exactly_as_before(env, lane, monkeypatch):
    """Nothing open: one note, one answer, no extra call, no extra key."""
    platform, ws, wire = env
    model = wire([
        _write("app.py", "X = 2\n"), _shell(TEST_CMD), "Set X to 2; the tests pass.",
        "1. met: app.py says X = 2 and the tests pass.\nNothing needed changing.", "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 1 and model.moves == ["never reached"]
    assert done["finish"] == {"tests": "passed", "command": TEST_CMD, "removed": 0, "kept": 0,
                              "points": 1, "unmet": 0, "after": "", "reused": True}
    assert done["reply"] == "Set X to 2; the tests pass.\n\nNothing needed changing."


@LANES
async def test_met_lines_written_in_a_later_answer_are_not_shown(env, lane, monkeypatch):
    """Seen live: the model looked once more before answering the note, so
    its "N. met" lines came in a LATER answer and stayed in the reply. They
    are the check's bookkeeping (the record counts them), not what a person
    reads."""
    platform, ws, wire = env
    look = [ToolCall(id="look", name="read_file", arguments={"path": "app.py"})]
    model = wire([
        _write("app.py", "X = 2\n"), _shell(TEST_CMD), "Set X to 2; the tests pass.",
        ("Let me look once more.", look),
        "1. met: app.py says X = 2 and the tests pass.\nNothing needed changing.", "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.notes()) == 1 and model.moves == ["never reached"]
    assert done["finish"]["points"] == 1 and done["finish"]["unmet"] == 0
    assert "1. met" not in done["reply"]
    assert done["reply"].endswith("Nothing needed changing.")


@LANES
async def test_a_file_the_request_lists_is_asked_for_once(env, lane, monkeypatch):
    platform, ws, wire = env
    text = "Deliverables:\n- app.py\n- NOTES.md\n\nSet X to 2 in app.py and run the tests."

    def make_it(model: Model):
        note = _last_note(model)
        assert "## Files the request names that are not there\n- NOTES.md" in note
        assert F.AGAIN_FILES_ASK in note
        return _write("NOTES.md", "X is 2 now.\n", "w2")

    model = wire([
        _write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.",
        "Checked: everything is in place.", make_it, "Added NOTES.md.",
    ])
    done, _frames = await _lane(lane, platform, _body(ws, text=text), monkeypatch)
    assert (ws / "NOTES.md").exists() and not model.moves
    assert done["finish"]["again"]["rounds"] == 1 and done["finish"]["again"]["ended"] == "done"
    assert VERDICT not in done["reply"]


@LANES
async def test_a_listed_file_never_made_is_named_as_a_fact_not_as_unfinished(env, lane, monkeypatch):
    platform, ws, wire = env
    text = "Deliverables:\n- app.py\n- NOTES.md\n\nSet X to 2 in app.py and run the tests."
    model = wire([
        _write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.",
        "Checked: everything is in place.", "The notes are in my reply instead.", "never reached",
    ])
    done, _frames = await _lane(lane, platform, _body(ws, text=text), monkeypatch)
    assert model.moves == ["never reached"], "a missing file is asked for once, never twice"
    assert done["reply"].endswith(
        f"{VERDICT} a file the request names is not there: `NOTES.md`. Tell it to keep going and "
        "it will carry on._"
    )
    assert "this is not finished" not in done["reply"]


async def test_a_declined_re_run_is_not_run_again_and_the_reply_says_what_is_known(env):
    """Stream lane, cards on: the user allows the model's command and the
    check's first run, and DECLINES the re-run's card. Nothing says the tests
    pass, so the reply says exactly what is known."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")
    cards: list[dict] = []

    def answer(frame: dict) -> str:
        cards.append(frame)
        return "deny" if len(cards) == 3 else "once"

    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        ("The test fails: fixing it.", _edit("X = 11", "X = 2", "fix")), "Fixed.", "never reached",
    ])
    out = await _stream(platform, _body(ws, approval_mode="approve_for_me"), answer=answer)
    done = out["done"]
    assert len(cards) == 3, "the model's command, the check's run, the re-run"
    assert model.moves == ["never reached"]
    assert done["finish"]["again"] == {"rounds": 0, "ended": "unchecked", "open": 0}
    assert done["finish"]["after"] == ""
    assert (
        f"{VERDICT} the tests (`{TEST_CMD}`) failed when they were last run, "
        "and were not run again after the last change."
    ) in done["reply"]


# --------------------------------------------------------------------------- #
# the loop's small parts
# --------------------------------------------------------------------------- #


def test_blocked_points_and_the_saved_record():
    text = "1. met: ok\n2. blocked: needs your key\n- **3. Blocked** — no access\n9. blocked: out of range\nblocked: 4"
    assert F.blocked_points(text, 3) == {2, 3}
    assert F.saved_again({"rounds": 2, "ended": "stuck", "open": 1}) == {"rounds": 2, "ended": "stuck", "open": 1}
    assert F.saved_again({"rounds": 0, "ended": ""}) == {"rounds": 0, "ended": "", "open": 0}
    for bad in (None, {}, {"rounds": True, "ended": "done"}, {"rounds": 99, "ended": "done"},
                {"rounds": 1, "ended": "whatever"}, {"rounds": "1", "ended": "done"}):
        assert F.saved_again(bad) is None
    base = {"tests": "failed", "command": "pytest", "removed": 0, "kept": 0, "points": 0, "unmet": 0, "after": ""}
    assert F.saved_record({**base, "again": {"rounds": 1, "ended": "limit", "open": 0}})["again"] == {
        "rounds": 1, "ended": "limit", "open": 0}
    assert "again" not in F.saved_record({**base, "again": {"rounds": 1, "ended": "nope"}})
    assert "again" not in F.saved_record(base)
    assert list(F.RECORD_KEYS).index("again") < list(F.RECORD_KEYS).index("review")


def test_the_folder_switch_is_read_and_marked():
    assert F.parse_config("again = false\n") == F.FinishConfig(again=False)
    assert F.FinishConfig(again=False).mark() == "finish-off:again"
    assert F.FinishConfig().mark() == ""
    with pytest.raises(ValueError):
        F.parse_config("again = 3\n")


def test_required_files_reads_a_tasks_list_inside_the_folder_only(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    listed = "Deliverables:\n- out/report.md\n- data.csv\n- ../outside.txt\n- //host/share/x.txt\n"
    got = required_files(listed, ws)
    assert [d.text for d in got] == ["out/report.md", "data.csv"]
    assert all(str(d.path).startswith(str(ws)) for d in got)
    # A one-line request names no required file (the task threshold).
    assert required_files("write notes.txt", ws) == []
    assert required_files("", ws) == [] and required_files(listed, None) == []


async def test_a_teams_reply_is_never_looped_and_gets_no_line(tmp_path):
    check = F.FinishCheck(engine=None, home=tmp_path, event_bus=None, session_id="build:t",
                          folder=tmp_path, mark=0, request="do it", team=object())
    check.ran = check._note_sent = True
    check._latest = F.TESTS_FAILED
    assert await check.prepare(reply="done", round_index=3, rounds=30, truncated=False, low_trust=False) is None
    assert await check.verdict_note() == "" and check.again_end == ""


async def test_the_loop_never_runs_on_a_cut_reply_or_in_low_trust(tmp_path):
    for kw in ({"truncated": True, "low_trust": False}, {"truncated": False, "low_trust": True}):
        check = F.FinishCheck(engine=None, home=tmp_path, event_bus=None, session_id="build:t",
                              folder=tmp_path, mark=0, request="do it")
        check.ran = check._note_sent = True
        check._latest = F.TESTS_FAILED
        assert await check.prepare(reply="done", round_index=3, rounds=30, **kw) is None
        assert check.again_rounds == 0 and not check.settled()


# --------------------------------------------------------------------------- #
# the give-up nudge
# --------------------------------------------------------------------------- #

GIVES_UP = [
    "I tried several approaches but I couldn't get the tests to pass.",
    "Unfortunately this can't be done with the current setup.",
    "I was not able to fix the import error.\n\nHere is what I tried:\n- pinning the version\n- a shim",
    "I am unable to complete this task.",
    "The build keeps failing. You'll need to fix this manually.",
    "I can't run the tests because pytest is missing.",
    "After three attempts I'm giving up on the migration.",
    "It isn't possible to implement this without changing the API.",
    "Summary of the work:\n\n```\npytest -q\n```\n\nI could not make the last test pass.",
]
KEEPS_GOING = [
    "Done. All tests pass.",
    "You can't use await outside an async function, so I moved the call.",
    "I can't see any problems in the code. It is fine as it is.",
    "I couldn't run the tests at first, so I installed pytest.\n\nAll tests pass now.",
    "I can't proceed until I know: which database do you use?",
    "Blocked: I could not sign in to GitHub; run gh auth login and tell me to carry on.",
    # It says what it could not do AND already ends on its "Blocked:" line:
    # that is the answer the nudge asks for, so it is not sent back.
    "I couldn't get the tests to pass.\n\nBlocked: the build box is offline; turn it on and tell me to carry on.",
    "I was unable to fix the import error.\n\n**Blocked:** the package index needs your sign-in.",
    "Fixed the bug. It was hard to reproduce before because of caching, and now it works.",
    "The function returns None when the file cannot be read.\n\n```python\n# I can't fix this here\n```\n\nAll good.",
    "> I couldn't get this to work\n\nThat was the old comment; I replaced it and the tests pass.",
    "",
]


@pytest.mark.parametrize("text", GIVES_UP)
def test_these_endings_give_up(text):
    assert G.gives_up(text) is True


@pytest.mark.parametrize("text", KEEPS_GOING)
def test_these_endings_do_not(text):
    assert G.gives_up(text) is False


def test_the_nudge_fires_once_and_only_where_the_closing_offer_would():
    ok = dict(has_calls=False, round_index=1, rounds=30, armed=True)
    text = GIVES_UP[0]
    g = G.GiveUp()
    assert g.wants(text, **ok) and g.pending
    assert g.take() == G.GIVE_UP_NUDGE and g.fired and not g.pending and g.just_fired
    assert not g.wants(text, **ok), "once per reply"
    for off in ({"has_calls": True}, {"armed": False}, {"truncated": True}, {"low_trust": True},
                {"plan_mode": True}, {"after_finish": True}, {"round_index": 28}):
        assert not G.GiveUp().wants(text, **{**ok, **off}), off
    assert not G.GiveUp().wants("Done. All tests pass.", **ok)
    assert "authorises nothing" in G.GIVE_UP_NUDGE and '"Blocked:"' in G.GIVE_UP_NUDGE


@LANES
async def test_a_reply_that_gives_up_is_sent_back_once(env, lane, monkeypatch):
    platform, ws, wire = env
    seen: list[dict] = []

    async def spy(event_type, payload, **kw):  # the autonomy.nudged event
        if str(getattr(event_type, "value", event_type)) == "autonomy.nudged":
            seen.append(dict(payload))
        return await real(event_type, payload, **kw)

    real = platform.event_bus.publish
    monkeypatch.setattr(platform.event_bus, "publish", spy)

    def second(model: Model):
        assert _last_note(model) == G.GIVE_UP_NUDGE
        return "Blocked: I tried the config file and the env var; the server needs your password."

    model = wire(["I couldn't get this to work.", second, "never reached"])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert model.moves == ["never reached"], "once: the Blocked line ends the reply"
    assert done["reply"] == "Blocked: I tried the config file and the env var; the server needs your password."
    assert [p.get("kind") for p in seen] == ["gave_up"]
    assert "finish" not in done, "nothing changed: the finish check did not run"


@LANES
async def test_plain_chat_is_never_nudged(env, lane, monkeypatch):
    platform, ws, wire = env
    model = wire(["I couldn't get this to work.", "never reached"])
    done, _frames = await _lane(lane, platform, _body(ws), monkeypatch, harness=None, session_id="chat")
    assert model.moves == ["never reached"]
    assert done["reply"].startswith("I couldn't get this to work.")


def test_the_new_readings_stay_fast_on_hostile_text():
    """Linear: a child process with a deadline (a regex that backtracks would
    hold the event loop, and the GIL)."""
    code = (
        "from iron_jarvis.harness import finish as F, give_up as G\n"
        "bad = ['I ' + 'just ' * 20000 + 'x', '1' * 60000 + '. blocked', "
        "'I can' + chr(39) + 't ' + 'really ' * 20000, ' ' * 80000 + 'unable to ' * 5000, "
        "('1. blocked: x\\n' * 20000), chr(10) * 60000 + '1. met', '1.' + ' ' * 60000 + 'x', "
        "'*' * 50000, ' ' * 60000 + 'blocked']\n"
        "for t in bad:\n"
        "    G.gives_up(t); F.blocked_points(t, 50); F.answered_points(t, 50); F.drop_met_lines(t, 50)\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == "ok", out.stderr[-800:]
