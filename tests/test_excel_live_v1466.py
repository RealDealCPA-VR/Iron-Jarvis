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
    excel.apply.update(destructive=excel.plan["destructive"], copy="C:/kept/Budget.xlsx")
    res = await tool.execute(dict(EDIT), _ctx(tmp_path))
    assert "THE DELETION CANNOT BE UNDONE" in res.output and "C:/kept/Budget.xlsx" in res.output
    assert "can undo this whole call" not in res.output, "a model repeated that line to the user after a delete"
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
    assert ok and words.startswith("Put back part of that change in Budget.xlsx. Left as it is: Data!B4 changed again since")
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


def test_the_suite_never_starts_the_child(monkeypatch):
    """No test may look into the developer's own Excel."""
    from iron_jarvis.core import procs

    monkeypatch.setattr(procs, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the child was started")))
    assert os.environ.get(L.OFF_ENV) == "off"
    if os.name == "nt":
        assert L._ask_worker({"op": "look"}, 1)["code"] == "off"
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    doctor._LIVE_EXCEL_OK.clear()
    row = doctor.check_live_excel()
    assert row["name"] == "live_excel" and row["ok"] and row["level"] == "recommended"


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


def test_a_reply_about_the_changed_workbook_is_not_called_a_false_claim():
    """The workbook WAS changed (in Excel, unsaved), so the "nothing was
    written to disk" note stays away."""
    reply = "I updated Budget.xlsx: the growth rate in B4 is now 8%, and I saved the change to the workbook."
    assert CT._claimed_write_note(reply, ["excel_live_edit"]) == ""
    assert CT._may_have_changed_files(["excel_live_edit"]) and not CT._may_have_changed_files(["excel_live_look"])


# ------------------------------------------- what the first live trial showed --


def test_a_range_written_with_its_sheet_is_read_and_two_sheets_are_refused():
    assert W.split_sheet({"op": "sort", "range": "Sales!A1:D49"}) == {"op": "sort", "range": "A1:D49", "sheet": "Sales"}
    assert W.split_sheet({"op": "chart", "data": "'My Sheet'!A1:B9", "sheet": "my sheet"})["data"] == "A1:B9"
    assert W.split_sheet({"op": "set", "cell": "B4", "sheet": "Data"}) == {"op": "set", "cell": "B4", "sheet": "Data"}
    assert W.split_sheet({"op": "name", "name": "T", "refers_to": "Data!$B$2"})["refers_to"] == "Data!$B$2"
    with pytest.raises(W.Refusal, match="names the sheet 'Sales' but 'sheet' says 'Data'"):
        W.split_sheet({"op": "sort", "range": "Sales!A1:D49", "sheet": "Data"})


async def test_a_refusal_leads_with_nothing_was_changed(excel, tmp_path):
    """A model told only "Excel refused it: ..." said the rows were deleted."""
    excel.apply = {"ok": False, "code": "excel_refused", "unchanged": True,
                   "error": "step 1 (delete_rows): Excel refused it: We can't make this change because it will affect a PivotTable."}
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and res.error.startswith("NOTHING WAS CHANGED in the workbook. step 1 (delete_rows)")
    excel.apply = {"ok": False, "code": "partly_done", "partly": True, "error": "x The 2 step(s) before it were made and could not all be put back: y"}
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and "NOTHING WAS CHANGED" not in res.error, "never said when something did change"


def test_the_note_is_decided_by_what_ran_never_by_the_replys_words():
    L._TURNS.clear()
    ctx = SimpleNamespace(turn_id="t-1")
    assert L.unchanged_note("t-1", []) == "", "a turn that never tried says nothing"
    L.note_turn(ctx, False)
    assert L.unchanged_note("t-1", []) == L.UNCHANGED_NOTE
    assert L.unchanged_note("t-1", ["excel_live_edit"]) == "" and L.unchanged_note("", []) == ""
    L.note_turn(ctx, True)
    L.note_turn(ctx, False)  # a later try failed, but a change DID land this turn
    assert L.unchanged_note("t-1", []) == ""
    L.note_turn(SimpleNamespace(turn_id=""), False)
    assert "" not in L._TURNS


async def test_a_reply_that_claims_a_change_excel_refused_gets_the_note_in_both_lanes(lane):
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    look, edit = _calls()
    lane.excel.apply = {"ok": False, "code": "excel_refused", "unchanged": True, "error": "step 1 (set): Excel refused it: the sheet is protected."}
    lane.wire([look, edit, "Done! The growth rate is 8% now."])
    out = await _stream(lane.platform, _body(turn_id="turn-stream-1"))
    assert out["done"]["reply"].endswith(L.UNCHANGED_NOTE) and out["done"]["config_cards"] == []
    lane.wire([look, edit, "All set."])
    out = await _stream(lane.platform, _body(turn_id="turn-stream-2"), answer="deny")
    assert out["done"]["reply"].endswith(L.UNCHANGED_NOTE) is False, "a declined card never reached the tool, so the tool has nothing to say"
    lane.excel.apply = dict(FakeExcel().apply)
    lane.wire([look, edit, "The growth rate is 8% now."])
    out = await _stream(lane.platform, _body(turn_id="turn-stream-3"))
    assert L.UNCHANGED_NOTE not in out["done"]["reply"]
    # The POST lane arms no change tool, so it has nothing to note.
    lane.wire([look, "B4 is 5%."])
    post = await run_chat_turn(lane.platform, BUILTIN_PERSONAS, _body("what is in the selected cells?", turn_id="turn-post-1"))
    assert L.UNCHANGED_NOTE not in post["reply"]


def test_both_lanes_append_the_note():
    for path in ("daemon/chat_turn.py", "daemon/routes/chat.py"):
        assert "_xl_unchanged(getattr(body, \"turn_id\", \"\"), tools_used)" in _text(SRC / path), path


# ------------------------------------------------------------ the review's pins --
# Each of these was RED on the first cut.

DELETE = {"workbook": "Budget.xlsx", "ops": [{"op": "delete_rows", "sheet": "Data", "at": 3, "count": 2}]}


async def test_a_deletion_asks_once_only_whatever_excel_said_about_the_plan(excel, tmp_path):
    """The card was decided from the child's plan alone: with Excel busy for
    that one moment, or the rows on a sheet renamed earlier in the call, a
    deletion ran on the ordinary approval (none at all under Don't ask) and
    with no copy kept."""
    tool = L.ExcelLiveEditTool()
    excel.plan = {"ok": False, "code": "busy", "error": W.BUSY_WORDS}
    check = await tool.spend_check(dict(DELETE), _ctx(tmp_path))
    assert check is not None and check.ask and check.once_only and not check.dont_ask_skips
    assert "cannot be undone" in check.words
    excel.plan = dict(FakeExcel().plan)  # ...and a plan that did not see the deletion as one
    tool = L.ExcelLiveEditTool()
    check = await tool.spend_check(dict(DELETE), _ctx(tmp_path))
    assert check is not None and check.ask and check.once_only
    res = await tool.execute(dict(DELETE), _ctx(tmp_path))
    assert res.ok, res.error
    copy = next(r for r in excel.asked if r["op"] == "apply")["copy_path"]
    assert copy and Path(copy).parent == tmp_path / "excel-live" / "copies", "the copy is kept for every deletion"
    assert L.is_destructive([{"op": "set"}, {"op": "delete_sheet"}]) and not L.is_destructive([{"op": "set"}, {"op": "clear"}])
    assert not L.is_destructive("nonsense") and not L.is_destructive([None, 3])
    assert L.DESTRUCTIVE_OPS == W.DESTRUCTIVE_OPS, "one list of what cannot be put back"
    assert set(L.EDIT_OPS) == set(W.OPS), "the tool offers exactly the steps the child knows"


async def test_the_change_lands_on_the_workbook_the_card_named(tmp_path, monkeypatch):
    """With no workbook named, "the one in front" was worked out again after
    the card: a user who looked at another workbook while deciding had the
    change (a deletion, even) made in THAT one."""
    asked: list[dict] = []
    front = {"path": r"C:\Clients\A\Budget.xlsx", "name": "Budget.xlsx"}

    def child(request, timeout):
        asked.append(request)
        path = request.get("workbook") or front["path"]
        name = path.rsplit("\\", 1)[-1]
        if request["op"] == "plan":
            return {"ok": True, "workbook": name, "path": path, "steps": [], "overwrites": 0, "formulas_lost": 0,
                    "destructive": ["delete rows 3:4 on Data (6 cell(s) hold something)"], "autosave": False, "calculation": "automatic"}
        return dict(FakeExcel().apply, workbook=name, path=path)

    monkeypatch.setattr(L, "_ask_worker", child)
    monkeypatch.setattr(L, "_read_refusal", lambda path: "")
    tool = L.ExcelLiveEditTool()
    args = {"ops": DELETE["ops"]}
    check = await tool.spend_check(args, _ctx(tmp_path))
    assert "In Budget.xlsx" in check.words
    front.update(path=r"C:\Clients\B\Payroll.xlsx", name="Payroll.xlsx")  # the user looks at another workbook
    tool._plans.clear()  # ...for longer than the plan is remembered
    res = await tool.execute(args, _ctx(tmp_path))
    assert res.ok and res.data["workbook"] == "Budget.xlsx", res.output
    assert [r["workbook"] for r in asked if r["op"] == "apply"] == [r"C:\Clients\A\Budget.xlsx"]


async def test_a_child_stopped_before_it_began_says_nothing_was_changed(tmp_path, monkeypatch):
    """No undo record means the child never reached its first step. The answer
    said "stopped part-way ... cannot be undone" about a workbook nobody touched."""
    asked = []

    def child(request, timeout):
        asked.append(request["op"])
        if request["op"] == "plan":
            return dict(FakeExcel().plan)
        return {"ok": False, "code": "no_answer", "error": L.NO_ANSWER, "timed_out": True}  # and it wrote no undo record

    monkeypatch.setattr(L, "_ask_worker", child)
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and res.error.startswith(L.UNCHANGED_LEAD) and "part-way" not in res.error
    assert asked == ["plan", "apply"], "there is nothing to put back"


async def test_a_stopped_change_that_could_not_be_put_back_is_never_called_unchanged(excel, tmp_path):
    L._TURNS.clear()
    excel.apply = {"ok": False, "code": "no_answer", "error": L.NO_ANSWER, "timed_out": True}
    excel.restore = {"ok": False, "code": "busy", "error": W.BUSY_WORDS}
    ctx = _ctx(tmp_path)
    ctx.turn_id = "t-stopped"
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), ctx)
    assert not res.ok and "stopped part-way" in res.error and "may still be in the workbook" in res.error
    assert L.unchanged_note("t-stopped", []) == "", "something may have changed: the reply is not told otherwise"
    excel.restore = dict(FakeExcel().restore)
    ctx.turn_id = "t-put-back"
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), ctx)
    assert "back to how it was" in res.error and L.unchanged_note("t-put-back", []) == L.UNCHANGED_NOTE


