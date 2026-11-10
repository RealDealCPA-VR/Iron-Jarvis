"""v1.465.0: the calculator, honest PDFs, refused formats and the helper label.

* ``calculate``: exact arithmetic for every figure an answer states (a live
  client memo had a correct table and three wrong numbers in its prose). It
  rides along with a turn about money; it never takes a place from another
  tool and the tool picker itself is unchanged.
* a PDF written from rows is a table (the columns used to run together:
  "1042" and "500" printed as "1042500"), a tab is space, and a character the
  PDF font cannot draw is said, never dropped in silence;
* a name ending .xls / .doc / .rtf (formats this app cannot write) is refused
  with nothing written (it used to write a few bytes of text and report a save);
* a coding helper reads "part failed" only when its LAST change failed (on the
  owner's history 53 of 70 helper runs carried the label and 5 ended that way).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.outcome import (
    OUTCOME_COMPLETED,
    OUTCOME_WITH_FAILURES,
    session_result,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.core.models import SessionStatus, ToolInvocation
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.documents import writers as W
from iron_jarvis.documents.calculate_tool import MAX_EXPRESSIONS, CalculateTool, shown
from iron_jarvis.documents.readers import extract_text
from iron_jarvis.harness.helpers import HELPER_ORIGIN
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools, wants_calculator
from iron_jarvis.tools.base import Reversibility, ToolContext


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r",
                       config=None, event_bus=None, engine=None)


def _calc(args, tmp_path):
    return asyncio.run(CalculateTool().execute(args, _ctx(tmp_path)))


# ------------------------------------------------------------- the calculator --


def test_the_memo_that_went_wrong_is_worked_out_exactly(tmp_path):
    """The live memo said "gross travel $2,235.80" for two flights and a hotel."""
    res = _calc({"expressions": [
        {"label": "travel", "expression": "=612.40*2+431"},
        {"label": "tax", "expression": "=ROUND(travel*7.25%,2)"},
        {"label": "total", "expression": "=travel+tax"},
        "=1333.60+1043.40+1154.99+2500+35+219.75",
    ]}, tmp_path)
    assert res.ok, res.error
    assert res.output.splitlines() == [
        "travel: =612.40*2+431 = 1,655.80",
        "tax: =ROUND(travel*7.25%,2) = 120.05",
        "total: =travel+tax = 1,775.85",
        "#4: =1333.60+1043.40+1154.99+2500+35+219.75 = 6,286.74",
    ]
    assert [r["value"] for r in res.data["results"]] == [1655.8, 120.05, 1775.85, 6286.74]


def test_one_bad_line_does_not_cost_the_others_and_is_named(tmp_path):
    res = _calc({"expressions": ["=1/0", "=FOO(1)", "=B2+1", "=2+2"]}, tmp_path)
    assert res.ok and res.data["failed"] == 3
    lines = res.output.splitlines()
    assert lines[0] == "#1: =1/0 -> NOT WORKED OUT: #DIV/0!: division by zero"
    assert "function FOO is not supported" in lines[1] and "Supported:" not in lines[1]
    assert "there is no workbook here" in lines[2] and lines[3] == "#4: =2+2 = 4"


def test_nothing_worked_out_is_a_failed_call_that_lists_what_it_knows(tmp_path):
    res = _calc({"expressions": ["=FOO(1)"]}, tmp_path)
    assert not res.ok and "NOT WORKED OUT" in res.error and "Functions it knows: ABS, AND" in res.error
    assert not _calc({}, tmp_path).ok and "pass expressions" in _calc({}, tmp_path).error


def test_the_short_form_and_the_cap(tmp_path):
    assert _calc({"expression": "2+2"}, tmp_path).output == "#1: 2+2 = 4"
    many = _calc({"expressions": ["=1+1"] * (MAX_EXPRESSIONS + 3)}, tmp_path)
    assert len(many.data["results"]) == MAX_EXPRESSIONS
    assert "(3 more expression(s) were not worked out: at most 40 in one call)" in many.output


def test_a_label_that_looks_like_a_cell_is_never_a_name(tmp_path):
    res = _calc({"expressions": [{"label": "A1", "expression": "=5"}, "=A1+1"]}, tmp_path)
    assert "there is no workbook here" in res.output  # A1 stayed a cell reference
    odd = _calc({"expressions": [{"label": "two words", "expression": "=5"}, "=5+1"]}, tmp_path)
    assert odd.output.splitlines() == ["two words: =5 = 5", "#2: =5+1 = 6"]  # a label, not a name


@pytest.mark.parametrize("value,text", [
    (1655.8, "1,655.80"), (4, "4"), (4.0, "4"), (1 / 3, "0.3333333333"), (0.004, "0.004"),
    (True, "TRUE"), ("x", "'x'"), (-1234567.891, "-1,234,567.891"),
])
def test_a_result_is_shown_with_every_digit_that_matters(value, text):
    assert shown(value) == text


def test_the_calculator_is_a_safe_tool_every_agent_has():
    from iron_jarvis.agents.types import get_agent_definition
    from iron_jarvis.core.models import AgentType

    tool = CalculateTool()
    assert tool.reversibility is Reversibility.READONLY
    assert tool.input_schema["type"] == "object"
    assert "calculate" in get_agent_definition(AgentType.BUILDER).tools


# ------------------------------------------------ when it rides along a turn --

RIDES = [
    ("what is 15% of 2,340?", []),
    ("calculate the monthly payment on a 250000 loan at 6%", []),
    ("what is 3 + 4", []),
    ("add up these invoices for me", []),
    ("work out the sales tax on $1,655.80", []),
    ("write a client memo summarizing these travel expenses with the total", ["write_document"]),
    ("put the budget in a workbook", ["write_document"]),
    ("read the invoice and tell me the amount due", ["read_document"]),
    ("how much revenue is in the March sheet", ["excel_query"]),
]
STAYS_HOME = [
    ("hello, how are you", []),
    ("work out the kids' schedule", []),
    ("summarize this contract", ["read_document"]),
    ("draft a thank-you letter to the client", ["write_document"]),
    ("what's the total?", []),                 # a money word with nothing to read or write
    ("figure out why the build is broken", []),
    ("rename report-2 to report-3", []),       # a minus sign between words is not arithmetic
]


@pytest.mark.parametrize("text,picked", RIDES)
def test_a_turn_about_figures_gets_the_calculator(text, picked):
    assert wants_calculator(text, picked) is True


@pytest.mark.parametrize("text,picked", STAYS_HOME)
def test_a_turn_about_anything_else_does_not(text, picked):
    assert wants_calculator(text, picked) is False


def test_the_tool_picker_itself_is_exactly_what_it_was():
    """`calculate` is no member of the picker's set and no rule scores it, so
    the picker's pinned output (and its six-tool cap) cannot move."""
    assert "calculate" not in AUTO_SAFE_TOOLS
    for text, _ in RIDES + STAYS_HOME:
        got = select_auto_tools(text, attachments=["expenses.xlsx"])
        assert "calculate" not in got and len(got) <= 6, (text, got)


