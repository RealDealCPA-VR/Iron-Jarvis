"""Intelligence plan, release 5: the ONE record of checks, and its line.

The table (``checkrecord``): a write is read back for its owner only, a
second look at the same file replaces the first, a row holds a file's NAME
and a masked, clipped detail, a new chat's first reply's rows follow the chat
when it is named, a deleted chat's rows go with it, and old rows are removed.
The line and the list a page draws are built from rows alone.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.checks import record as R
from iron_jarvis.checks.record import Check, CheckRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.app import create_app

SHEET = [
    Check(R.FILE_OPENS, R.PASSED, "opens", file="summary.xlsx"),
    Check(R.SHEET_ERRORS, R.PASSED, "no error cells", file="summary.xlsx"),
    Check(R.SHEET_TOTALS, R.PASSED, "3 totals add up", file="summary.xlsx"),
]


def _all(engine) -> list[CheckRecord]:
    with session_scope(engine) as db:
        return list(db.exec(select(CheckRecord).order_by(CheckRecord.created_at, CheckRecord.id)))


def _row(check, result, detail, file="", by="rule", step="", folder=""):
    return {"check": check, "result": result, "detail": detail, "file": file, "by": by, "step": step, "folder": folder}


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #
def test_the_table_is_made_with_the_database_and_a_write_is_read_back_for_its_owner_only(platform):
    from sqlalchemy import inspect

    from iron_jarvis.core.db import _LATE_MODEL_MODULES

    # Registered with the tables that are made (and given new columns) at start-up.
    assert "..checks.record" in _LATE_MODEL_MODULES
    assert inspect(platform.engine).has_table("checkrecord"), "a fresh database has no record of checks"
    assert R.write(platform.engine, SHEET, thread_id="thread_a", turn_id="turn_1") == 3
    assert R.write(platform.engine, [Check(R.CLAIMS, R.LOOK, "it may say a file was saved that no step wrote")],
                   thread_id="thread_b", turn_id="turn_2") == 1
    assert R.write(platform.engine, [Check(R.STEP_CHECK, R.NOT_CHECKED, "its check did not run", by="Sonnet", step="2")],
                   session_id="session_9", run_id="run_9") == 1

    mine = R.for_turn(platform.engine, "turn_1")
    assert [(r["check"], r["result"], r["detail"], r["file"], r["by"]) for r in mine] == [
        (c.check, c.result, c.detail, c.file, "rule") for c in SHEET]
    assert {r["thread_id"] for r in mine} == {"thread_a"}
    assert [r["check"] for r in R.for_thread(platform.engine, "thread_b")] == [R.CLAIMS]
    (step,) = R.for_session(platform.engine, "session_9")
    assert (step["step"], step["by"], step["run_id"], step["result"]) == ("2", "Sonnet", "run_9", R.NOT_CHECKED)
    # Nothing is read without an owner, and the shared id "chat" is no owner.
    assert R.for_turn(platform.engine, "") == [] and R.for_thread(platform.engine, "") == []
    assert R.for_session(platform.engine, "chat") == []
    assert R.write(platform.engine, SHEET) == 0, "a row with no owner was written"
    assert R.write(platform.engine, SHEET, session_id="chat") == 0, "rows were filed under the shared id"
    assert len(_all(platform.engine)) == 5


def test_a_second_look_at_the_same_file_replaces_the_first(platform):
    eng = platform.engine
    R.write(eng, [Check(R.SHEET_TOTALS, R.LOOK, "B5 says 9 and the cells above it add up to 8", file="a.xlsx"),
                  Check(R.SHEET_TOTALS, R.NOT_CHECKED, "the check ran out of time", file="a.xlsx"),
                  Check(R.FILE_OPENS, R.PASSED, "opens", file="b.xlsx")], session_id="s1")
    R.write(eng, [Check(R.SHEET_TOTALS, R.PASSED, "2 totals add up", file="a.xlsx")], session_id="s1")
    got = {(r["check"], r["file"], r["result"]) for r in R.for_session(eng, "s1")}
    assert got == {(R.SHEET_TOTALS, "a.xlsx", R.PASSED), (R.FILE_OPENS, "b.xlsx", R.PASSED)}
    # Another owner's same check and file is untouched.
    R.write(eng, [Check(R.SHEET_ERRORS, R.FAILED, "x", file="a.xlsx")], session_id="s2")
    R.write(eng, [Check(R.SHEET_ERRORS, R.PASSED, "y", file="a.xlsx")], session_id="s1")
    assert [(r["result"], r["detail"]) for r in R.for_session(eng, "s2")] == [(R.FAILED, "x")]
    # ... and so is the same name in another folder (two files, told apart by their own field).
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="s.xlsx", folder="2023"),
                  Check(R.FILE_OPENS, R.FAILED, "it is empty", file="s.xlsx", folder="2024")], session_id="s3")
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="s.xlsx", folder="2024")], session_id="s3")
    assert sorted((r["folder"], r["result"]) for r in R.for_session(eng, "s3")) == [
        ("2023", R.PASSED), ("2024", R.PASSED)]


@pytest.mark.parametrize("newest", [False, True])
def test_rows_written_in_one_clock_tick_keep_the_order_they_were_written(platform, monkeypatch, newest):
    """A Windows clock is coarse: two writes can carry one time. Oldest
    first (and newest first) still follows the order they were written,
    never the rows' random ids."""
    from datetime import datetime, timezone
    tick = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(R, "utcnow", lambda: tick)
    eng = platform.engine
    for n in range(12):
        R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file=f"f{n:02d}.md")], turn_id="tick")
    got = [r["file"] for r in R._read(eng, R.CheckRecord.turn_id == "tick", newest=newest)]
    want = [f"f{n:02d}.md" for n in range(12)]
    assert got == (want[::-1] if newest else want)


