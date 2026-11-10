"""Live Excel (v1.466.0), the part that needs no Excel.

GitHub's runners have no Excel, so the child that talks to it is replaced by a
stand-in here (``excel_live._ask_worker``); the real Excel is driven by
``tests/test_excel_live_real_v1466.py`` on a PC that has it.

What this file holds true:

* the child's pure helpers (ranges, colours, cell values, the undo file);
* the two tools' rules: what is read, what is carded, all-or-nothing words,
  the Undo record, the change card;
* chat arms the look tool only for a workbook that is OPEN, the stream lane
  alone can ask to change it, and the chat's Undo reaches Excel again;
* nothing but the child imports the Windows bridge, and the frozen app can
  start the child.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis import excel_worker as W
from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.documents import excel_live as L
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import wants_live_excel
from iron_jarvis.tools.base import ToolContext

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# ------------------------------------------------------------- pure helpers --


@pytest.mark.parametrize(
    "text, box",
    [
        ("B2", (2, 2, 2, 2)), ("b2:D9", (2, 2, 9, 4)), ("$D$9:$B$2", (2, 2, 9, 4)),
        ("B:D", (1, 2, 1_048_576, 4)), ("3:7", (3, 1, 7, 16_384)), ("XFD1048576", (1_048_576, 16_384, 1_048_576, 16_384)),
    ],
)
def test_a_range_is_read_as_a_box(text, box):
    assert W.parse_range(text) == box


@pytest.mark.parametrize("text", ["", "Sheet1!A1", "A0", "XFE1", "A1048577", "hello", "A1:B", "1:A"])
def test_a_bad_range_is_refused_in_words(text):
    with pytest.raises(W.Refusal):
        W.parse_range(text)


def test_cells_and_columns_convert_both_ways():
    assert [W.col_letter(n) for n in (1, 26, 27, 702, 703, 16_384)] == ["A", "Z", "AA", "ZZ", "AAA", "XFD"]
    assert all(W.col_num(W.col_letter(n)) == n for n in (1, 26, 27, 52, 703, 16_384))
    assert W.a1(2, 2) == "B2" and W.a1(2, 2, 9, 4) == "B2:D9" and W.a1(5, 3, 5, 3) == "C5"


def test_a_colour_is_stored_the_way_excel_stores_it():
    assert W.bgr("FF0000") == 255 and W.bgr("#00ff00") == 65_280 and W.bgr("0000FF") == 16_711_680
    with pytest.raises(W.Refusal):
        W.bgr("red")


def test_a_cell_value_reads_as_plain_data():
    assert W.plain(-2146826281) == "#DIV/0!" and W.plain(-2146826246) == "#N/A" and W.plain(-2146826265) == "#REF!"
    assert W.plain(decimal.Decimal("1234.5")) == 1234.5 and W.plain(3.0) == 3 and W.plain(2.5) == 2.5
    assert W.plain(dt.datetime(2026, 11, 10)) == "2026-11-10"
    assert W.plain(dt.datetime(2026, 11, 10, 9, 30)) == "2026-11-10 09:30:00"
    assert W.plain(True) is True and W.plain(None) is None and W.plain("x") == "x"
    assert W.grid(5.0, 1, 1) == [[5.0]] and W.grid(((1, 2), (3, 4)), 2, 2) == [[1, 2], [3, 4]]


def test_the_steps_are_checked_before_excel_is_asked():
    with pytest.raises(W.Refusal, match="at least one step"):
        W.check_steps([])
    with pytest.raises(W.Refusal, match="at most 60"):
        W.check_steps([{"op": "set"}] * 61)
    with pytest.raises(W.Refusal, match="step 2 has no known 'op'"):
        W.check_steps([{"op": "set"}, {"op": "macro"}])
    assert set(L.EDIT_OPS) == set(W.OPS), "the tool offers exactly the steps the child knows"
    assert set(W.DESTRUCTIVE_OPS) <= set(W.OPS)
    assert W.sheet_name_problem("Summary") == "" and W.sheet_name_problem("a/b") and W.sheet_name_problem("x" * 32)


def test_the_undo_file_reads_back_and_survives_a_line_cut_off_by_a_kill(tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text(
        json.dumps({"head": {"workbook": "B.xlsx", "path": "C:/x/B.xlsx", "steps": 3}}) + "\n"
        + json.dumps({"i": 1, "inverse": {"kind": "cells", "sheet": "S", "address": "A1", "formulas": [["1"]]}}) + "\n"
        + json.dumps({"i": 1, "after": {"formulas": [["2"]]}}) + "\n"
        + json.dumps({"i": 2, "inverse": {"kind": "sheet_added", "name": "N"}}) + "\n"
        + '{"i": 3, "inver',  # the child was stopped while writing this line
        encoding="utf-8",
    )
    head, steps = W.read_undo(str(f))
    assert head["workbook"] == "B.xlsx" and [s["i"] for s in steps] == [1, 2]
    assert steps[0]["after"] == {"formulas": [["2"]]} and "after" not in steps[1]


def test_the_child_never_raises_and_refuses_what_it_does_not_know():
    assert W.answer(None)["code"] == "bad_request" and W.answer({"op": "format_disk"})["code"] == "bad_request"


def test_only_the_child_imports_the_windows_bridge():
    """A call into Excel can wait forever; it must never run in the daemon."""
    rx = re.compile(r"^\s*(?:import|from)\s+(?:pythoncom|win32com|pywintypes)\b", re.M)
    hits = [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if rx.search(_text(p))]
    assert hits == ["excel_worker.py"], hits
    top = _text(SRC / "excel_worker.py").split("def _com()")[0]
    assert not rx.search(top), "the bridge is imported inside functions, so the pure helpers load anywhere"


def test_the_frozen_app_can_start_the_child(monkeypatch):
    entry = _text(ROOT / "packaging" / "ironjarvis_entry.py")
    assert 'EXCEL_WORKER_SUBCOMMAND = "excel-worker"' in entry and "from iron_jarvis.excel_worker import main" in entry
    assert '@app.command("excel-worker", hidden=True)' in _text(SRC / "daemon" / "cli.py")
    assert L.WORKER_SUBCOMMAND == "excel-worker"
    spec = _text(ROOT / "packaging" / "ironjarvis.spec")
    assert '"pythoncom"' in spec and '"win32com.client"' in spec
    assert "pywin32" in _text(ROOT / "pyproject.toml")
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert L._worker_argv() == [sys.executable, "excel-worker"]


# ------------------------------------------------- a stand-in for the child --


class FakeExcel:
    """Answers the child's requests from a script and remembers them."""

    def __init__(self) -> None:
        self.asked: list[dict] = []
        self.plan = {"ok": True, "workbook": "Budget.xlsx", "path": "", "steps": [{"op": "set", "summary": "write 1 cell(s) at Data!B4", "overwrites": 1}],
                     "overwrites": 1, "formulas_lost": 0, "destructive": [], "autosave": False, "calculation": "automatic"}
        self.apply = {"ok": True, "workbook": "Budget.xlsx", "path": "", "steps": [{"op": "set", "summary": "write 1 cell(s) at Data!B4"}],
                      "changes": [{"sheet": "Data", "cell": "B4", "before": 0.05, "after": 0.08}], "changed_cells": 1,
                      "new_errors": [], "destructive": [], "copy": "", "autosave": False, "calculation": "automatic", "saved": False}
        self.restore = {"ok": True, "workbook": "Budget.xlsx", "restored": 1, "left": [], "steps": 1}
        self.look = {"ok": True, "excel_open": True, "instances": 1, "protected_view": [],
                     "workbooks": [{"name": "Budget.xlsx", "path": "", "saved": False, "read_only": False, "autosave": False, "active": True,
                                    "sheets": [{"name": "Data", "visible": True, "used": "A1:B4", "rows": 4, "cols": 2, "pivots": ["ByRegion"]}]}],
                     "active": {"workbook": "Budget.xlsx", "sheet": "Data", "selection": "B4", "cell": "B4", "calculation": "automatic"}}

    def __call__(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        op = request["op"]
        if op == "apply" and request.get("undo_path"):
            Path(request["undo_path"]).write_text(json.dumps({"head": {"workbook": "Budget.xlsx", "path": "", "steps": 1}}) + "\n", encoding="utf-8")
        if op == "look" and request.get("range"):
            return dict(self.look, range={"workbook": "Budget.xlsx", "path": self.look["workbooks"][0]["path"], "sheet": "Data", "address": "A1:B4",
                                          "rows": [["Rate", None], ["a", 1], ["b", 2], ["growth", 0.05]],
                                          "formulas": {"B2": "=A2*2", "B3": "=A3*2"}, "truncated": False, "total_rows": 4})
        return dict(getattr(self, op))

    def ops(self) -> list[str]:
        return [r["op"] for r in self.asked]


@pytest.fixture()
def excel(monkeypatch):
    fake = FakeExcel()
    monkeypatch.setattr(L, "_ask_worker", fake)
    return fake


def _ctx(home: Path) -> ToolContext:
    return ToolContext(workspace=home / "ws", session_id="chat", agent_run_id="r",
                       config=SimpleNamespace(home=home), event_bus=None, engine=None)


EDIT = {"workbook": "Budget.xlsx", "ops": [{"op": "set", "sheet": "Data", "cell": "B4", "value": 0.08}]}


# ---------------------------------------------------------------- the tools --


async def test_looking_says_what_is_open_and_shows_a_filled_formula_once(excel, tmp_path):
    res = await L.ExcelLiveLookTool().execute({"range": "used"}, _ctx(tmp_path))
    assert res.ok
    assert "open workbook: Budget.xlsx (in front, has unsaved changes)" in res.output
    assert "sheet Data: A1:B4 (pivot tables: ByRegion)" in res.output
    assert "selected now: Data!B4 in Budget.xlsx (active cell B4)" in res.output
    assert "\tA\tB" in res.output and "4\tgrowth\t0.05" in res.output
    assert "B2:B3: =A2*2 (the same down the column)" in res.output
    assert L.ExcelLiveLookTool.returns_untrusted_content, "a workbook's text is someone else's words"


async def test_excel_not_open_is_said_plainly_and_a_refusal_is_the_childs_own_sentence(excel, tmp_path):
    excel.look = {"ok": True, "excel_open": False, "workbooks": []}
    res = await L.ExcelLiveLookTool().execute({}, _ctx(tmp_path))
    assert res.ok and res.output.startswith("Excel is not open on this PC")
    excel.look = {"ok": False, "code": "busy", "error": W.BUSY_WORDS}
    res = await L.ExcelLiveLookTool().execute({}, _ctx(tmp_path))
    assert not res.ok and "Finish typing in the cell" in res.error


async def test_a_workbook_in_a_folder_jarvis_may_not_read_is_not_read(excel, tmp_path, monkeypatch):
    excel.look["workbooks"][0]["path"] = str(tmp_path / "private" / "Budget.xlsx")
    monkeypatch.setattr("iron_jarvis.core.fs_policy.fs_read_ok", lambda p: (False, "That folder isn't one Jarvis may read."))
    res = await L.ExcelLiveLookTool().execute({"range": "used"}, _ctx(tmp_path))
    assert not res.ok and res.error == "That folder isn't one Jarvis may read."
    excel.plan["path"] = str(tmp_path / "private" / "Budget.xlsx")
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and "apply" not in excel.ops(), "nothing is changed in a workbook Jarvis may not read"


async def test_a_change_lists_before_and_after_and_keeps_an_undo_record(excel, tmp_path):
    tool = L.ExcelLiveEditTool()
    args = dict(EDIT)
    undo = await tool.capture_undo(args, _ctx(tmp_path))
    res = await tool.execute(args, _ctx(tmp_path))
    assert res.ok, res.error
    assert res.output.splitlines()[0] == "Changed Budget.xlsx, open in Excel (1 cell on Data). It is NOT saved: the user saves it in Excel."
    assert "  Data!B4: 0.05 -> 0.08" in res.output and "Ctrl+Z history is cleared" in res.output
    assert undo["kind"] == "excel_live_restore" and Path(undo["pre_ref"]).parent == tmp_path / "excel-live" / "undo"
    apply = next(r for r in excel.asked if r["op"] == "apply")
    assert apply["undo_path"] == undo["pre_ref"], "the child writes its undo record where the journal row points"
    card = res.data["excel_change"]
    assert card == {"title": "Excel changed", "key": "excel:Budget.xlsx", "label": "Budget.xlsx", "old": None,
                    "new": "1 cell on Data", "change_id": json.loads(undo["pre_inline"])["change_id"]}
    assert CT.config_cards_from_result("excel_live_edit", res) == [{"kind": "change", "restart": False, **card}]
    assert CT.config_cards_from_result("excel_edit", res) == [], "only the live tool's result makes this card"


async def test_a_refused_plan_changes_nothing_and_says_why(excel, tmp_path):
    excel.plan = {"ok": False, "code": "no_such_sheet", "error": "step 1 (set): 'Budget.xlsx' has no sheet called 'Dta'. Its sheets: Data."}
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and "no sheet called 'Dta'" in res.error and "apply" not in excel.ops()


async def test_a_failed_change_is_all_or_nothing_in_the_childs_words(excel, tmp_path):
    excel.apply = {"ok": False, "code": "excel_refused", "error": "step 2 (sort): Excel refused it: x. Nothing was changed: the 1 step(s) before it were put back."}
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and "Nothing was changed" in res.error


async def test_a_change_stopped_part_way_is_put_back(excel, tmp_path):
    excel.apply = {"ok": False, "code": "no_answer", "error": L.NO_ANSWER, "timed_out": True}
    tool = L.ExcelLiveEditTool()
    args = dict(EDIT)
    undo = await tool.capture_undo(args, _ctx(tmp_path))
    Path(undo["pre_ref"]).write_text("{}\n", encoding="utf-8")
    res = await tool.execute(args, _ctx(tmp_path))
    assert not res.ok and "stopped part-way" in res.error and "back to how it was" in res.error
    back = excel.asked[-1]
    assert back["op"] == "restore" and back["forgiving"] is True and back["undo_path"] == undo["pre_ref"]


async def test_new_errors_manual_calculation_and_autosave_are_said(excel, tmp_path):
    excel.apply.update(new_errors=["Data!E3", "Data!E7"], calculation="manual", autosave=True)
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert "WARNING: these formulas show an error now and did not before: Data!E3, Data!E7" in res.output
    assert "calculation is set to manual" in res.output
    assert "AutoSave is on, so Excel has already saved this" in res.output and "NOT saved" not in res.output


async def test_a_small_change_follows_the_ordinary_approval(excel, tmp_path):
    assert await L.ExcelLiveEditTool().spend_check(dict(EDIT), _ctx(tmp_path)) is None
    excel.plan.update(overwrites=L.OVERWRITE_CARD_CELLS)
    assert await L.ExcelLiveEditTool().spend_check(dict(EDIT), _ctx(tmp_path)) is None


async def test_replacing_data_is_shown_first_even_when_edits_are_allowed(excel, tmp_path):
    excel.plan.update(overwrites=L.OVERWRITE_CARD_CELLS + 1)
    check = await L.ExcelLiveEditTool().spend_check(dict(EDIT), _ctx(tmp_path))
    assert check.ask and not check.once_only and check.dont_ask_skips
    assert check.words == "In Budget.xlsx, open in Excel: this replaces 11 cells that hold data. It can be undone from the chat."
    excel.plan.update(overwrites=1, formulas_lost=1)
    check = await L.ExcelLiveEditTool().spend_check(dict(EDIT), _ctx(tmp_path))
    assert check.ask and "replaces 1 cell that holds data, 1 of them formula replaced by plain values" in check.words


async def test_a_step_that_cannot_be_undone_asks_every_time_and_keeps_a_copy(excel, tmp_path):
    excel.plan.update(destructive=["delete rows 3:4 on Data (6 cell(s) hold something)"])
    tool = L.ExcelLiveEditTool()
    check = await tool.spend_check(dict(EDIT), _ctx(tmp_path))
    assert check.ask and check.once_only and not check.dont_ask_skips, "asked even under Don't ask"
    assert "delete rows 3:4 on Data" in check.words and "cannot be undone here" in check.words
    excel.apply.update(destructive=excel.plan["destructive"])
    await tool.execute(dict(EDIT), _ctx(tmp_path))
    apply = next(r for r in excel.asked if r["op"] == "apply")
    copy = Path(apply["copy_path"])
    assert copy.parent == tmp_path / "excel-live" / "copies" and copy.name.endswith("Budget.xlsx")


async def test_the_plan_is_asked_once_for_the_card_and_the_change(excel, tmp_path):
    tool = L.ExcelLiveEditTool()
    args = dict(EDIT)
    await tool.spend_check(args, _ctx(tmp_path))
    await tool.capture_undo(args, _ctx(tmp_path))
    await tool.execute(args, _ctx(tmp_path))
    assert excel.ops() == ["plan", "apply"]


async def test_undo_says_what_was_left_alone(excel, tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    words, ok = await L.restore({"pre_ref": str(f)})
    assert ok and words == "Budget.xlsx is back to how it was before that change."
    excel.restore.update(left=["Data!B4 changed again since, so it was left as it is"])
    words, ok = await L.restore({"pre_ref": str(f)})
    assert ok and words.startswith("Put back most of that change in Budget.xlsx. Left as it is: Data!B4 changed again since")
    excel.restore.update(restored=0)
    words, ok = await L.restore({"pre_ref": str(f)})
    assert not ok and words.startswith("Nothing in Budget.xlsx was put back")
    words, ok = await L.restore({"pre_ref": str(tmp_path / "gone.jsonl")})
    assert not ok and "no longer kept" in words


async def test_a_live_path_is_a_copy_named_after_the_workbook_and_other_paths_pass_through(excel, tmp_path, monkeypatch):
    assert await L.live_path("budget.xlsx", _ctx(tmp_path)) == ("budget.xlsx", "")

    def fake(request, timeout):
        excel.asked.append(request)
        out = Path(request["dir"]) / "Budget.xlsx"
        out.write_bytes(b"x")
        return {"ok": True, "path": str(out), "workbook": "Budget.xlsx", "source": "Book1", "saved": False}

    monkeypatch.setattr(L, "_ask_worker", fake)
    got, why = await L.live_path("live:Budget.xlsx", _ctx(tmp_path))
    assert why == "" and Path(got).name == "Budget.xlsx" and L.is_live_copy(got)
    assert excel.asked[-1]["workbook"] == "Budget.xlsx" and not L.is_live_copy(tmp_path / "ws" / "Budget.xlsx")
    monkeypatch.setattr(L, "_ask_worker", lambda r, t: {"ok": False, "error": "Excel is not open on this PC. Open the workbook in Excel first."})
    got, why = await L.live_path("live:", _ctx(tmp_path))
    assert got == "" and why.startswith("Excel is not open")
    assert not any((tmp_path / "excel-live" / "snap").glob("*/missing"))


def test_what_is_kept_is_swept_by_age(tmp_path):
    undo, copies, snap = (L.live_dir(tmp_path, p) for p in ("undo", "copies", "snap"))
    old = time.time() - 40 * 86400
    for d in (undo, copies):
        (d / "old").write_text("x")
        os.utime(d / "old", (old, old))
        (d / "new").write_text("x")
    (snap / "s1").mkdir()
    (snap / "s1" / "Budget.xlsx").write_text("x")
    os.utime(snap / "s1", (time.time() - 3600, time.time() - 3600))
    (snap / "s2").mkdir()
    L.sweep(tmp_path)
    assert [p.name for p in undo.iterdir()] == ["new"] and [p.name for p in copies.iterdir()] == ["new"]
    assert [p.name for p in snap.iterdir()] == ["s2"]


def test_backups_leave_the_live_excel_folder_out(tmp_path):
    import tarfile

    from iron_jarvis.maintenance import create_backup

    home = tmp_path / "home"
    (L.live_dir(home, "undo") / "xl_1.jsonl").write_text("client data")
    (home / "config.toml").write_text("")
    out, _size = create_backup(home, tmp_path / "backup.tar.gz")
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert any(n.endswith("config.toml") for n in names) and not any("excel-live" in n for n in names)


def test_the_child_is_never_started_off_windows(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    assert L._ask_worker({"op": "look"}, 1) == {"ok": False, "code": "not_windows", "error": L.NOT_WINDOWS}
    monkeypatch.delenv(L.SEEN_ENV, raising=False)
    assert L.excel_seen() is False


# ------------------------------------------------------------------- arming --

LIVE = [
    "change the growth rate in the open workbook to 8%", "what is in the selected cells?",
    "sum the cells I have selected", "fix the formula in the cell I am on", "format the active sheet",
    "the spreadsheet I have open has an error", "add a total row to the sheet I'm on",
    "this file is open in Excel, tidy the headings", "look at the current worksheet",
]
ONLY_WHILE_OPEN = [
    "why does this sheet show #REF?", "add a pivot table by region", "change cell B4 to 8%",
    "total A1:C9 for me", "how do I write a VLOOKUP in Excel?", "sort my selection by amount",
]
NEVER = [
    "Q3 revenue was up", "the K1 for the trust arrived", "open a new chat", "review my W2",
    "what is the selected candidate", "we have an open position", "summarize the meeting",
    "what's on the current agenda", "is the store open today",
]


@pytest.mark.parametrize("text", LIVE)
def test_an_open_workbook_said_outright_arms_the_look(text):
    assert wants_live_excel(text, False) and wants_live_excel(text, True)


@pytest.mark.parametrize("text", ONLY_WHILE_OPEN)
def test_spreadsheet_talk_means_the_open_workbook_only_while_excel_runs(text):
    assert wants_live_excel(text, True) and not wants_live_excel(text, False)
    assert not wants_live_excel(text, True, has_files=True), "a file sent with the message is what a file tool reads"


@pytest.mark.parametrize("text", NEVER + ["summarize budget.xlsx", "add a sheet to report.xlsm"])
def test_ordinary_talk_and_a_named_file_arm_nothing(text):
    assert not wants_live_excel(text, True) and not wants_live_excel(text, False)


def test_the_arming_words_are_linear_on_a_long_message():
    start = time.perf_counter()
    for text in ("open " * 20_000, "A1:" * 20_000, "the cells i " * 8_000, "workbook i have " * 6_000):
        wants_live_excel(text, True)
    assert time.perf_counter() - start < 2.0


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    return SimpleNamespace(platform=build_platform(str(tmp_path_factory.mktemp("live") / "home")))


def _armed(d, text, **kw):
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True, **kw)
    return list(CT._resolve_armed_tools(d, body)[0])


def test_chat_arms_the_look_tool_and_never_grants_the_change_tool(d, monkeypatch):
    assert "excel_live_look" in _armed(d, "what is in the selected cells?")
    assert "excel_live_look" not in _armed(d, "add a pivot table by region"), "Excel is not running in the test suite"
    monkeypatch.setenv(L.SEEN_ENV, "1")
    armed = _armed(d, "add a pivot table by region")
    assert "excel_live_look" in armed and "excel_live_edit" not in armed
    assert "excel_live_look" not in _armed(d, "add a pivot table by region", attachments=["C:/x/sales.xlsx"])
    body = ChatBody(messages=[{"role": "user", "content": "what is in the selected cells?"}], auto_tools=False)
    assert "excel_live_look" not in CT._resolve_armed_tools(d, body)[0]
    perms = d.platform.permissions
    assert perms.mode_for("excel_live_look").value == "allow" and perms.mode_for("excel_live_edit").value == "ask"


# ------------------------------------------------------ through the chat lane --

CLAUDE = ("anthropic", "claude-sonnet-4-5")


def _frames(chunks):
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


class Model:
    def __init__(self, moves):
        self.moves, self.tools_seen = list(moves), []

    async def stream(self, *, system, messages, tools, provider=None, model=None, **kw):
        self.tools_seen.append([t["name"] for t in tools or []])
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "text", "text": move}
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": CLAUDE[0], "model": CLAUDE[1]}

    async def complete(self, *, system, messages, tools, provider=None, model=None, **kw):
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={}), *CLAUDE)
        return RouteResult(LLMResponse(text="", tool_calls=list(move), usage={}), *CLAUDE)