async def test_looking_says_nothing_about_a_workbook_in_a_folder_jarvis_may_not_read(excel, tmp_path, monkeypatch):
    """Without a range, the look listed every open workbook's sheets, tables
    and selection whatever folder it was in."""
    private = str(tmp_path / "private" / "Payroll.xlsx")
    excel.look["workbooks"].append({"name": "Payroll.xlsx", "path": private, "saved": True, "read_only": False, "autosave": False, "active": False,
                                    "sheets": [{"name": "Smith K-1", "visible": True, "used": "A1:D9", "tables": ["Salaries"]}]})
    monkeypatch.setattr("iron_jarvis.core.fs_policy.fs_read_ok", lambda p: (("private" not in str(p)), "That folder isn't one Jarvis may read."))
    res = await L.ExcelLiveLookTool().execute({}, _ctx(tmp_path))
    assert res.ok and "Payroll.xlsx" in res.output and "not one Jarvis may read" in res.output
    assert "Smith K-1" not in res.output and "Salaries" not in res.output and "private" not in res.output
    assert "Smith K-1" not in json.dumps(res.data) and "sheet Data: A1:B4" in res.output, "the readable workbook is still listed in full"
    excel.look["active"] = {"workbook": "Payroll.xlsx", "path": private, "sheet": "Smith K-1", "selection": "B4", "cell": "B4", "calculation": "automatic"}
    res = await L.ExcelLiveLookTool().execute({}, _ctx(tmp_path))
    assert "Smith K-1" not in res.output and "Smith K-1" not in json.dumps(res.data)