def test_two_chats_that_send_one_turn_id_never_replace_each_others_rows(platform):
    """Decision 11. A turn's id is the caller's own choice. Chat B's reply,
    sent under the id chat A used, with a file of the same name, deleted chat
    A's row: the replace key now holds the chat."""
    eng = platform.engine
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="summary.md"),
                  Check(R.DOC_TEXT, R.PASSED, "has text", file="summary.md")], thread_id="chat_a", turn_id="same")
    R.write(eng, [Check(R.DOC_TEXT, R.LOOK, "it is empty", file="summary.md")], thread_id="chat_b", turn_id="same")
    assert sorted((r["check"], r["result"]) for r in R.for_thread(eng, "chat_a")) == [
        (R.DOC_TEXT, R.PASSED), (R.FILE_OPENS, R.PASSED)], "another chat's write took this chat's row"
    assert [(r["check"], r["result"]) for r in R.for_thread(eng, "chat_b")] == [(R.DOC_TEXT, R.LOOK)]
    # The same chat's second look at the same file still replaces its own row.
    R.write(eng, [Check(R.DOC_TEXT, R.LOOK, "Iron Jarvis found no text in it", file="summary.md")],
            thread_id="chat_a", turn_id="same")
    assert sorted((r["check"], r["result"]) for r in R.for_thread(eng, "chat_a")) == [
        (R.DOC_TEXT, R.LOOK), (R.FILE_OPENS, R.PASSED)]
    # A reply with NO chat yet replaces nothing at all: whose a row of that turn id is cannot be told (two
    # new chats may send one id). And a replace of made-file checks keeps to its chat.
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="summary.md")], turn_id="same")
    R.write(eng, [Check(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", file="summary.md")], turn_id="same")
    assert [(r["thread_id"], r["result"]) for r in R.for_turn(eng, "same") if not r["thread_id"]] == [
        ("", R.PASSED), ("", R.NOT_CHECKED)]
    assert R.write(eng, [], turn_id="same", replace_content=True) == 0
    assert len([r for r in R.for_turn(eng, "same") if not r["thread_id"]]) == 2
    assert R.write(eng, [], thread_id="chat_b", turn_id="same", replace_content=True) == 0
    assert R.for_thread(eng, "chat_b") == [] and len(R.for_thread(eng, "chat_a")) == 2
    # A RUN's rows are its session's, whatever chat they sit under (it may be settled again under another answer).
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="a.md")], session_id="run_x", thread_id="chat_a")
    R.write(eng, [Check(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", file="a.md")], session_id="run_x")
    assert [(r["thread_id"], r["result"]) for r in R.for_session(eng, "run_x")] == [("", R.NOT_CHECKED)]


def test_a_settle_that_replaces_takes_every_earlier_check_of_a_file_with_it(platform):
    """``replace_content``: a run that is settled again says what its files
    hold afresh. Checks of files (whatever file) from the earlier settle go,
    in the same transaction, even when nothing new is written; the run's
    other rows (a step's check, the reply's own) stay."""
    eng = platform.engine
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="old.xlsx"),
                  Check(R.SHEET_ERRORS, R.FAILED, "A1 holds #DIV/0!", file="old.xlsx"),
                  Check(R.SHEET_TOTALS, R.LOOK, "B4 says 650 and the cells above it add up to 300", file="budget.xlsx"),
                  Check(R.STEP_CHECK, R.PASSED, "it passed its check", step="1"),
                  Check(R.CLAIMS, R.LOOK, "it may say a file was saved that no step wrote")], session_id="run_a")
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="other.xlsx")], session_id="run_b")
    assert R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="new.xlsx")], session_id="run_a",
                   replace_content=True) == 1
    assert sorted((r["check"], r["file"]) for r in R.for_session(eng, "run_a")) == [
        (R.CLAIMS, ""), (R.FILE_OPENS, "new.xlsx"), (R.STEP_CHECK, "")]
    assert R.write(eng, [], session_id="run_a", replace_content=True) == 0
    assert sorted(r["check"] for r in R.for_session(eng, "run_a")) == [R.CLAIMS, R.STEP_CHECK]
    assert [r["file"] for r in R.for_session(eng, "run_b")] == ["other.xlsx"], "another run's rows went with it"
    # Without the switch a write only replaces the same check of the same file (a chat reply's write).
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="a.md")], turn_id="t1")
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="b.md")], turn_id="t1")
    assert sorted(r["file"] for r in R.for_turn(eng, "t1")) == ["a.md", "b.md"]
    # Rows owned by a run id alone (no turn, no session): a replace for one run takes that run's only,
    # and a replace with NO owner takes nothing (it would match every such row).
    def by_run() -> list:
        with session_scope(eng) as db:
            return sorted((r.run_id, r.file) for r in db.exec(
                select(CheckRecord).where(CheckRecord.run_id.in_(["wf_1", "wf_2"]))).all())  # type: ignore[attr-defined]

    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="one.md")], run_id="wf_1")
    R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="two.md")], run_id="wf_2")
    assert R.write(eng, [], replace_content=True) == 0
    assert by_run() == [("wf_1", "one.md"), ("wf_2", "two.md")], "a write with no owner removed something"
    assert R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="next.md")], run_id="wf_1", replace_content=True) == 1
    assert by_run() == [("wf_1", "next.md"), ("wf_2", "two.md")]


def test_problem_is_an_allowlist_of_facts_and_every_other_check_that_did_not_pass_is_worth_a_look(platform):
    """THE BLANKET. "Problem" is not a default: only a row of a check on the
    allowlist may be ``failed``. A ``failed`` row of ANY other check code the
    package can write, whoever built it, is stored, read, shown and listed
    as "Worth a look"."""
    assert R.PROBLEM_CHECKS == {R.FILE_OPENS, R.SHEET_ERRORS, R.TESTS, R.REQUIRED_FILE}
    assert R.PROBLEM_CHECKS < R.CHECKS and len(R.CHECKS) == 12
    for check in sorted(R.CHECKS | {"a_check_nobody_knows"}):
        may = check in R.PROBLEM_CHECKS
        stored = R._clean(Check(check, R.FAILED, "it did not pass", file="a.xlsx", step="2"))
        assert stored.result == (R.FAILED if may else R.LOOK), check
        R.write(platform.engine, [Check(check, R.FAILED, "it did not pass", file="a.xlsx", step="2")],
                turn_id=f"t_{check}")
        (row,) = R.for_turn(platform.engine, f"t_{check}")
        assert row["result"] == (R.FAILED if may else R.LOOK), check
        old = _row(check, R.FAILED, "it did not pass", "a.xlsx", step="2")     # a row that got in another way
        line, tone = R.line_for([old])
        assert tone == "warn"
        assert ("roblem" in line) is may and ("Worth a look" in line) is (not may), (check, line)
        shown = R.payload([old])["checks"][0]
        assert (shown["result"], shown["result_words"]) == (("failed", "Problem") if may else ("look", "Worth a look"))
    # Every other result is kept as it is, for every check.
    for check in sorted(R.CHECKS):
        for result in (R.PASSED, R.LOOK, R.NOT_CHECKED):
            assert R._clean(Check(check, result, "x")).result == result


def test_a_row_holds_a_name_a_masked_clipped_detail_and_a_known_result(platform, tmp_path):
    secret = "sk-" + "a1B2c3D4" * 4            # the shape the shared mask knows, built here
    deep = tmp_path / "clients" / "acme" / "summary.xlsx"
    R.write(platform.engine, [
        Check(R.FILE_OPENS, "wonderful", f"key {secret} " + "x" * 900, file=str(deep), by="  My   Model  "),
    ], turn_id="t")
    (row,) = R.for_turn(platform.engine, "t")
    assert row["file"] == "summary.xlsx", "a folder was kept with the name"
    assert secret not in row["detail"] and len(row["detail"]) <= R.DETAIL_MAX
    assert row["result"] == R.NOT_CHECKED, "an unknown result was stored as given"
    assert row["by"] == "My Model"
    assert R.file_name("C:\\work\\a b.xlsx") == "a b.xlsx" and R.file_name("/srv/x/report.pdf") == "report.pdf"


def test_one_write_is_capped_and_never_raises(platform):
    many = [Check(R.FILE_OPENS, R.PASSED, "opens", file=f"f{i}.txt") for i in range(R.MAX_ROWS_PER_WRITE + 25)]
    assert R.write(platform.engine, many, turn_id="t") == R.MAX_ROWS_PER_WRITE
    assert R.write(object(), SHEET, turn_id="t") == 0          # no database: nothing, and no error
    assert R.for_turn(object(), "t") == []
    assert R.prune(object()) == 0 and R.home_turn(object(), "t", "x") == 0