@pytest.fixture()
def lane(tmp_path, monkeypatch, excel):
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)

    def wire(moves):
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return SimpleNamespace(platform=platform, wire=wire, excel=excel)


def _body(text="change the growth rate in the open workbook to 8%", **kw):
    return ChatBody(messages=[ChatMessageBody(role="user", content=text)], provider=CLAUDE[0], model=CLAUDE[1],
                    auto_tools=True, **kw)


async def _stream(platform, body, answer="once"):
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
    frames = []
    async for chunk in gen:
        for event, data in _frames([chunk]):
            frames.append((event, data))
            if event == "approval" and answer:
                assert approvals.resolve(str(data["id"]), answer)
    await flush_interrupt_notes()
    done = [x for e, x in frames if e == "done"]
    assert done, frames[-4:]
    return {"done": done[0], "cards": [x for e, x in frames if e == "approval"]}


def _calls():
    return [ToolCall(id="l1", name="excel_live_look", arguments={"range": "used"})], \
           [ToolCall(id="e1", name="excel_live_edit", arguments=dict(EDIT))]


async def test_the_stream_lane_looks_freely_asks_before_a_change_and_shows_its_undo(lane):
    look, edit = _calls()
    model = lane.wire([look, edit, "The growth rate is 8% now."])
    out = await _stream(lane.platform, _body(), answer="once")
    assert "excel_live_look" in model.tools_seen[0] and "excel_live_edit" in model.tools_seen[0]
    assert [c["tool"] for c in out["cards"]] == ["excel_live_edit"], "looking needs no card; a change asks"
    assert lane.excel.ops() == ["look", "plan", "apply"]
    cards = out["done"]["config_cards"]
    assert len(cards) == 1 and cards[0]["title"] == "Excel changed" and cards[0]["label"] == "Budget.xlsx"
    with session_scope(lane.platform.engine) as db:
        row = db.exec(select(UndoJournal).where(UndoJournal.kind == "excel_live_restore")).one()
        action, ref = row.action_id, row.pre_ref
        assert cards[0]["change_id"] in (row.pre_inline or "")
        inv = db.get(ToolInvocation, action)
        assert inv.tool == "excel_live_edit" and inv.ok
    assert Path(ref).is_file()
    # The chat's Undo: no tool permission is asked, and Excel is asked to put it back.
    d = SimpleNamespace(platform=lane.platform)
    done = await undo_routes.perform_undo(d, action)
    assert "back to how it was" in json.dumps(done)
    assert lane.excel.asked[-1]["op"] == "restore" and lane.excel.asked[-1]["undo_path"] == ref