def test_wants_calculator_is_linear_on_a_huge_message():
    import time

    started = time.perf_counter()
    for text in ("work out " * 60_000, "1 + " * 80_000, "total " * 90_000, "$" * 300_000):
        wants_calculator(text, ["write_document"])
    assert time.perf_counter() - started < 5


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    return SimpleNamespace(platform=create_app(str(tmp_path_factory.mktemp("calc") / "home")).state.platform)


def _armed(d, text, **kw):
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True, **kw)
    return list(CT._resolve_armed_tools(d, body)[0])


def test_the_chat_arms_it_outside_the_six_tool_cap(d):
    armed = _armed(d, "Write a client memo summarizing these travel expenses with the total",
                   attachments=["C:/x/expenses.xlsx"])
    assert armed[-1] == "calculate" or "calculate" in armed
    assert len([t for t in armed if t != "calculate"]) <= 6
    assert _armed(d, "what is 15% of 2,340?") == ["calculate"]
    assert "calculate" not in _armed(d, "hello, how are you")
    assert "calculate" not in _armed(d, "summarize this contract", attachments=["C:/x/c.pdf"])


def test_auto_tools_off_arms_no_calculator(d):
    body = ChatBody(messages=[{"role": "user", "content": "what is 15% of 2,340?"}], auto_tools=False)
    assert "calculate" not in CT._resolve_armed_tools(d, body)[0]


def test_a_user_who_set_it_to_never_run_is_obeyed(d, monkeypatch):
    perms = d.platform.permissions
    real = perms.mode_for

    def mode_for(key, *a, **kw):
        if key == "calculate":
            return SimpleNamespace(value="deny")
        return real(key, *a, **kw)

    monkeypatch.setattr(perms, "mode_for", mode_for)
    assert "calculate" not in _armed(d, "what is 15% of 2,340?")


# ------------------------------------------------------------------- PDFs --