async def test_note_writes_off_the_event_loop(platform, monkeypatch):
    import threading

    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = R.write

    def watched(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(R, "write", watched)
    assert await R.note(platform.engine, SHEET, turn_id="t") == 3
    assert seen and seen[0] != loop_thread, "the record was written on the event loop"
    assert await R.note(platform.engine, [], turn_id="t") == 0 and len(seen) == 1


def test_a_new_chats_first_replys_rows_follow_the_chat_when_it_is_named(platform):
    R.write(platform.engine, SHEET, turn_id="turn_new")
    R.write(platform.engine, SHEET[:1], turn_id="turn_other", thread_id="thread_other")
    assert R.for_thread(platform.engine, "thread_new") == []
    assert R.home_turn(platform.engine, "turn_new", "thread_new") == 3
    assert len(R.for_thread(platform.engine, "thread_new")) == 3
    # A row that already names a chat is never moved.
    assert R.home_turn(platform.engine, "turn_other", "thread_new") == 0
    assert len(R.for_thread(platform.engine, "thread_other")) == 1


def test_old_rows_are_removed_by_the_tidy_up_and_by_the_first_write_of_a_day(platform):
    eng = platform.engine
    R.write(eng, SHEET, turn_id="old")
    R.write(eng, SHEET[:1], turn_id="new")
    with session_scope(eng) as db:
        for row in db.exec(select(CheckRecord).where(CheckRecord.turn_id == "old")):
            row.created_at = utcnow() - timedelta(days=R.KEEP_DAYS + 1)
            db.add(row)
        db.commit()
    assert R.prune(eng, now=utcnow() - timedelta(days=5)) == 0          # not old enough yet
    assert R.prune(eng) == 3
    assert [r.turn_id for r in _all(eng)] == ["new"]

    # ... and the first write of a day does it too (its clock is handed in).
    day = 4_000_000_000.0          # later than any real write above
    R.write(eng, SHEET, turn_id="old2", clock=lambda: day)
    with session_scope(eng) as db:
        for row in db.exec(select(CheckRecord).where(CheckRecord.turn_id == "old2")):
            row.created_at = utcnow() - timedelta(days=R.KEEP_DAYS + 1)
            db.add(row)
        db.commit()
    R.write(eng, SHEET[:1], turn_id="same_day", clock=lambda: day + 3_600)
    assert "old2" in {r.turn_id for r in _all(eng)}, "it was removed twice in one day"
    R.write(eng, SHEET[:1], turn_id="next_day", clock=lambda: day + 25 * 3_600)
    assert "old2" not in {r.turn_id for r in _all(eng)}


def test_the_daily_tidy_up_calls_the_removal():
    src = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "app.py"
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    loop = text[text.index("async def _event_prune_loop"):text.index("async def _repl_sweep_loop")]
    assert "from ..checks.record import prune as _prune_checks" in loop
    assert "await asyncio.to_thread(_prune_checks, platform.engine)" in loop


def test_deleting_a_chat_removes_its_checks(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        engine = client.app.state.platform.engine
        made = client.put("/chat/threads/new", json={"title": "t", "messages": [{"role": "user", "content": "hi"}]})
        assert made.status_code == 200, made.text
        tid = made.json()["id"]
        R.write(engine, SHEET, thread_id=tid, turn_id="turn_1")
        R.write(engine, SHEET[:1], thread_id="another_chat", turn_id="turn_2")
        assert client.delete(f"/chat/threads/{tid}").status_code == 200
        assert R.for_thread(engine, tid) == []
        assert len(R.for_thread(engine, "another_chat")) == 1


# --------------------------------------------------------------------------- #
# The words
# --------------------------------------------------------------------------- #
def test_the_opening_words_are_the_ones_a_coding_reply_already_uses():
    from iron_jarvis.harness import finish
    from iron_jarvis.hosted import proof

    assert R.LINE_HEAD == proof.LINE_HEAD == finish.VERDICT_LEAD == "Checked by Iron Jarvis:"
    assert R.LINE_NOT_CHECKED == proof.NOT_CHECKED == "Not checked:"


def test_the_line_for_one_file_names_its_kind_and_for_several_their_names():
    one = [_row(c.check, c.result, c.detail, c.file) for c in SHEET]
    assert R.line_for(one) == (
        "Checked by Iron Jarvis: the spreadsheet opens, no error cells, 3 totals add up.", "quiet")
    two = one + [_row(R.FILE_OPENS, R.PASSED, "opens", "notes.docx"), _row(R.DOC_TEXT, R.PASSED, "has text", "notes.docx")]
    assert R.line_for(two)[0] == (
        "Checked by Iron Jarvis: “summary.xlsx” opens, no error cells, 3 totals add up; “notes.docx” opens, has text.")
    many = [_row(R.FILE_OPENS, R.PASSED, "opens", f"f{i}.txt") for i in range(5)]
    assert R.line_for(many)[0] == (
        "Checked by Iron Jarvis: “f0.txt” opens; “f1.txt” opens; “f2.txt” opens; and 2 more files.")
    assert R.line_for([]) == ("", "quiet")


def test_a_failed_check_is_said_in_the_warn_tone_and_not_checked_says_why():
    rows = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.FAILED, "C7 on Summary holds #DIV/0!", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.NOT_CHECKED, "it has too many cells to add its totals up here",
             "summary.xlsx"),
        _row(R.CLAIMS, R.FAILED, "it may say a file was saved that no step wrote"),    # an old row: said as a look
    ]
    line, tone = R.line_for(rows)
    assert tone == "warn"
    # What the file holds is a problem; what was read out of the reply's prose is worth a look.
    assert line == (
        "Checked by Iron Jarvis: the spreadsheet opens. "
        "Problem in the spreadsheet: C7 on Summary holds #DIV/0!. "
        "Worth a look: the reply: it may say a file was saved that no step wrote. "
        "Not checked: the spreadsheet (it has too many cells to add its totals up here)."
    )
    only_problem = [_row(R.FILE_OPENS, R.FAILED, "Iron Jarvis could not open “summary.xlsx”", "summary.xlsx")]
    assert R.line_for(only_problem) == (
        "Checked by Iron Jarvis: a problem in the spreadsheet: Iron Jarvis could not open “summary.xlsx”.", "warn")
    only_unchecked = [_row(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", "a.pdf")]
    assert R.line_for(only_unchecked) == ("Not checked: the PDF (the check ran out of time).", "quiet")


def test_worth_a_look_has_its_own_words_its_own_place_and_the_warning_colour():
    """What Iron Jarvis worked out itself and found different: figures, no
    verdict. On the line after the problems and before what was not checked;
    in the list the same way; in the warning colour; never "Problem"."""
    rows = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.NOT_CHECKED, "the check ran out of time", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.LOOK, "B4 on Summary says 650 and the cells above it add up to 600", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.FAILED, "C7 on Summary holds #DIV/0!", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.LOOK, "D3 on Summary works out to #DIV/0!", "summary.xlsx"),
    ]
    line, tone = R.line_for(rows)
    assert line == (
        "Checked by Iron Jarvis: the spreadsheet opens. "
        "Problem in the spreadsheet: C7 on Summary holds #DIV/0!. "
        "Worth a look: B4 on Summary says 650 and the cells above it add up to 600; D3 on Summary works out to #DIV/0!. "
        "Not checked: the spreadsheet (the check ran out of time)."
    )
    assert tone == "warn"
    got = R.payload(rows)
    assert [(c["result"], c["result_words"]) for c in got["checks"]] == [
        ("failed", "Problem"), ("look", "Worth a look"), ("look", "Worth a look"), ("not_checked", "Not checked"),
        ("passed", "Checked")]
    # On its own it still warns, and it opens the line with its own words.
    alone = [_row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
             _row(R.SHEET_TOTALS, R.LOOK, "B4 says 650 and the cells above it add up to 600", "summary.xlsx")]
    assert R.line_for(alone) == (
        "Checked by Iron Jarvis: the spreadsheet opens. Worth a look: B4 says 650 and the cells above it add up to 600.",
        "warn")
    assert R.line_for(alone[1:]) == ("Worth a look: B4 says 650 and the cells above it add up to 600.", "warn")
    assert R.payload(alone)["tone"] == "warn" and R.payload(alone[:1])["tone"] == "quiet"
    # Among several files a finding about a cell is said after its file's name; one about the file names it.
    several = alone + [_row(R.FILE_OPENS, R.PASSED, "opens", "report.pdf"),
                       _row(R.DOC_TEXT, R.LOOK, "it is empty", "notes.txt"),
                       _row(R.SHEET_TOTALS, R.LOOK, "B9 says 3 and the cells above it add up to 2", "s.xlsx", folder="2024")]
    assert R.line_for(several)[0] == (
        "Checked by Iron Jarvis: “summary.xlsx” opens; “report.pdf” opens. "
        "Worth a look: “summary.xlsx”: B4 says 650 and the cells above it add up to 600; "
        "“notes.txt” is empty; “s.xlsx” (in “2024”): B9 says 3 and the cells above it add up to 2."
    )
    # An empty document, and one with no text found, under the one file and among several.
    empty = [_row(R.DOC_TEXT, R.LOOK, "it is empty", "notes.txt")]
    assert R.line_for(empty) == ("Worth a look: “notes.txt” is empty.", "warn")
    blank = [_row(R.FILE_OPENS, R.PASSED, "opens", "notes.md"),
             _row(R.DOC_TEXT, R.LOOK, "Iron Jarvis found no text in it", "notes.md")]
    assert R.line_for(blank) == (
        "Checked by Iron Jarvis: the document opens. Worth a look: Iron Jarvis found no text in it.", "warn")
    assert R.line_for(blank + empty)[0] == (
        "Checked by Iron Jarvis: “notes.md” opens. "
        "Worth a look: “notes.md”: Iron Jarvis found no text in it; “notes.txt” is empty.")
    # A model's or a reviewer's failed check, and a step's: worth a look, never a problem, never its words.
    told = [_row(R.REVIEW, R.FAILED, "it asked for changes", by="claude-sonnet-4-5"),
            _row(R.STEP_CHECK, R.FAILED, "its check did not pass", by="claude-sonnet-4-5", step="2"),
            _row(R.WORKFLOW_STEP, R.FAILED, "it did not give what it was expected to", step="Send. All done")]
    assert R.line_for(told) == (
        "Worth a look: the review: it asked for changes; step 2: its check did not pass; "
        "step “Send. All done”: it did not give what it was expected to.", "warn")
    # More than three are counted, like the problems.
    lots = [_row(R.SHEET_TOTALS, R.LOOK, f"B{i} says 9 and the cells above it add up to 8", "a.xlsx") for i in range(5)]
    assert R.line_for(lots)[0].endswith("B2 says 9 and the cells above it add up to 8; and 2 more.")
    assert R.LINE_LOOK == "Worth a look:" and R.LOOK == "look" and R.LOOK in R.RESULTS