async def test_a_refused_plan_for_a_workbook_jarvis_may_not_read_quotes_nothing_from_it(excel, tmp_path, monkeypatch):
    private = str(tmp_path / "private" / "Payroll.xlsx")
    excel.plan = {"ok": False, "code": "bad_step", "path": private,
                  "error": "step 1 (sort): 'Pay' is neither a column letter inside the range nor one of its headings (Name, SSN, Salary)"}
    monkeypatch.setattr("iron_jarvis.core.fs_policy.fs_read_ok", lambda p: (False, "That folder isn't one Jarvis may read."))
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), _ctx(tmp_path))
    assert not res.ok and "SSN" not in res.error and "isn't one Jarvis may read" in res.error
    excel.plan = dict(FakeExcel().plan, path=private, destructive=["delete rows 3:4 on Smith K-1 (6 cell(s) hold something)"])
    check = await L.ExcelLiveEditTool().spend_check(dict(DELETE), _ctx(tmp_path))
    assert check.once_only and "Smith K-1" not in check.words


async def test_undo_never_says_most_when_it_cannot_know(excel, tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    excel.restore.update(restored=1, left=["Data!B5, B6 and 40 more changed again since, so they were left as they are"])
    words, ok = await L.restore({"pre_ref": str(f)})
    assert ok and words.startswith("Put back part of that change in Budget.xlsx. Left as it is:") and "most" not in words


def test_a_folder_check_that_fails_is_a_refusal_not_a_pass(monkeypatch, tmp_path):
    def boom(p):
        raise RuntimeError("policy unreadable")

    monkeypatch.setattr("iron_jarvis.core.fs_policy.fs_read_ok", boom)
    assert L._read_refusal(str(tmp_path / "Budget.xlsx")) != ""
    assert L._read_refusal("Book1") == "" and L._read_refusal("https://d.docs.live.net/x/Budget.xlsx") == ""


async def test_a_write_excel_dropped_is_said_and_nothing_at_all_is_not_a_change(excel, tmp_path):
    L._TURNS.clear()
    warn = "no cell changed at Data!H1: it already held this, or Excel did not take it (a merged cell takes a value only in its first cell)"
    excel.apply.update(changes=[], changed_cells=0, steps=[{"op": "set", "summary": "write 1 cell(s) at Data!H1", "warning": warn, "no_change": True}])
    ctx = _ctx(tmp_path)
    ctx.turn_id = "t-merged"
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), ctx)
    assert not res.ok and res.error.startswith(L.UNCHANGED_LEAD) and "merged cell" in res.error
    assert L.unchanged_note("t-merged", []) == L.UNCHANGED_NOTE
    excel.apply.update(changed_cells=1, changes=[{"sheet": "Data", "cell": "B4", "before": 1, "after": 2}],
                       steps=[{"op": "set", "summary": "write 1 cell(s) at Data!B4"}, {"op": "set", "summary": "write 1 cell(s) at Data!H1", "warning": warn, "no_change": True}])
    res = await L.ExcelLiveEditTool().execute(dict(EDIT), ctx)
    assert res.ok and "2. write 1 cell(s) at Data!H1" in res.output and "WARNING: no cell changed at Data!H1" in res.output


