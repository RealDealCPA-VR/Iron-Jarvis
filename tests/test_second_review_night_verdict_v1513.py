"""Second review T5, T6, T7, T10 (v1.513.0): what the night shift says in the
morning must be true.

* T5. A night task's "Done" / "Needs you" was read from the finish check's
  FIRST answer (``finish["unmet"]``, kept that way by design), not from what
  was still open when the reply ended (``finish["again"]``). A point the
  check-again loop got fixed read "Needs you"; one a later round answered
  "blocked" read "Done". The records here are made by the REAL finish check
  (``FinishCheck.record``) and kept the way a reply line keeps them
  (``finish.saved_record``) — the older test built them by hand with no
  ``again`` key, which is how this was missed.
* T6. The reason for open points was filed as "info" (``add_note`` keeps
  only ``NOTE_KINDS`` and "unmet" was not one), so a "Needs you" row and the
  phone line carried no reason. The note is written THROUGH ``add_note``
  here and read back from the morning row; every kind a writer uses must be
  storable.
* T7. A task that changed nothing and passed no check was announced "Done".
* T10. "Night tasks run now, until 7:00 AM" although a started task runs to
  its own time limit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_morning as M
from iron_jarvis.harness import night_window as W
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import make_repo, no_real_gh
from tests.test_build_sessions_v1367 import asgi, wire
from tests.test_night_morning_run_v1399 import PY, NightScript, _ended, _local, _repo_with_tests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"


# --------------------------------------------------------------------------- #
# records made by the real finish check
# --------------------------------------------------------------------------- #


def _check(tmp_path: Path, request: str = "Fix the page title and the footer link.") -> F.FinishCheck:
    check = F.FinishCheck(engine=None, home=tmp_path, event_bus=None, session_id="build:t",
                          folder=tmp_path, mark=0, request=request)
    # The check ran and its note went out (what the lanes leave behind).
    check.ran = check._note_sent = True
    return check


async def _ends(check: F.FinishCheck, round_index: int):
    """The model ends the reply on words: what the check does next."""
    return await check.prepare(reply="Done.", round_index=round_index, rounds=30, truncated=False,
                               low_trust=False)


def _line(rec: dict) -> dict:
    """The reply line as the session keeps it."""
    return {"role": "assistant", "content": "Done: the page is fixed.", "finish": F.saved_record(rec)}


async def _fixed_by_the_loop(tmp_path: Path) -> dict:
    """(A) the first answer says "2. not met"; the loop sends it back; the
    model fixes it and the loop ends "done" with nothing open."""
    check = _check(tmp_path)
    check.points = ["Fix the page title", "Fix the footer link"]
    check.heard("1. met: the title is fixed\n2. not met: the footer link still points nowhere")
    assert await _ends(check, 3) == []  # sent back: point 2 is open
    await check.note()
    check.heard("2. met: the footer link now points home")
    assert await _ends(check, 5) is None  # nothing open: the reply ends
    return await check.record()


async def _blocked_after_the_tests_pass(tmp_path: Path) -> dict:
    """(B) the first answer says all met, the tests fail, a later round
    writes "3. blocked: ..."; Iron Jarvis's own re-run of the tests passes."""
    check = _check(tmp_path, "Fix the title, the footer and the sign-in page.")
    check.points = ["Fix the title", "Fix the footer", "Fix the sign-in page"]
    check.runs = [F._Run(command="python -m unittest", args={}, source="folder",
                         verdict=F.TESTS_FAILED, exit_code=1)]
    check.tests = check._latest = F.TESTS_FAILED
    check.heard("1. met: done\n2. met: done\n3. met: done")
    assert await _ends(check, 3) == []  # sent back: the tests fail
    await check.note()
    check.heard("3. blocked: the sign-in page needs the site's own account, which only you can give")
    # The check's own re-run (what the lanes hand ``settled`` once it ran).
    check._again_pending = True
    check._again_runs = [F._Run(command="python -m unittest", args={}, source="folder",
                                verdict=F.TESTS_PASSED, exit_code=0)]
    assert check.settled() is True  # nothing more to tell the model: the reply ends
    return await check.record()