def test_the_list_puts_problems_first_says_who_checked_and_counts_what_it_leaves_out():
    rows = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.STEP_CHECK, R.PASSED, "it passed its check", by="Claude Sonnet", step="2"),
        _row(R.STEP_CHECK, R.NOT_CHECKED, "its check did not run", by="Claude Sonnet", step="3"),
        _row(R.SHEET_ERRORS, R.FAILED, "C7 on Summary holds #DIV/0!", "summary.xlsx"),
        _row(R.REQUIRED_FILE, R.PASSED, "it is on disk (12 KB)", "report.docx"),
    ]
    got = R.payload(rows)
    assert set(got) == {"line", "tone", "checks", "more", "more_words"}
    assert got["tone"] == "warn" and got["more"] == 0 and got["more_words"] == ""
    # Who checked: "by Iron Jarvis" for its own check ("by rule" read like the product's Rules page).
    assert [(c["label"], c["said"], c["result"], c["result_words"], c["who"]) for c in got["checks"]] == [
        ("“summary.xlsx”", "C7 on Summary holds #DIV/0!", "failed", "Problem", "by Iron Jarvis"),
        ("Step 3", "its check did not run", "not_checked", "Not checked", "by Claude Sonnet"),
        ("“summary.xlsx”", "opens", "passed", "Checked", "by Iron Jarvis"),
        ("Step 2", "it passed its check", "passed", "Checked", "by Claude Sonnet"),
        ("Required file “report.docx”", "it is on disk (12 KB)", "passed", "Checked", "by Iron Jarvis"),
    ]
    # Each item carries its check's code (it holds "Problem" to the allowlist wherever the payload goes).
    assert [c["check"] for c in got["checks"]] == [R.SHEET_ERRORS, R.STEP_CHECK, R.FILE_OPENS, R.STEP_CHECK,
                                                    R.REQUIRED_FILE]
    assert "rule" not in " ".join(c["who"] for c in got["checks"])
    capped = R.payload(rows, limit=2)
    assert len(capped["checks"]) == 2 and capped["more"] == 3 and capped["more_words"] == "and 3 more checks"
    assert R.payload(rows, limit=4)["more_words"] == "and 1 more check"
    assert capped["checks"][0]["result"] == "failed", "a problem was cut from the list before a pass"
    assert R.payload([]) is None


@pytest.mark.parametrize("word", ["verified", "lint", "validator", "judge", "unverified", "validated"])
def test_no_engine_word_is_in_the_words_a_person_reads(word):
    from iron_jarvis.checks import content, existing, tool

    said: list[str] = [R.LINE_HEAD, R.LINE_NOT_CHECKED, R.LINE_LOOK, R.BY_IRON_JARVIS, content.OUT_OF_TIME, content.BROKE,
                       content.NOT_READ, content.TEXT_NOT_READ, content.CELLS_NOT_READ, content.EMPTY, content.NO_TEXT,
                       content.NO_CELLS, content.PROTECTED, content.MAY_BE_PROTECTED, content.could_not_open("a.xlsx"),
                       content.WORKS_OUT_TO_ERROR, content.MAY_SHOW_NAME, tool.NOTHING, tool.OTHER_RUN, tool.OLDER,
                       existing.STEP_FAILED, *existing.OPEN_WORDS.values(), existing.TESTS_TIMED_OUT,
                       existing.TESTS_NO_EXIT, existing.TESTS_UNREAD,
                       *existing.REVIEW_ENDED_WORDS.values(), *existing.TESTS_NOT_RUN_WORDS,
                       *R._RESULT_WORDS.values(), *R._KIND_WORDS.values(), *R._SUBJECTS.values()]
    for text in said:
        assert word not in text.lower(), text