def test_a_copy_keeps_the_workbooks_own_ending():
    assert W.copy_name("ledger.csv") == "ledger.csv" and W.copy_name("Budget.xlsm") == "Budget.xlsm"
    assert W.copy_name("Book1") == "Book1.xlsx" and W.copy_name('a<b>:c?.xlsx') == "a_b_c_.xlsx"
    long = "Q" * 80 + ".xlsm"
    assert L._safe_name(long).endswith(".xlsm") and len(L._safe_name(long)) <= 70, "a long name keeps its ending"
    assert L._safe_name("ledger.csv") == "ledger.csv"


async def test_the_copy_kept_before_a_deletion_is_a_file_excel_can_open(excel, tmp_path):
    for name, ending in (("ledger.csv", "ledger.csv"), ("Book1", "Book1.xlsx"), ("Q" * 80 + ".xlsm", ".xlsm")):
        excel.asked.clear()
        excel.plan.update(workbook=name, destructive=["delete rows 3:4 on Data"])
        await L.ExcelLiveEditTool().execute(dict(DELETE), _ctx(tmp_path))
        copy = next(r for r in excel.asked if r["op"] == "apply")["copy_path"]
        assert copy.endswith(ending) and not copy.endswith(".csv.xlsx"), copy


async def test_a_map_of_something_that_is_not_a_workbook_is_refused_in_words(excel, tmp_path, monkeypatch):
    def child(request, timeout):
        out = Path(request["dir"]) / "ledger.csv"
        out.write_text("a,b\n1,2\n", encoding="utf-8")
        return {"ok": True, "path": str(out), "workbook": "ledger.csv", "source": "Book1", "saved": True}

    monkeypatch.setattr(L, "_ask_worker", child)
    res = await L.ExcelLiveLookTool().execute({"map": True}, _ctx(tmp_path))
    assert not res.ok and "ledger.csv" in res.error and "range" in res.error
    assert not any((tmp_path / "excel-live" / "snap").iterdir()), "the reading copy is removed"