async def _blocked_with_no_tests(tmp_path: Path) -> dict:
    """(B, with no tests) the first answer says all met, a file the request
    lists is not there, and the round it is asked for answers "1. blocked"."""
    check = _check(tmp_path, "Write the monthly report.\nDeliverables:\n- out/report.md\n- data.csv\n")
    check.points = ["Write out/report.md", "Write data.csv"]
    check.heard("1. met: written\n2. met: written")
    assert await _ends(check, 3) == []  # sent back: the listed files are not there
    await check.note()
    check.heard("1. blocked: the figures come from a server only you can reach")
    assert await _ends(check, 5) is None
    return await check.record()


async def test_a_point_the_loop_got_fixed_reads_done(tmp_path):
    rec = await _fixed_by_the_loop(tmp_path)
    assert rec["unmet"] == 1 and rec["again"] == {"rounds": 1, "ended": "done", "open": 0}
    line = _line(rec)
    state, notes = N.outcome_of({"id": "nt1", "notes": []}, line)
    assert state == N.DONE, notes
    assert not [n for n in notes if n[0] == "unmet"], notes
    assert N.finish_needs_you(line["finish"]) is False


async def test_a_point_blocked_in_a_later_round_reads_needs_you_with_its_count(tmp_path):
    rec = await _blocked_after_the_tests_pass(tmp_path)
    assert rec["unmet"] == 0 and rec["after"] == "passed"
    assert rec["again"] == {"rounds": 1, "ended": "blocked", "open": 1}
    state, notes = N.outcome_of({"id": "nt1", "notes": []}, _line(rec))
    assert state == N.NEEDS_YOU
    assert ("unmet", "1 point of the request still needs work.") in notes


async def test_a_blocked_point_with_no_tests_is_never_done(tmp_path):
    rec = await _blocked_with_no_tests(tmp_path)
    assert rec["tests"] == "none" and rec["unmet"] == 0
    assert rec["again"] == {"rounds": 1, "ended": "blocked", "open": 1}
    line = _line(rec)
    state, notes = N.outcome_of({"id": "nt1", "notes": []}, line)
    assert state == N.NEEDS_YOU
    assert ("unmet", "1 point of the request still needs work.") in notes
    assert N.finish_needs_you(line["finish"]) is True


@pytest.mark.parametrize("ended", ["stuck", "limit", "steps", "unchecked", "blocked"])
def test_a_loop_that_ended_unfinished_with_nothing_counted_needs_you_and_says_so(ended):
    """The tests pass and no point is open, yet the loop did not end "done":
    the ending alone says it is not finished, and the note says so in the
    coding session's own words."""
    fin = {"tests": "passed", "command": "pytest", "removed": 0, "kept": 0, "points": 2, "unmet": 0,
           "again": {"rounds": 2, "ended": ended, "open": 0}}
    line = {"role": "assistant", "content": "Done.", "finish": F.saved_record(fin)}
    state, notes = N.outcome_of({"id": "nt1", "notes": []}, line)
    assert state == N.NEEDS_YOU
    assert ("unmet", N.NOT_FINISHED_WORDS) in notes
    assert N.finish_needs_you(line["finish"]) is True


@pytest.mark.parametrize("ended", ["stuck", "limit", "steps", "unchecked", "blocked"])
def test_a_loop_that_ended_with_the_tests_failing_leaves_the_reason_to_the_tests_note(ended):
    """Lead review: the tests passed first and FAILED in a later run
    (``after``). They are failing as last seen, so the task needs you, and the
    reason is the route's own "Its tests failed." note, never the general
    "not finished" line beside it."""
    fin = {"tests": "passed", "command": "pytest", "removed": 0, "kept": 0, "points": 2, "unmet": 0,
           "after": "failed", "again": {"rounds": 2, "ended": ended, "open": 0}}
    line = {"role": "assistant", "content": "Done.", "finish": F.saved_record(fin)}
    state, notes = N.outcome_of({"id": "nt1", "notes": []}, line)
    assert state == N.NEEDS_YOU
    assert ("unmet", N.NOT_FINISHED_WORDS) not in notes
    assert N.finish_tests(line["finish"]) == "failed"


@pytest.mark.parametrize("again", [None, "junk", {"rounds": 1, "ended": "whatever"}, {"rounds": True}])
def test_with_no_readable_loop_the_first_answer_is_read_and_junk_reads_as_no(again):
    base = {"tests": "passed", "unmet": 2}
    fin = dict(base) if again is None else {**base, "again": again}
    assert N.finish_unmet(fin) == 2 and N.finish_needs_you(fin) is True
    assert N.finish_loop_unfinished(fin) is False
    assert N.finish_needs_you({"tests": "passed", "unmet": 0, "again": "junk"}) is False
    assert N.finish_needs_you("junk") is False and N.finish_unmet(None) == 0