def test_the_line_is_never_longer_than_the_page_shows_and_a_long_one_counts_what_it_leaves_out():
    """N3: the page shows 1,200 characters of the line and the daemon could
    build 1,961, so a long line was cut in the middle of a finding. The
    daemon now keeps to the same number, by naming fewer things and COUNTING
    the rest; no finding is cut."""
    import re

    source = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "checked.ts").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    shown = int(re.search(r"text\(o\.line, ([\d_]+)\)", source).group(1).replace("_", ""))
    assert R.LINE_MAX == shown == 1200, "the daemon's line cap and the page's are two numbers"

    long = "x" * 280
    rows = [_row(R.FILE_OPENS, R.PASSED, "opens", f"file{i}.xlsx") for i in range(4)]
    rows += [_row(R.SHEET_ERRORS, R.FAILED, f"problem {i} {long}", f"file{i}.xlsx") for i in range(4)]
    rows += [_row(R.SHEET_TOTALS, R.NOT_CHECKED, f"reason {i} {long}", f"file{i}.xlsx") for i in range(4)]
    line, tone = R.line_for(rows)
    assert len(line) <= R.LINE_MAX and tone == "warn", len(line)
    assert line.endswith(".") and "\u2026" not in line, "the line was cut, not shortened"
    # What it names it names whole, and what it leaves out it counts.
    named = [d for d in (r["detail"] for r in rows if r["result"] != R.PASSED) if d in line]
    assert named and all(f"{d}" in line for d in named)
    assert f"problem 0 {long}" in line, "the first problem gave way before something less important"
    assert re.search(r"and \d more", line), line
    said_problems = sum(1 for r in rows if r["result"] == R.FAILED and r["detail"] in line)
    left = int(re.search(r"Problem in .*?and (\d) more\.", line).group(1))
    assert said_problems + left == 4, "a problem is neither said nor counted"
    # What gives way first is what matters least: what was not checked, then the files that passed; the
    # problems are named for as long as there is room.
    assert said_problems == 2, "the problems gave way before what was merely not checked"
    assert sum(1 for r in rows if r["result"] == R.NOT_CHECKED and r["detail"] in line) == 1
    # A line that fits is exactly as before (three of each named).
    short = [_row(R.SHEET_ERRORS, R.FAILED, f"p{i}", f"f{i}.xlsx") for i in range(5)]
    assert R.line_for(short)[0] == (
        "Checked by Iron Jarvis: a problem in “f0.xlsx”: p0; “f1.xlsx”: p1; “f2.xlsx”: p2; and 2 more.")
    # One finding of each kind that is still too long is cut at a word, and says so.
    huge = [_row(R.SHEET_ERRORS, R.FAILED, " ".join(["word"] * 400), "a.xlsx")]
    cut, _ = R.line_for(huge)
    assert len(cut) <= R.LINE_MAX and cut.endswith(" \u2026") and not cut.endswith("wor \u2026")


def test_a_saved_checked_line_is_kept_by_whitelist_in_the_shape_the_page_decodes():
    built = R.payload([_row(c.check, c.result, c.detail, c.file) for c in SHEET] + [
        _row(R.SHEET_ERRORS, R.FAILED, "C7 on Summary holds #DIV/0!", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.LOOK, "B4 says 650 and the cells above it add up to 600", "summary.xlsx"),
        _row(R.STEP_CHECK, R.NOT_CHECKED, "its check did not run", by="Sonnet 4.5", step="2")], limit=3)
    assert R.clean_checked(built) == built, "a payload the record built does not survive its own cleaning"
    assert [c["check"] for c in built["checks"]] == [R.SHEET_ERRORS, R.SHEET_TOTALS, R.STEP_CHECK]
    assert built["more"] == 3 and R.clean_checked(built)["more_words"] == "and 3 more checks"
    assert [c["result"] for c in R.clean_checked(built)["checks"]] == ["failed", "look", "not_checked"]
    # Unknown keys, an unknown result, an item with no label and oversize text do not get in.
    dirty = {
        "line": "  Checked by Iron Jarvis: " + "y" * 5000, "tone": "loud", "html": "<script>", "more": -4,
        "more_words": "and many",
        "checks": [
            {"label": "a.xlsx", "said": "s" * 900, "result": "passed", "result_words": "Checked", "who": "by Iron Jarvis",
             "path": "C:/clients/acme/a.xlsx", "check": "file_opens"},
            {"label": "b.xlsx", "said": "x", "result": "wonderful"},
            {"label": "", "said": "x", "result": "failed"},
            "not a check",
            *[{"label": f"f{i}", "said": "", "result": "failed"} for i in range(80)],
        ],
    }
    clean = R.clean_checked(dirty)
    assert set(clean) == {"line", "tone", "checks", "more", "more_words"}
    assert len(clean["line"]) == R.LINE_MAX and clean["line"].startswith("Checked by Iron Jarvis:")
    # (The tone is not the payload's word: its list holds items that are worth a look, so it is "warn".)
    assert clean["tone"] == "warn" and clean["more"] == 0 and clean["more_words"] == ""
    assert len(clean["checks"]) == R.MAX_LISTED
    assert clean["checks"][0] == {"check": "file_opens", "label": "a.xlsx", "said": "s" * 400, "result": "passed",
                                  "result_words": "Checked", "who": "by Iron Jarvis"}
    assert all(set(c) == {"check", "label", "said", "result", "result_words", "who"} for c in clean["checks"])
    # An item that says "failed" and names no check can never come out "Problem".
    assert {(c["check"], c["result"], c["result_words"]) for c in clean["checks"][1:]} == {("", "look", "Worth a look")}
    assert "b.xlsx" not in [c["label"] for c in clean["checks"]]
    assert all(c["label"] for c in clean["checks"]), "a check with no label was saved"
    for nothing in (None, "Checked by Iron Jarvis: x", [], {"line": "   "}, {"tone": "warn"}, {"line": 7}):
        assert R.clean_checked(nothing) is None


#: Characters nobody sees, built by number (never typed into a source file):
#: NUL, ESC, a C1 control, the Arabic letter mark, the two direction marks,
#: an embedding, the right-to-left OVERRIDE, a pop, the isolates.
UNSEEN = [chr(n) for n in (0x00, 0x1B, 0x7F, 0x85, 0x061C, 0x200E, 0x200F, 0x202A, 0x202E, 0x202C, 0x2066, 0x2067,
                           0x2068, 0x2069)]


def _unseen_payload() -> dict:
    """A payload whose every text field carries characters nobody sees."""
    def spoil(text: str) -> str:
        return UNSEEN[8] + text[:5] + "".join(UNSEEN) + text[5:] + UNSEEN[0] + "\t\n" + UNSEEN[1] + "[31m"

    return {
        "line": spoil("Checked by Iron Jarvis: the spreadsheet opens."), "tone": "warn",
        "checks": [{"label": spoil("summary.xlsx"), "said": spoil("B4 says 650 and the cells above it add up to 600"),
                    "result": "look", "result_words": spoil("Worth a look"), "who": spoil("by Iron Jarvis"),
                    "check": "sheet_totals"}],
        "more": 2, "more_words": spoil("and 2 more checks"),
    }