class _Book:
    def __init__(self, name, folder="C:\\w"):
        self.Name, self.FullName = name, f"{folder}\\{name}"


class _App:
    def __init__(self, *books):
        self.Workbooks, self.ActiveWorkbook = list(books), books[0]


def test_a_name_with_a_dot_never_picks_another_workbook():
    """"Budget.2025" lost its ".2025" as if it were a file ending, and so
    named Budget.xlsx."""
    plain, dated = _Book("Budget.xlsx"), _Book("Budget.2025.xlsx")
    apps = [_App(plain, dated)]
    assert W._find_book(apps, "Budget.2025")[1] is dated and W._find_book(apps, "budget")[1] is plain
    assert W._find_book(apps, "BUDGET.XLSX")[1] is plain and W._find_book(apps, "C:\\w\\Budget.2025.xlsx")[1] is dated
    with pytest.raises(W.Refusal):
        W._find_book([_App(plain)], "Budget.2025")
    with pytest.raises(W.Refusal, match="More than one"):
        W._find_book([_App(plain), _App(_Book("Budget.xlsx", "D:\\other"))], "Budget.xlsx")
    # Undo names the workbook by where it was open from, and nothing looser.
    other = _Book("Budget.xlsx", "D:\\other")
    with pytest.raises(W.Refusal, match="not open in Excel now"):
        W._find_book([_App(other)], "C:\\w\\Budget.xlsx", exact=True)
    assert W._find_book([_App(other, plain)], "c:\\W\\budget.xlsx", exact=True)[1] is plain