def test_every_loop_ending_but_done_is_unfinished():
    """A new ending in ``finish.AGAIN_ENDS`` is unfinished unless it is "done"."""
    for ended in F.AGAIN_ENDS:
        fin = {"tests": "none", "unmet": 0, "again": {"rounds": 1, "ended": ended, "open": 0}}
        assert N.finish_loop_unfinished(fin) is (ended != F.AGAIN_DONE), ended
    assert N.finish_loop_unfinished({"tests": "none", "again": {"rounds": 0, "ended": "", "open": 0}}) is False


# --------------------------------------------------------------------------- #
# T6: the note goes through add_note and comes back on the morning row
# --------------------------------------------------------------------------- #


def _engine(tmp_path: Path):
    engine = make_engine(tmp_path / "night.db")
    init_db(engine)
    return engine


def _end_task(engine, words: str, state: str, notes) -> dict:
    task = N.add_task(engine, folder="C:\\work\\site", words=words)
    assert N.mark_running(engine, task["id"], night="2026-10-26")
    for kind, text in notes:
        assert N.add_note(engine, task["id"], kind, text)
    assert N.finish_task(engine, task["id"], state)
    return N.get_task(engine, task["id"])


async def test_the_open_points_note_is_kept_as_its_own_kind_and_the_row_and_phone_say_why(tmp_path):
    rec = await _blocked_with_no_tests(tmp_path)
    state, notes = N.outcome_of({"id": "nt1", "notes": []}, _line(rec))
    engine = _engine(tmp_path)
    task = _end_task(engine, "Write the monthly report", state, notes)
    assert {"kind": "unmet", "text": "1 point of the request still needs work."} in [
        {"kind": n["kind"], "text": n["text"]} for n in task["notes"]]
    row = M.summary_row(task)
    assert row["state"] == "needs_you"
    assert row["needs"][0] == "1 point of the request still needs work."
    assert M.notice_words([row])["title"] == "Last night: 1 needs you"
    assert M.phone_line(row).startswith("• Needs you: Write the monthly report: 1 point of the request still needs work")