def test_rows_written_to_a_pdf_are_a_table_with_the_columns_apart(tmp_path, monkeypatch):
    drawn: list[list[list[str]]] = []
    real = W._pdf_table

    def spy(pdf, rows, *a, **kw):
        drawn.append([list(r) for r in rows])
        return real(pdf, rows, *a, **kw)

    monkeypatch.setattr(W, "_pdf_table", spy)
    out = tmp_path / "t.pdf"
    W.write_document(out, [["Invoice", "Amount"], ["1042", "500"], ["1043", "1,250.00"]])
    assert drawn == [[["Invoice", "Amount"], ["1042", "500"], ["1043", "1,250.00"]]], "drawn as ONE real table"
    text = extract_text(out)
    assert "1042500" not in text.replace(" ", "X"), text
    for cell in ("Invoice", "Amount", "1042", "500", "1043", "1,250.00"):
        assert cell in text
    assert "1042 500" in " ".join(text.split()) or "1042\n500" in text


def test_sheets_written_to_a_pdf_are_a_table_each_under_its_name(tmp_path, monkeypatch):
    drawn: list[int] = []
    real = W._pdf_table
    monkeypatch.setattr(W, "_pdf_table", lambda pdf, rows, *a, **kw: (drawn.append(len(rows)), real(pdf, rows, *a, **kw))[1])
    out = tmp_path / "s.pdf"
    W.write_document(out, {"January": [["Item", "Cost"], ["Rent", "500"]],
                           "February": [["Item", "Cost"], ["Power", "75"]]})
    text = extract_text(out)
    assert text.index("January") < text.index("Rent") < text.index("February") < text.index("Power")
    assert drawn == [2, 2], "one table per sheet"
    assert "Rent500" not in text.replace(" ", "")  or "Rent 500" in " ".join(text.split())


def test_a_tab_in_text_keeps_its_columns_apart(tmp_path):
    out = tmp_path / "tab.pdf"
    W.write_document(out, "Invoice\t500\nTotal\t1250")
    flat = " ".join(extract_text(out).split())
    assert "Invoice 500" in flat and "Invoice500" not in flat


def test_a_character_the_font_cannot_draw_is_said_not_dropped(tmp_path):
    warnings: list[str] = []
    out = tmp_path / "c.pdf"
    W.write_document(out, "Client \u5f20\u4f1f owes 500", warnings=warnings)
    assert any("2 character(s) are in a script this PDF's font cannot draw" in w for w in warnings), warnings
    assert "Client ?? owes 500" in " ".join(extract_text(out).split())
    plain: list[str] = []
    W.write_document(tmp_path / "p.pdf", "Caf\u00e9 r\u00e9sum\u00e9 \u2014 500 \u20ac", warnings=plain)
    assert plain == [], "accents, dashes and the euro sign are drawn, so nothing is said"


# ------------------------------------------------- formats that are refused --


@pytest.mark.parametrize("name", sorted(W.UNWRITABLE_FORMATS))
def test_a_format_that_cannot_be_written_is_refused_and_nothing_is_written(tmp_path, name):
    out = tmp_path / f"books{name}"
    with pytest.raises(ValueError) as err:
        W.write_document(out, [["a", "b"], [1, 2]])
    assert f"cannot write a {name} file" in str(err.value) and "Nothing was written." in str(err.value)
    assert not out.exists() and list(tmp_path.iterdir()) == []


def test_the_refusal_names_the_format_to_use_instead():
    assert "save it as .xlsx" in W.UNWRITABLE_FORMATS[".xls"]
    assert "save it as .docx" in W.UNWRITABLE_FORMATS[".doc"]
    assert "save it as .pptx" in W.UNWRITABLE_FORMATS[".ppt"]


def test_formats_it_can_write_are_untouched(tmp_path):
    for name in ("a.xlsx", "a.docx", "a.pdf", "a.csv", "a.txt", "a.md"):
        W.write_document(tmp_path / name, [["a", "b"], ["1", "2"]])
        assert (tmp_path / name).stat().st_size > 0


# ------------------------------------------------------- the helper's label --


def _run_rows(p, sid, origin, calls):
    """A finished run with *calls*: (tool, ok, reversibility) in order."""
    import datetime as dt

    base = dt.datetime(2026, 11, 9, 12, 0, 0)
    with session_scope(p.engine) as db:
        db.add(SessionRow(id=sid, task="t", status=SessionStatus.COMPLETED, origin=origin))
        for i, (tool, ok, rev) in enumerate(calls):
            db.add(ToolInvocation(session_id=sid, agent_run_id="r", tool=tool, ok=ok,
                                  reversibility=rev, created_at=base + dt.timedelta(seconds=i)))
        db.commit()
    with session_scope(p.engine) as db:
        return db.get(SessionRow, sid)


FIXED_LATER = [("shell", False, "irreversible"), ("edit_file", True, "reversible"),
               ("shell", True, "irreversible"), ("read_file", False, "readonly")]