def test_a_rollback_excel_was_too_busy_for_is_never_reported_as_nothing_changed(monkeypatch, tmp_path):
    """Excel busy while the finished steps were being put back surfaced as a
    plain "Excel is busy", which the tool read as "nothing was changed"."""

    class Busy(Exception):
        hresult = -2147418111

    book = SimpleNamespace(Name="Budget.xlsx", FullName="C:\\w\\Budget.xlsx", ReadOnly=False, Worksheets=[], AutoSaveOn=False, Saved=True)
    monkeypatch.setattr(W, "_apps", lambda: [object()])
    monkeypatch.setattr(W, "_find_book", lambda apps, ref, **kw: (SimpleNamespace(Calculation=-4105), book))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)

    def steps(cx, ops):
        if not cx.planning:
            cx.steps.append({"op": "set"})
            raise Busy()

    tries = []

    def restore(app, wb, path, forgiving):
        tries.append(forgiving)
        raise Busy()

    monkeypatch.setattr(W, "_run_steps", steps)
    monkeypatch.setattr(W, "_restore_file", restore)
    out = W.op_apply({"op": "apply", "ops": [{"op": "set"}, {"op": "set"}], "undo_path": str(tmp_path / "u.jsonl")})
    assert out["ok"] is False and out.get("partly") and not out.get("unchanged"), out
    assert "could not all be put back" in out["error"] and len(tries) > 1, "the rollback is tried more than once"


TABLE_ARMS_WHILE_OPEN = [
    "why is this formula returning #REF!", "fix my formula in D12", "my VLOOKUP returns #N/A", "add a total under column C",
    "sum rows 2 to 40", "what's in the tab I'm on?", "highlight the duplicates in this column", "freeze the top row of this sheet",
]
TABLE_NEVER = [
    "my current cell phone plan is too expensive", "the active cell phone numbers on file", "what is the current range of estimates",
    "the K-1 for the Smith trust arrived", "review the W-2 box 12 codes", "Q3 estimated payments are due", "list the open items for the audit",
    "who are our active clients", "send the selected clients a reminder", "I want to excel at tax planning",
    "make me a spreadsheet of my 2025 expenses", "create a new workbook for the Jones engagement", "export this to Excel",
    "build an excel file with one tab per month", "what is the formula for the QBI deduction",
    "prepare the balance sheet", "the current year return", "fill out the social security benefits worksheet",
]


def test_the_tab_i_am_on_is_a_browsers_tab_unless_excel_is_running():
    assert not wants_live_excel("summarize the tab I'm on", False), "it armed the Excel tools with Excel closed"
    assert wants_live_excel("add a total row to the sheet I'm on", False), "a sheet is never a browser's"


@pytest.mark.parametrize("text", TABLE_ARMS_WHILE_OPEN)
def test_natural_requests_about_the_open_workbook_arm_the_look_while_excel_runs(text):
    assert wants_live_excel(text, True) and not wants_live_excel(text, True, has_files=True)


@pytest.mark.parametrize("text", TABLE_NEVER)
def test_a_cpas_everyday_words_and_a_request_for_a_new_file_arm_nothing(text):
    assert not wants_live_excel(text, True) and not wants_live_excel(text, False), text


def test_the_review_arming_words_are_linear_on_a_long_message():
    import subprocess
    import sys

    code = (
        "import time\n"
        "from iron_jarvis.tools.autoselect import wants_live_excel\n"
        "t = time.perf_counter()\n"
        "for s in ('make ' * 40000, 'create a new ' * 20000, 'formula ' * 30000, 'this ' * 40000, 'rows ' * 40000 + '1', 'column ' * 30000, 'excel at ' * 20000):\n"
        "    wants_live_excel(s, True)\n"
        "    wants_live_excel(s * 8, True)\n"
        "print(time.perf_counter() - t)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and float(out.stdout.strip()) < 5.0, out.stdout + out.stderr