def _written_kinds() -> set[str]:
    """Every note kind a writer of a night task's notes uses, read from the
    source: the settle's and ``outcome_of``'s ``(kind, text)`` tuples, every
    ``add_note(..., kind, ...)``, and a night turn's ``.note(kind, ...)``."""
    kinds: set[str] = set()
    tuple_rx = re.compile(r'(?:append|insert)\(\s*(?:0,\s*)?\(\s*"([a-z_]+)"\s*,')
    add_rx = re.compile(r'add_note\(\s*[\w.\[\]"\']+\s*,\s*[\w.\[\]"\']+\s*,\s*"([a-z_]+)"')
    turn_rx = re.compile(r'(?:_night_turn|_nt|night)\.note\(\s*"([a-z_]+)"(?:\s+if\s+[^,]+?\s+else\s+"([a-z_]+)")?')
    for rel in ("harness/night.py", "daemon/routes/night.py"):
        text = (SRC / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")
        kinds |= set(tuple_rx.findall(text))
        kinds |= set(add_rx.findall(text))
    for rel in ("daemon/mcp_turn.py", "daemon/chat_turn.py", "daemon/routes/chat.py", "agents/runtime.py"):
        text = (SRC / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")
        for pair in turn_rx.findall(text):
            kinds |= {k for k in pair if k}
    return kinds


def test_every_kind_a_writer_uses_is_storable_and_every_kind_the_morning_reads_can_be_stored():
    written = _written_kinds()
    # The reading found the writers (a scan that finds nothing checks nothing).
    assert {"unmet", "did_nothing", "commit", "tests", "outside", "declined", "unanswered",
            "low_trust", "emergency", "limit", "stopped", "restart", "account", "error", "env",
            "info"} <= written, written
    assert written <= set(N.NOTE_KINDS), sorted(written - set(N.NOTE_KINDS))
    read = set(M.NEEDS_KINDS) | set(M.STOP_KINDS) | {"outside", "error"}
    assert read <= set(N.NOTE_KINDS), sorted(read - set(N.NOTE_KINDS))


# --------------------------------------------------------------------------- #
# T7: a task that changed nothing
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


@pytest.mark.parametrize(("state", "fin", "changed", "try_on", "want"), [
    (N.DONE, None, False, False, True),
    (N.DONE, {"tests": "none"}, False, False, True),
    (N.DONE, {"tests": "passed"}, False, False, False),  # a check passed
    (N.DONE, {"tests": "failed", "after": "passed"}, False, False, False),
    (N.DONE, None, True, False, False),  # it changed something
    (N.DONE, None, False, True, False),  # tried on several models: their own branches
    (N.NEEDS_YOU, None, False, False, False),  # already needs you
    (N.STOPPED, None, False, False, False),
])
def test_the_did_nothing_reading(state, fin, changed, try_on, want):
    assert N.ended_doing_nothing(state, fin, changed=changed, try_on=try_on) is want


@pytest.mark.usefixtures("no_network")
async def test_a_task_that_changed_nothing_ends_needs_you_with_its_line(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    wire(app, NightScript({"TASK-LOOK": [
        [ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"})],
        "Done TASK-LOOK: I could not find the menu's code.",
    ]}))
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "TASK-LOOK: fix the menu"})
    assert status == 201, task
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["state"] == N.NEEDS_YOU, ended["notes"]
    assert any(n["kind"] == "did_nothing" and n["text"] == N.DID_NOTHING_WORDS for n in ended["notes"])
    assert ended["morning"]["commit_note"] == M.NOTHING_CHANGED
    [summary] = M.list_summaries(app.state.platform.engine)
    [row] = summary["rows"]
    assert row["state"] == "needs_you" and row["needs"][0] == "It ended without changing anything."
    assert M.notice_words([row])["title"] == "Last night: 1 needs you"
    assert "It ended without changing anything" in M.phone_line(row)


@pytest.mark.usefixtures("no_network")
async def test_a_task_that_changed_files_and_passed_its_check_still_ends_done(tmp_path, monkeypatch):
    repo = _repo_with_tests(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    wire(app, NightScript({"TASK-ADD": [
        [ToolCall(id="d1", name="Write", arguments={
            "file_path": "calc.py", "content": "def add(a, b):\n    return a + b\n"})],
        [ToolCall(id="d2", name="Bash", arguments={"command": f'"{PY}" -m unittest -q test_calc'})],
        "Done TASK-ADD: add() adds now and its test passes.",
    ]}))
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "TASK-ADD: fix add()"})
    assert status == 201, task
    await night_routes.night_pass(d)
    ended = await _ended(app, d, task["id"])
    assert ended["state"] == N.DONE, ended["notes"]
    assert ended["commit_sha"]
    assert not [n for n in ended["notes"] if n["kind"] == "did_nothing"]


# --------------------------------------------------------------------------- #
# T10: the window's sentence
# --------------------------------------------------------------------------- #


def test_the_window_says_tasks_start_until_its_end_and_run_to_their_own_limit():
    at = _local(23, 30)
    assert W.window_words(at, "23:00", "07:00") == (
        "Night tasks start now, until 7:00 AM. One that has started runs to its own time limit.")
    assert W.window_words(_local(15, 0), "23:00", "07:00") == "Starts at 11:00 PM tonight."


# --------------------------------------------------------------------------- #
# Lead review of T5: the tests as they were LAST seen
# --------------------------------------------------------------------------- #
import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "fin,last,needs",
    [
        ({"tests": "failed", "after": "passed"}, "passed", False),  # fixed in the same reply
        ({"tests": "passed", "after": "failed"}, "failed", True),   # broken by a later change
        ({"tests": "failed"}, "failed", True),
        ({"tests": "passed"}, "passed", False),
        ({"tests": "not_run", "after": "passed"}, "passed", False),
        ({"tests": "not_run"}, "", False),
        ({"tests": "failed", "after": "junk"}, "failed", True),
        ("junk", "", False),
        (None, "", False),
    ],
)
def test_the_tests_are_read_as_they_were_last_seen(fin, last, needs):
    """The finish record keeps the first run in ``tests`` and a later run in
    ``after``. The daemon read only the first: a fix that made the tests pass
    still said "Needs you: its tests failed", and a later failure said
    "Done". The screen's own reading (``testsFailing``) is the model."""
    from iron_jarvis.harness import night as N

    assert N.finish_tests(fin) == last
    assert N.finish_needs_you(fin) is needs
