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


def _row(check, result, detail, file="", by="rule", step=""):
    return {"check": check, "result": result, "detail": detail, "file": file, "by": by, "step": step}


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
    assert R.write(platform.engine, [Check(R.CLAIMS, R.PASSED, "what it says matches what it did")],
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
    R.write(eng, [Check(R.SHEET_TOTALS, R.FAILED, "Summary!B5 says 9 and the cells above it add up to 8", file="a.xlsx"),
                  Check(R.SHEET_TOTALS, R.NOT_CHECKED, "1 total could not be worked out by rule", file="a.xlsx"),
                  Check(R.FILE_OPENS, R.PASSED, "opens", file="b.xlsx")], session_id="s1")
    R.write(eng, [Check(R.SHEET_TOTALS, R.PASSED, "2 totals add up", file="a.xlsx")], session_id="s1")
    got = {(r["check"], r["file"], r["result"]) for r in R.for_session(eng, "s1")}
    assert got == {(R.SHEET_TOTALS, "a.xlsx", R.PASSED), (R.FILE_OPENS, "b.xlsx", R.PASSED)}
    # Another owner's same check and file is untouched.
    R.write(eng, [Check(R.SHEET_TOTALS, R.FAILED, "x", file="a.xlsx")], session_id="s2")
    R.write(eng, [Check(R.SHEET_TOTALS, R.PASSED, "y", file="a.xlsx")], session_id="s1")
    assert [(r["result"], r["detail"]) for r in R.for_session(eng, "s2")] == [(R.FAILED, "x")]


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
        "Checked by Iron Jarvis: summary.xlsx opens, no error cells, 3 totals add up; notes.docx opens, has text.")
    many = [_row(R.FILE_OPENS, R.PASSED, "opens", f"f{i}.txt") for i in range(5)]
    assert R.line_for(many)[0] == (
        "Checked by Iron Jarvis: f0.txt opens; f1.txt opens; f2.txt opens; and 2 more files.")
    assert R.line_for([]) == ("", "quiet")


def test_a_failed_check_is_said_in_the_warn_tone_and_not_checked_says_why():
    rows = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.FAILED, "Summary!C7 holds #DIV/0!", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.NOT_CHECKED, "1 total could not be worked out by rule: Summary!D9 is not a plain sum",
             "summary.xlsx"),
        _row(R.CLAIMS, R.FAILED, "it says a file was saved that no step wrote"),
    ]
    line, tone = R.line_for(rows)
    assert tone == "warn"
    assert line == (
        "Checked by Iron Jarvis: the spreadsheet opens. "
        "Problem in the spreadsheet: Summary!C7 holds #DIV/0!; the reply: it says a file was saved that no step wrote. "
        "Not checked: the spreadsheet (1 total could not be worked out by rule: Summary!D9 is not a plain sum)."
    )
    only_problem = [_row(R.FILE_OPENS, R.FAILED, "it is empty", "summary.xlsx")]
    assert R.line_for(only_problem) == ("Checked by Iron Jarvis: a problem in the spreadsheet: it is empty.", "warn")
    only_unchecked = [_row(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", "a.pdf")]
    assert R.line_for(only_unchecked) == ("Not checked: the PDF (the check ran out of time).", "quiet")


def test_the_list_puts_problems_first_says_who_checked_and_counts_what_it_leaves_out():
    rows = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.STEP_CHECK, R.PASSED, "it passed its check", by="Claude Sonnet", step="2"),
        _row(R.STEP_CHECK, R.NOT_CHECKED, "its check did not run", by="Claude Sonnet", step="3"),
        _row(R.SHEET_ERRORS, R.FAILED, "Summary!C7 holds #DIV/0!", "summary.xlsx"),
        _row(R.REQUIRED_FILE, R.PASSED, "12 KB, opens", "report.docx"),
    ]
    got = R.payload(rows)
    assert set(got) == {"line", "tone", "checks", "more", "more_words"}
    assert got["tone"] == "warn" and got["more"] == 0 and got["more_words"] == ""
    assert [(c["label"], c["said"], c["result"], c["result_words"], c["who"]) for c in got["checks"]] == [
        ("summary.xlsx", "Summary!C7 holds #DIV/0!", "failed", "Problem", "by rule"),
        ("Step 3", "its check did not run", "not_checked", "Not checked", "by Claude Sonnet"),
        ("summary.xlsx", "opens", "passed", "Checked", "by rule"),
        ("Step 2", "it passed its check", "passed", "Checked", "by Claude Sonnet"),
        ("Required file report.docx", "12 KB, opens", "passed", "Checked", "by rule"),
    ]
    capped = R.payload(rows, limit=2)
    assert len(capped["checks"]) == 2 and capped["more"] == 3 and capped["more_words"] == "and 3 more checks"
    assert R.payload(rows, limit=4)["more_words"] == "and 1 more check"
    assert capped["checks"][0]["result"] == "failed", "a problem was cut from the list before a pass"
    assert R.payload([]) is None