async def test_a_declined_change_never_reaches_excel(lane):
    look, edit = _calls()
    lane.wire([look, edit, "I left it as it was."])
    out = await _stream(lane.platform, _body(), answer="deny")
    assert len(out["cards"]) == 1 and "apply" not in lane.excel.ops()
    assert out["done"]["config_cards"] == []


async def test_an_undo_excel_cannot_make_is_refused_in_words(lane):
    look, edit = _calls()
    lane.wire([look, edit, "Done."])
    await _stream(lane.platform, _body())
    with session_scope(lane.platform.engine) as db:
        action = db.exec(select(UndoJournal).where(UndoJournal.kind == "excel_live_restore")).one().action_id
    lane.excel.restore = {"ok": False, "code": "no_such_workbook", "error": "No open workbook is called 'Budget.xlsx'. Open in Excel now: Other.xlsx."}
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as err:
        await undo_routes.perform_undo(SimpleNamespace(platform=lane.platform), action)
    assert err.value.status_code == 409 and "No open workbook is called 'Budget.xlsx'" in err.value.detail


async def test_the_post_lane_can_look_but_is_never_shown_the_change_tool(lane):
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    look, _edit = _calls()
    model = lane.wire([look, "B4 holds the growth rate, 5%."])
    out = await run_chat_turn(lane.platform, BUILTIN_PERSONAS, _body("what is in the selected cells?"))
    assert lane.excel.ops() == ["look"] and "5%" in out["reply"]
    assert model.moves == []


async def test_a_large_overwrite_is_carded_once_only_with_its_words(lane):
    look, edit = _calls()
    lane.excel.plan.update(overwrites=40, formulas_lost=12)
    lane.wire([look, edit, "Done."])
    out = await _stream(lane.platform, _body(), answer="conversation")
    card = out["cards"][0]
    assert "replaces 40 cells that hold data, 12 of them formulas replaced by plain values" in json.dumps(card)
    assert lane.excel.ops().count("apply") == 1