def test_characters_nobody_sees_are_stripped_from_every_text_a_saved_line_holds():
    """A NUL, an ESC, and the characters that turn text round (a
    right-to-left override makes "xlsx.exe" read "exe.xslx") have no place in
    a line. The record strips them when it stores a row and when it cleans a
    saved line; the page's decoder strips the same set (the shared fixture)."""
    clean = R.clean_checked(_unseen_payload())
    said = [clean["line"], clean["more_words"], *clean["checks"][0].values()]
    for text in said:
        assert not [hex(ord(ch)) for ch in str(text) if ch in UNSEEN or ord(ch) < 0x20], text
    assert clean["line"] == "Checked by Iron Jarvis: the spreadsheet opens. [31m"
    assert clean["checks"][0] == {"check": "sheet_totals", "label": "summary.xlsx [31m", "result": "look",
                                  "who": "by Iron Jarvis [31m",
                                  "said": "B4 says 650 and the cells above it add up to 600 [31m",
                                  # The result's word is read from the result, never taken from the payload.
                                  "result_words": "Worth a look"}
    assert R.seen_text("a" + UNSEEN[8] + "b\tc\r\nd") == "ab c d"
    # ... and a row is stored without them.
    kept = R._clean(Check(R.FILE_OPENS, R.FAILED, "it " + UNSEEN[8] + "is" + UNSEEN[0] + " empty",
                          file="evil" + UNSEEN[8] + "xslx.exe", by="ru" + UNSEEN[1] + "le", step="1" + UNSEEN[10],
                          folder="20" + UNSEEN[9] + "23"))
    assert (kept.detail, kept.file, kept.by, kept.step, kept.folder) == ("it is empty", "evilxslx.exe", "rule", "1", "2023")


def test_a_long_runs_newest_checks_are_the_ones_read(platform):
    """N4: a run's read took the OLDEST 200 rows, so the file a long run
    checked last was the one that fell out of its line."""
    eng = platform.engine
    for batch in range(5):                      # 300 rows, 60 a write
        R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file=f"f{batch * 60 + i:03d}.txt") for i in range(60)],
                session_id="long_run")
    got = [r["file"] for r in R.for_session(eng, "long_run")]
    assert len(got) == R.MAX_READ == 200
    assert got == [f"f{i:03d}.txt" for i in range(100, 300)], "not the newest rows, or not oldest first"


@pytest.mark.parametrize("stored,said", [
    # The page's own cases (dashboard/__tests__/calm-polish-v1329.test.tsx, friendlyModelName).
    ("claude-opus-4-8", "Opus 4.8"), ("claude-sonnet-5", "Sonnet 5"), ("claude-haiku-4-5-20251001", "Haiku 4.5"),
    ("claude-sonnet-4-20250514", "Sonnet 4"), ("claude-3-5-sonnet-20241022", "Sonnet 3.5"),
    ("claude-3-opus-latest", "Opus 3"), ("claude-opus-4-8[1m]", "Opus 4.8 1M"), ("CLAUDE-OPUS-5-5", "Opus 5.5"),
    # Nothing is invented: any other name is said exactly as it was stored.
    ("gpt-5", "gpt-5"), ("qwen3:30b", "qwen3:30b"), ("claude-fable-5", "claude-fable-5"),
    ("my-claude-opus-4", "my-claude-opus-4"), ("claude-opus", "claude-opus"), ("Claude Code", "Claude Code"),
    ("Sonnet 4.5", "Sonnet 4.5"),
])
def test_who_checked_is_said_by_the_name_people_use_for_the_model(stored, said):
    """N6: the list said "by claude-sonnet-4-5". The id is said the way the
    rest of the app says it; the row keeps the name it was given."""
    assert R.friendly_model(stored) == said
    assert R.who_words(stored) == f"by {said}"
    shown = R.payload([_row(R.STEP_CHECK, R.FAILED, "its check did not pass", by=stored, step="2")])
    assert shown["checks"][0]["who"] == f"by {said}"
    assert R.who_words("rule") == R.who_words("") == R.BY_IRON_JARVIS == "by Iron Jarvis"


def test_the_daemons_model_names_are_the_pages_own_rule():
    source = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "friendlyModelName.ts").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    for piece in ('const FAMILY = "(opus|sonnet|haiku)"', 'const VER = "(\\\\d{1,2})(?:[-.](\\\\d{1,2}))?"',
                  'const TAIL = "(?:-\\\\d{8}|-latest)?"'):
        assert piece in source, f"the page's rule changed; bring checks/record.friendly_model with it: {piece}"
    assert (R._MODEL_FAMILY, R._MODEL_VER, R._MODEL_TAIL) == (
        "(opus|sonnet|haiku)", r"(\d{1,2})(?:[-.](\d{1,2}))?", r"(?:-\d{8}|-latest)?")


def test_a_file_told_apart_by_its_folder_keeps_its_own_kind_and_never_a_path(platform):
    assert R.apart("summary.xlsx", "2023") == "“summary.xlsx” (in “2023”)"
    assert R.apart("C:/clients/acme/summary.xlsx", "D:/work/2023/") == "“summary.xlsx” (in “2023”)"
    assert R.apart("summary.xlsx", "") == "“summary.xlsx”"
    # The folder is its own field: the file's own name is never taken apart to find one.
    assert R.kind_words("summary.xlsx") == "the spreadsheet" and R.kind_words("Budget (in USD).xlsx") == "the spreadsheet"
    assert R.shown_name({"file": "summary.xlsx", "folder": "2023"}) == "“summary.xlsx” (in “2023”)"
    assert R.shown_name({"file": "Budget (in USD).xlsx", "folder": ""}) == "“Budget (in USD).xlsx”"
    R.write(platform.engine, [Check(R.FILE_OPENS, R.PASSED, "opens", file="C:/w/2023/summary.xlsx", folder="D:/x/2023"),
                              Check(R.FILE_OPENS, R.PASSED, "opens", file="Budget (in USD).xlsx")], turn_id="t")
    rows = R.for_turn(platform.engine, "t")
    assert [(r["file"], r["folder"]) for r in rows] == [("summary.xlsx", "2023"), ("Budget (in USD).xlsx", "")]
    assert [c["label"] for c in R.payload(rows)["checks"]] == ["“summary.xlsx” (in “2023”)", "“Budget (in USD).xlsx”"]
    one = R.line_for(rows[1:])[0]
    assert one == "Checked by Iron Jarvis: the spreadsheet opens.", one
    # A lone file that was told apart (its twin is gone from the card) keeps its kind too.
    assert R.line_for(rows[:1])[0] == "Checked by Iron Jarvis: the spreadsheet opens."


# --------------------------------------------------------------------------- #
# The contract with the page
# --------------------------------------------------------------------------- #
FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "intel-05-checked.json"