@pytest.mark.parametrize("word", ["verified", "lint", "validator", "judge", "unverified", "validated"])
def test_no_engine_word_is_in_the_words_a_person_reads(word):
    from iron_jarvis.checks import content, existing, tool

    said: list[str] = [R.LINE_HEAD, R.LINE_NOT_CHECKED, content.OUT_OF_TIME, content.BROKE, content.NOT_ONE_LIST,
                       content.UNDER_TOTALS, tool.NOTHING, tool.OTHER_RUN, tool.OLDER, existing.CLAIMS_PASSED,
                       existing.STEP_FAILED, *existing.OPEN_WORDS.values(), *existing.SKIPPED_WORDS.values(),
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
        "Checked by Iron Jarvis: a problem in f0.xlsx: p0; f1.xlsx: p1; f2.xlsx: p2; and 2 more.")
    # One finding of each kind that is still too long is cut at a word, and says so.
    huge = [_row(R.SHEET_ERRORS, R.FAILED, " ".join(["word"] * 400), "a.xlsx")]
    cut, _ = R.line_for(huge)
    assert len(cut) <= R.LINE_MAX and cut.endswith(" \u2026") and not cut.endswith("wor \u2026")


def test_a_saved_checked_line_is_kept_by_whitelist_in_the_shape_the_page_decodes():
    built = R.payload([_row(c.check, c.result, c.detail, c.file) for c in SHEET] + [
        _row(R.SHEET_ERRORS, R.FAILED, "Summary!C7 holds #DIV/0!", "summary.xlsx"),
        _row(R.STEP_CHECK, R.NOT_CHECKED, "its check did not run", by="Sonnet 4.5", step="2")], limit=3)
    assert R.clean_checked(built) == built, "a payload the record built does not survive its own cleaning"
    assert built["more"] == 2 and R.clean_checked(built)["more_words"] == "and 2 more checks"
    # Unknown keys, an unknown result, an item with no label and oversize text do not get in.
    dirty = {
        "line": "  Checked by Iron Jarvis: " + "y" * 5000, "tone": "loud", "html": "<script>", "more": -4,
        "more_words": "and many",
        "checks": [
            {"label": "a.xlsx", "said": "s" * 900, "result": "passed", "result_words": "Checked", "who": "by rule",
             "path": "C:/clients/acme/a.xlsx"},
            {"label": "b.xlsx", "said": "x", "result": "wonderful"},
            {"label": "", "said": "x", "result": "failed"},
            "not a check",
            *[{"label": f"f{i}", "said": "", "result": "failed"} for i in range(80)],
        ],
    }
    clean = R.clean_checked(dirty)
    assert set(clean) == {"line", "tone", "checks", "more", "more_words"}
    assert len(clean["line"]) == R.LINE_MAX and clean["line"].startswith("Checked by Iron Jarvis:")
    assert clean["tone"] == "quiet" and clean["more"] == 0 and clean["more_words"] == ""
    assert len(clean["checks"]) == R.MAX_LISTED
    assert clean["checks"][0] == {"label": "a.xlsx", "said": "s" * 400, "result": "passed",
                                  "result_words": "Checked", "who": "by rule"}
    assert all(set(c) == {"label", "said", "result", "result_words", "who"} for c in clean["checks"])
    assert "b.xlsx" not in [c["label"] for c in clean["checks"]]
    assert all(c["label"] for c in clean["checks"]), "a check with no label was saved"
    for nothing in (None, "Checked by Iron Jarvis: x", [], {"line": "   "}, {"tone": "warn"}, {"line": 7}):
        assert R.clean_checked(nothing) is None


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
    assert R.who_words("rule") == R.who_words("") == "by rule"