ENDED_BADLY = [("shell", True, "irreversible"), ("edit_file", False, "reversible"),
               ("read_file", True, "readonly")]


def test_a_helper_whose_failed_command_was_followed_by_a_good_one_is_plainly_done(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    row = _run_rows(p, "h1", HELPER_ORIGIN, FIXED_LATER)
    result = session_result(p.engine, "h1")
    assert result["tools_failed_mutating"] == 1 and result["ended_on_failed_change"] is False
    assert orch._settle_finished_run(row, "", SessionStatus.COMPLETED)[1] == OUTCOME_COMPLETED


def test_a_helper_whose_last_change_failed_is_still_part_failed(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    row = _run_rows(p, "h2", HELPER_ORIGIN, ENDED_BADLY)
    assert session_result(p.engine, "h2")["ended_on_failed_change"] is True
    assert orch._settle_finished_run(row, "", SessionStatus.COMPLETED)[1] == OUTCOME_WITH_FAILURES


@pytest.mark.parametrize("origin", ["chat", "schedule:nightly", "job:mission-member", None])
def test_every_other_run_keeps_the_old_rule(tmp_path, origin):
    """Only a coding helper is eased: a schedule or a chat-started agent whose
    command failed along the way still reads part failed."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    row = _run_rows(p, "o1", origin, FIXED_LATER)
    assert orch._settle_finished_run(row, "", SessionStatus.COMPLETED)[1] == OUTCOME_WITH_FAILURES


def test_a_run_with_no_change_at_all_did_not_end_on_a_failed_one(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _run_rows(p, "r1", HELPER_ORIGIN, [("read_file", False, "readonly"), ("grep", True, "readonly")])
    result = session_result(p.engine, "r1")
    assert result["ended_on_failed_change"] is False and result["tools_failed_mutating"] == 0


# ------------------------------------- the model's own idea of "this folder" --


def test_a_path_in_the_models_scratch_folder_means_the_work_folder(tmp_path):
    """Claude Code is started in an empty scratch folder and tells its model
    that is the working directory. Seen live: "read october-expenses.xlsx in
    this folder" became a read of <temp>/ironjarvis-claude-cwd/october-…"""
    import os
    import tempfile

    from iron_jarvis.core.model_cwd import remap

    temp, ws = tempfile.gettempdir(), str(tmp_path)
    scratch = os.path.join(temp, "ironjarvis-claude-cwd")
    assert remap(os.path.join(scratch, "a.xlsx"), ws) == str(tmp_path / "a.xlsx")
    assert remap(scratch, ws) == ws
    assert remap(os.path.join(temp, "ironjarvis-claude-cwd-ab12", "sub", "a.txt"), ws) == str(tmp_path / "sub" / "a.txt")
    # every other path is exactly what it was
    for raw in (os.path.join(str(tmp_path), "ironjarvis-claude-cwd", "x.txt"),  # same name, another place
                "ironjarvis-claude-cwd/x.txt",                                     # not absolute
                os.path.join(temp, "other", "x.txt"),
                os.path.join(scratch, "..", "..", "secret.txt"),                   # climbs out: untouched
                str(tmp_path / "a.xlsx"), ""):
        assert remap(raw, ws) == raw, raw


def test_the_tools_read_and_write_through_that_path(tmp_path):
    import os
    import tempfile

    from iron_jarvis.documents.excel_tools import ExcelProfileTool
    from iron_jarvis.documents.tools import document_tools
    from iron_jarvis.tools.base import safe_path
    from openpyxl import Workbook

    scratch = os.path.join(tempfile.gettempdir(), "ironjarvis-claude-cwd")
    wb = Workbook()
    wb.active.append(["Item", "Amount"])
    wb.active.append(["Rent", 500])
    wb.save(tmp_path / "books.xlsx")
    (tmp_path / "note.txt").write_text("hello there", encoding="utf-8")
    assert safe_path(tmp_path, os.path.join(scratch, "new.txt")) == (tmp_path / "new.txt").resolve()
    with pytest.raises(PermissionError, match="escapes the session workspace"):
        safe_path(tmp_path, os.path.join(tempfile.gettempdir(), "somewhere-else", "x.txt"))
    prof = asyncio.run(ExcelProfileTool().execute({"path": os.path.join(scratch, "books.xlsx")}, _ctx(tmp_path)))
    assert prof.ok and "books.xlsx" in prof.output, prof.error
    read = next(t for t in document_tools() if t.name == "read_document")
    got = asyncio.run(read.execute({"path": os.path.join(scratch, "note.txt")}, _ctx(tmp_path)))
    assert got.ok and "hello there" in got.output, got.error