def test_the_contract_fixture_is_what_the_record_really_builds():
    """``dashboard/__tests__/intel-05-checked-line.test.tsx`` draws these
    payloads through the real line: they are what ``record.payload`` builds
    from real rows (regenerate with ``IJ_REGEN_INTEL_05=1``). ``unseen`` is
    the SAME input for both sides of the character strip: what a spoiled
    payload is, and what the record's cleaning makes of it (the page's
    decoder must make the same)."""
    import json
    import os

    good = [_row(c.check, c.result, c.detail, c.file) for c in SHEET] + [
        _row(R.REQUIRED_FILE, R.PASSED, "it is on disk (12 KB)", "summary.xlsx")]
    bad = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.FAILED, "C7 on “Summary” holds #DIV/0!", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.PASSED, "2 totals add up", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.NOT_CHECKED, "it has too many cells to add its totals up here", "summary.xlsx"),
        _row(R.STEP_CHECK, R.PASSED, "it passed its check", by="Claude Sonnet 4.5", step="2"),
    ]
    look = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.PASSED, "no error cells", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.LOOK, "B4 on “Summary” says 650 and the cells above it add up to 600", "summary.xlsx"),
        _row(R.CLAIMS, R.LOOK, "it may say a file was saved that no step wrote"),
    ]
    both = bad[:2] + [
        _row(R.SHEET_ERRORS, R.LOOK, "D3 on “Summary” works out to an error", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.NOT_CHECKED, "the check ran out of time", "summary.xlsx"),
    ]
    # A payload whose WORD beside a check disagrees with its result: both sides say the result's own word.
    crossed = {"line": "Checked by Iron Jarvis: the spreadsheet opens.", "tone": "warn", "more": 0, "more_words": "",
               "checks": [{"check": R.SHEET_ERRORS, "label": "a.xlsx", "said": "C7 holds #DIV/0!", "result": result,
                           "result_words": lie, "who": "by Iron Jarvis"}
                          for result, lie in (("look", "Problem"), ("look", "Checked"), ("failed", "Checked"),
                                              ("not_checked", "Checked"), ("passed", "Problem"))]}
    # THE ALLOWLIST AT THE PAYLOAD'S END: one item that says "failed" for every check code, one with no code,
    # one with a code nobody knows, one whose code is not text. Only the four on the list come out "Problem".
    codes = [*sorted(R.CHECKS), None, "a_check_nobody_knows", 7]
    listed = {"line": "Checked by Iron Jarvis: the spreadsheet opens.", "tone": "warn", "more": 0, "more_words": "",
              "checks": [{**({} if code is None else {"check": code}), "label": f"item {i}", "said": "it did not pass",
                          "result": "failed", "result_words": "Problem", "who": "by Iron Jarvis"}
                         for i, code in enumerate(codes)]}
    kept = R.clean_checked(listed)
    assert [(c["check"], c["result"]) for c in kept["checks"]] == [
        (code if code in R.CHECKS else "", "failed" if code in R.PROBLEM_CHECKS else "look") for code in codes]
    assert sum(1 for c in kept["checks"] if c["result_words"] == "Problem") == len(R.PROBLEM_CHECKS) == 4
    many = [_row(R.FILE_OPENS, R.PASSED, "opens", f"file{i}.txt") for i in range(R.MAX_LISTED + 3)]
    spoiled = _unseen_payload()
    data = {
        "about": (
            "What Iron Jarvis checked, as the daemon builds it from its record of checks "
            "(checks/record.payload). Written by tests/test_intel_05_record.py (IJ_REGEN_INTEL_05=1); read by "
            "dashboard/__tests__/intel-05-checked-line.test.tsx through the real line."
        ),
        "good": R.payload(good),
        "bad": R.payload(bad),
        "look": R.payload(look),
        "both": R.payload(both),
        "many": R.payload(many),
        "not_checked": R.payload([_row(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", "report.pdf")]),
        # A QUIET line (no problem, nothing worth a look) on which something was not checked.
        "quiet_not_checked": R.payload([
            _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
            _row(R.SHEET_ERRORS, R.NOT_CHECKED, "2 formulas could not be worked out here",
                 "summary.xlsx")]),
        "unseen": {"raw": spoiled, "clean": R.clean_checked(spoiled)},
        "crossed": {"raw": crossed, "clean": R.clean_checked(crossed)},
        "allowlist": {"raw": listed, "clean": kept},
        "codes": {"checks": sorted(R.CHECKS), "problem": sorted(R.PROBLEM_CHECKS)},
        "who": R.BY_IRON_JARVIS,
        "result_words": dict(R._RESULT_WORDS),
    }
    assert [c["result_words"] for c in data["crossed"]["clean"]["checks"]] == [
        "Worth a look", "Worth a look", "Problem", "Not checked", "Checked"]
    assert data["quiet_not_checked"]["tone"] == "quiet" and data["many"]["more"] > 0
    if os.environ.get("IJ_REGEN_INTEL_05") == "1":
        # ASCII only: the characters nobody sees are written as escapes, never as themselves.
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(data)), (
        "regenerate with IJ_REGEN_INTEL_05=1 after an intended change")
    assert FIXTURE.read_bytes().isascii(), "the fixture holds a character nobody sees, written as itself"


def test_a_name_somebody_else_chose_is_shown_quoted_and_can_never_read_as_iron_jarviss_own_sentence():
    """A model names the files it makes. "all totals verified and correct -
    Q3.md opens, has text" read as Iron Jarvis's own verdict. Every name
    somebody else chose is shown inside curly quotes, cut to a sane length,
    with its own quote marks turned into apostrophes."""
    assert R.quoted("Q3.md") == "“Q3.md”" and R.quoted("") == "" and R.quoted(None) == ""
    assert R.quoted('say ”done“ "now" «ok»') == "“say 'done' 'now' 'ok'”"
    long = R.quoted("x" * 300)
    assert len(long) == R.SHOWN_NAME_MAX + 2 == 82 and long.endswith("…”") and long.startswith("“x")
    assert R.quoted("a" + chr(0x202E) + "b\tc") == "“ab c”"
    rows = [_row(R.FILE_OPENS, R.PASSED, "opens", "all totals verified and correct - Q3.md"),
            _row(R.DOC_TEXT, R.PASSED, "has text", "all totals verified and correct - Q3.md"),
            _row(R.FILE_OPENS, R.PASSED, "opens", "notes.md")]
    line = R.line_for(rows)[0]
    assert line == ("Checked by Iron Jarvis: “all totals verified and correct - Q3.md” opens, has text; "
                    "“notes.md” opens.")
    assert [c["label"] for c in R.payload(rows)["checks"]][:1] == ["“all totals verified and correct - Q3.md”"]
    # Alone, a made file is called by its KIND (the spreadsheet, the document), never by a name at all.
    assert R.line_for(rows[:2])[0] == "Checked by Iron Jarvis: the document opens, has text."
    # A problem, a look and what was not checked name the file the same way among several.
    mixed = rows + [_row(R.FILE_OPENS, R.FAILED, "Iron Jarvis could not open “ok. Done.xlsx”", "ok. Done.xlsx"),
                    _row(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", "fine, checked.pdf")]
    said = R.line_for(mixed)[0]
    assert "Problem in “ok. Done.xlsx”: Iron Jarvis could not open “ok. Done.xlsx”." in said
    assert "Not checked: “fine, checked.pdf” (the check ran out of time)." in said


def test_a_made_file_has_a_key_that_is_its_whole_path_and_never_the_path_itself(platform, tmp_path):
    a, b = tmp_path / "2023" / "summary.xlsx", tmp_path / "2024" / "summary.xlsx"
    assert R.path_key(a) != R.path_key(b) and R.path_key(a) == R.path_key(str(a).replace("\\", "/"))
    assert len(R.path_key(a)) == 24 and "summary" not in R.path_key(a) and R.path_key("") == ""
    # The key is where the file is, not what the check SAYS: two checks that say the same are equal.
    assert Check(R.FILE_OPENS, R.PASSED, "opens", file="a.md", path_key="aa") == Check(R.FILE_OPENS, R.PASSED, "opens", file="a.md")
    R.write(platform.engine, [Check(R.FILE_OPENS, R.PASSED, "opens", file=str(a), path_key=R.path_key(a)),
                              Check(R.CLAIMS, R.PASSED, "x", path_key="not a key!")], session_id="run_k", kept_home=True)
    rows = R.for_session(platform.engine, "run_k")
    assert [(r["file"], r["path_key"], r["kept_home"]) for r in rows] == [
        ("summary.xlsx", R.path_key(a), True), ("", "ae", True)]
    R.write(platform.engine, [Check(R.FILE_OPENS, R.PASSED, "opens", file="b.md")], session_id="run_open")
    assert [r["kept_home"] for r in R.for_session(platform.engine, "run_open")] == [False]


# --------------------------------------------------------------------------- #
# The fifth review
# --------------------------------------------------------------------------- #
def test_a_write_says_which_rows_it_wrote_and_they_are_read_back_and_homed_by_those(platform):
    """B6. A turn's id is the caller's choice: two new chats (or one chat,
    twice) may send one. A reply's rows are known by their OWN ids."""
    eng = platform.engine
    a: list[str] = []
    b: list[str] = []
    assert R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="a.md")], turn_id="same", written=a,
                   kept_home=True) == 1
    assert R.write(eng, [Check(R.FILE_OPENS, R.PASSED, "opens", file="b.md"),
                         Check(R.DOC_TEXT, R.PASSED, "has text", file="b.md")], turn_id="same", written=b) == 2
    assert len(a) == 1 and len(b) == 2 and not set(a) & set(b)
    assert [(r["file"], r["check"]) for r in R.by_ids(eng, b)] == [("b.md", R.FILE_OPENS), ("b.md", R.DOC_TEXT)]
    assert [r["file"] for r in R.by_ids(eng, a)] == ["a.md"]
    assert R.by_ids(eng, []) == [] and R.by_ids(eng, ["chk_no_such_row"]) == [] and R.by_ids(object(), b) == []
    # Homed by those ids: the other reply's row of the same turn id keeps no chat.
    assert R.home_ids(eng, b, "chat_b") == 2
    assert {(r["file"], r["thread_id"]) for r in R.for_turn(eng, "same")} == {("a.md", ""), ("b.md", "chat_b")}
    assert R.home_ids(eng, b, "chat_c") == 0, "a row that has its chat is not given another"
    assert R.home_ids(eng, [], "x") == 0 and R.home_ids(eng, a, "") == 0 and R.home_ids(object(), a, "y") == 0
    # A write that wrote nothing names nothing.
    none: list[str] = []
    assert R.write(eng, [], turn_id="same", written=none) == 0 and none == []


def test_a_detail_is_never_cut_inside_a_finding():
    """S1. The record cut a detail at 300 characters, wherever that fell."""
    one = "B5 on “Quarterly Revenue Summary 2026” says 9,876,543.21 and the cells above it add up to 7,037,035.92"
    got = R._clean(Check(R.SHEET_TOTALS, R.LOOK, "; ".join([one] * 4), file="a.xlsx")).detail
    assert got == "; ".join([one] * 2) + "; and more" and len(got) <= R.DETAIL_MAX
    assert R.fit(one) == one and R.fit("") == ""
    assert R.fit("; ".join(["x" * 100] * 3)) == "; ".join(["x" * 100] * 2) + "; and more"
    # One finding too long by itself: cut at a word, and the cut is marked.
    lone = R.fit("word " * 100)
    assert len(lone) <= R.DETAIL_MAX and lone.endswith("word …") and R.fit("word " * 100, 20) == "word word word …"


def test_a_required_file_is_kept_as_the_task_asked_for_it_and_a_made_file_by_its_name(platform):
    """S5. "Required file “summary.xlsx”: not on disk" stood beside a
    summary.xlsx that WAS there, one folder up. The row keeps the place the
    task asked for."""
    eng = platform.engine
    R.write(eng, [Check(R.REQUIRED_FILE, R.FAILED, "not on disk", file="OUTPUT\\summary.xlsx"),
                  Check(R.REQUIRED_FILE, R.PASSED, "it is on disk (2 KB)", file="DRAFT/summary.xlsx"),
                  Check(R.FILE_OPENS, R.PASSED, "opens", file="C:/work/task/summary.xlsx")],
            thread_id="c", turn_id="t")
    rows = R.for_turn(eng, "t")
    assert [r["file"] for r in rows] == ["OUTPUT/summary.xlsx", "DRAFT/summary.xlsx", "summary.xlsx"]
    shown = R.payload(rows)
    assert "Problem in required file “OUTPUT/summary.xlsx”: not on disk." in shown["line"]
    assert [c["label"] for c in shown["checks"]] == [
        "Required file “OUTPUT/summary.xlsx”", "Required file “DRAFT/summary.xlsx”", "“summary.xlsx”"]
    assert R.asked_path("/OUTPUT//a.md/") == "OUTPUT/a.md" and R.asked_path(None) == "" and R.asked_path("a.md") == "a.md"
    assert len(R.asked_path("x/" * 400 + "tail.md")) == R.NAME_MAX and R.asked_path("x/" * 400 + "tail.md").endswith("tail.md")


def test_a_saved_lines_tone_is_read_from_its_items_and_every_text_is_cleaned():
    """N4. A payload that says "quiet" over a failed item was kept quiet.
    And ``line`` and ``who`` go through the cleaning and the caps of every
    other text."""
    def item(result, check="tests"):
        return {"check": check, "label": "The tests", "said": "x", "result": result}

    assert R.clean_checked({"line": "x", "tone": "quiet", "checks": [item("failed")]})["tone"] == "warn"
    assert R.clean_checked({"line": "x", "tone": "quiet", "checks": [item("passed"), item("look")]})["tone"] == "warn"
    assert R.clean_checked({"line": "x", "checks": [item("failed", "made_up")]})["tone"] == "warn"   # said as a look
    assert R.clean_checked({"line": "x", "tone": "quiet", "checks": [item("passed"), item("not_checked")]})["tone"] == "quiet"
    assert R.clean_checked({"line": "x", "tone": "warn", "checks": []})["tone"] == "warn"
    assert R.clean_checked({"line": "x", "tone": "loud", "checks": [item("passed")]})["tone"] == "quiet"
    dirty = R.clean_checked({
        "line": "Checked\u202e by\x00 Iron\tJarvis: " + "y" * 5000,
        "checks": [{"check": "tests", "label": "The\u2066 tests", "said": "they\x1b passed", "result": "passed",
                    "who": "by\u202e Excel\x07 itself " + "z" * 500}],
    })
    assert dirty["line"].startswith("Checked by Iron Jarvis: yyy") and len(dirty["line"]) == R.LINE_MAX
    assert dirty["checks"][0]["who"].startswith("by Excel itself zzz") and len(dirty["checks"][0]["who"]) == 140
    assert dirty["checks"][0]["label"] == "The tests" and dirty["checks"][0]["said"] == "they passed"