def test_the_daemons_model_names_are_the_pages_own_rule():
    source = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "friendlyModelName.ts").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    for piece in ('const FAMILY = "(opus|sonnet|haiku)"', 'const VER = "(\\\\d{1,2})(?:[-.](\\\\d{1,2}))?"',
                  'const TAIL = "(?:-\\\\d{8}|-latest)?"'):
        assert piece in source, f"the page's rule changed; bring checks/record.friendly_model with it: {piece}"
    assert (R._MODEL_FAMILY, R._MODEL_VER, R._MODEL_TAIL) == (
        "(opus|sonnet|haiku)", r"(\d{1,2})(?:[-.](\d{1,2}))?", r"(?:-\d{8}|-latest)?")


def test_a_file_told_apart_by_its_folder_keeps_its_own_kind_and_never_a_path():
    assert R.apart("summary.xlsx", "2023") == "summary.xlsx (in 2023)"
    assert R.apart("C:/clients/acme/summary.xlsx", "D:/work/2023/") == "summary.xlsx (in 2023)"
    assert R.apart("summary.xlsx", "") == "summary.xlsx"
    assert R.own_name("summary.xlsx (in 2023)") == "summary.xlsx" and R.own_name("plain.pdf") == "plain.pdf"
    assert R.kind_words("summary.xlsx (in 2023)") == "the spreadsheet" and R.kind_words("x (in y).pdf") == "the file"
    assert R.file_name(R.apart("summary.xlsx", "2023")) == "summary.xlsx (in 2023)"


# --------------------------------------------------------------------------- #
# The contract with the page
# --------------------------------------------------------------------------- #
FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "intel-05-checked.json"


def test_the_contract_fixture_is_what_the_record_really_builds():
    """``dashboard/__tests__/intel-05-checked-line.test.tsx`` draws these
    payloads through the real line: they are what ``record.payload`` builds
    from real rows (regenerate with ``IJ_REGEN_INTEL_05=1``)."""
    import json
    import os

    good = [_row(c.check, c.result, c.detail, c.file) for c in SHEET] + [
        _row(R.CLAIMS, R.PASSED, "what it says matches what it did")]
    bad = [
        _row(R.FILE_OPENS, R.PASSED, "opens", "summary.xlsx"),
        _row(R.SHEET_ERRORS, R.FAILED, "Summary!C7 holds #DIV/0!", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.PASSED, "2 totals add up", "summary.xlsx"),
        _row(R.SHEET_TOTALS, R.NOT_CHECKED,
             "1 total could not be worked out by rule: Summary!D9 is worked out by a formula this check cannot follow",
             "summary.xlsx"),
        _row(R.STEP_CHECK, R.PASSED, "it passed its check", by="Claude Sonnet 4.5", step="2"),
    ]
    many = [_row(R.FILE_OPENS, R.PASSED, "opens", f"file{i}.txt") for i in range(R.MAX_LISTED + 3)]
    data = {
        "about": (
            "What Iron Jarvis checked, as the daemon builds it from its record of checks "
            "(checks/record.payload). Written by tests/test_intel_05_record.py (IJ_REGEN_INTEL_05=1); read by "
            "dashboard/__tests__/intel-05-checked-line.test.tsx through the real line."
        ),
        "good": R.payload(good),
        "bad": R.payload(bad),
        "many": R.payload(many),
        "not_checked": R.payload([_row(R.FILE_OPENS, R.NOT_CHECKED, "the check ran out of time", "report.pdf")]),
    }
    if os.environ.get("IJ_REGEN_INTEL_05") == "1":
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(data)), (
        "regenerate with IJ_REGEN_INTEL_05=1 after an intended change")
