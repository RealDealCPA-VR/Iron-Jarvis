"""Excel's own features in a SAVED workbook (v1.469.0, documents/excel_features).

``excel_edit`` takes them as ``features`` with the live tool's step names.
Checked here without Excel (the real Excel opens the result in
``test_excel_features_real_v1469.py``):

* every step lands in the saved file as the thing it names;
* a step only Excel can make safely is refused BEFORE the workbook is opened;
* a step that fails leaves the file byte for byte as it was;
* a step never guesses its sheet;
* the change is one undoable edit, and formulas keep their saved results.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis.documents import excel_features as F
from iron_jarvis.documents import excel_live as L
from iron_jarvis.documents.excel_tools import ExcelEditTool
from iron_jarvis.tools.base import ToolContext


@pytest.fixture()
def book(tmp_path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Region", "Amount", "Status", "Due"])
    for i in range(1, 12):
        ws.append([["East", "West"][i % 2], i * 1000.5, "Open", None])
    ws["B13"] = "=SUM(B2:B12)"
    lists = wb.create_sheet("Lists")
    lists.append(["Open"])
    lists.append(["Closed"])
    path = tmp_path / "b.xlsx"
    wb.save(path)
    return path


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


def _edit(book: Path, features, **more):
    return asyncio.run(ExcelEditTool().execute({"path": book.name, "features": features, **more}, _ctx(book.parent)))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


ALL = [
    {"op": "conditional_format", "sheet": "Data", "range": "B2:B12", "rule": "greater", "value": 5000, "fill": "C6EFCE"},
    {"op": "conditional_format", "range": "Data!B2:B12", "rule": "between", "value": 1, "value2": 2000, "font_color": "9C0006", "bold": True},
    {"op": "conditional_format", "range": "Data!C2:C12", "rule": "text_contains", "value": "Open", "fill": "FFEB9C"},
    {"op": "conditional_format", "range": "Data!A2:A12", "rule": "duplicates", "fill": "FFC7CE"},
    {"op": "conditional_format", "range": "Data!B2:B12", "rule": "top", "value": 3, "fill": "FFFF00"},
    {"op": "conditional_format", "range": "Data!B2:B12", "rule": "data_bar"},
    {"op": "conditional_format", "range": "Data!B2:B12", "rule": "formula", "formula": "=$B2>AVERAGE($B$2:$B$12)", "fill": "DDEBF7"},
    {"op": "validation", "range": "Data!C2:C12", "list": ["Open", "Closed"], "message": "Pick Open or Closed"},
    {"op": "validation", "range": "Data!A2:A12", "source": "Lists!A1:A2"},
    {"op": "validation", "range": "Data!B2:B12", "type": "decimal", "min": 0, "max": 1000000},
    {"op": "validation", "range": "Data!D2:D12", "type": "date", "min": "2026-01-01"},
    {"op": "freeze", "sheet": "Data", "cell": "B2"},
    {"op": "column_width", "sheet": "Data", "columns": "A:B", "width": 18},
    {"op": "autofit", "sheet": "Lists"},
    {"op": "name", "name": "Amounts", "refers_to": "Data!B2:B12"},
    {"op": "table", "range": "Data!A1:D12", "name": "Sales"},
    {"op": "note", "cell": "Data!B1", "text": "Net of tax"},
    {"op": "print_setup", "sheet": "Data", "orientation": "landscape", "fit_width": 1, "print_area": "A1:D12",
     "title_rows": "1", "footer": "Page &P"},
]


def test_every_step_lands_in_the_saved_file(book):
    res = _edit(book, ALL)
    assert res.ok, res.error
    assert "added: colour rule (greater 5000) on Data!B2:B12;" in res.output and "table Sales on Data!A1:D12" in res.output
    assert len(res.data["features"]) == len(ALL)
    wb = load_workbook(book)
    ws = wb["Data"]
    rules = [r for rng in ws.conditional_formatting for r in rng.rules]
    assert sorted(r.type for r in rules) == sorted(
        ["cellIs", "cellIs", "containsText", "duplicateValues", "top10", "dataBar", "expression"])
    greater = next(r for r in rules if r.type == "cellIs" and r.operator == "greaterThan")
    assert greater.formula == ["5000"] and greater.dxf.fill.end_color.rgb.endswith("C6EFCE")
    dvs = {str(dv.sqref): dv for dv in ws.data_validations.dataValidation}
    assert dvs["C2:C12"].type == "list" and dvs["C2:C12"].formula1 == '"Open,Closed"' and dvs["C2:C12"].error == "Pick Open or Closed"
    assert dvs["A2:A12"].formula1 == "'Lists'!$A$1:$A$2"
    assert (dvs["B2:B12"].type, dvs["B2:B12"].operator, dvs["B2:B12"].formula1, dvs["B2:B12"].formula2) == ("decimal", "between", "0", "1000000")
    assert (dvs["D2:D12"].type, dvs["D2:D12"].operator, dvs["D2:D12"].formula1) == ("date", "greaterThanOrEqual", "DATE(2026,1,1)")
    assert ws.freeze_panes == "B2" and ws.column_dimensions["A"].width == 18 and ws.column_dimensions["B"].width == 18
    assert wb.defined_names["Amounts"].attr_text == "'Data'!$B$2:$B$12"
    assert ws.tables["Sales"].ref == "A1:D12"
    assert ws["B1"].comment.text == "Net of tax"
    assert ws.page_setup.orientation == "landscape" and ws.page_setup.fitToWidth == 1 and ws.page_setup.fitToHeight == 0
    assert ws.print_area == "'Data'!$A$1:$D$12" and ws.print_title_rows == "$1:$1" and ws.oddFooter.center.text == "Page &P"
    assert wb["Lists"].column_dimensions["A"].width == 8


def test_formulas_keep_a_saved_result_after_a_features_edit(book):
    assert _edit(book, [{"op": "freeze", "sheet": "Data", "cell": "A2"}]).ok
    assert load_workbook(book, data_only=True)["Data"]["B13"].value == pytest.approx(66033.0), \
        "saving from here drops Excel's results, so they are worked out and stored again"
    assert load_workbook(book)["Data"]["B13"].value == "=SUM(B2:B12)"


def test_features_and_cell_edits_go_in_one_call(book):
    res = _edit(book, [{"op": "name", "name": "Total", "refers_to": "Data!B13"}],
                edits=[{"sheet": "Data", "cell": "B14", "formula": "=Total*2"}])
    assert res.ok and "Data!B14 =Total*2 = 132,066.00" in res.output and "name Total means Data!B13" in res.output


@pytest.mark.parametrize("op", sorted(F.NEEDS_EXCEL))
def test_what_only_excel_can_do_safely_is_refused_before_the_file_is_opened(book, op, monkeypatch):
    before = _sha(book)
    monkeypatch.setattr("iron_jarvis.documents.excel_tools._apply_edits", lambda *a, **k: pytest.fail("the workbook was opened"))
    res = _edit(book, [{"op": "freeze", "sheet": "Data", "cell": "B2"}, {"op": op, "sheet": "Data"}])
    assert not res.ok and f"step 2: '{op}' is not done on a saved file" in res.error
    assert "Open the workbook in Excel and ask again" in res.error and res.error.endswith("Nothing was changed.")
    assert _sha(book) == before


@pytest.mark.parametrize(
    ("step", "words"),
    [
        ({"op": "freeze", "cell": "B2"}, "say which sheet in 'sheet' (this workbook's sheets: Data, Lists)"),
        ({"op": "freeze", "sheet": "Nope", "cell": "B2"}, "no sheet called 'Nope'"),
        ({"op": "conditional_format", "range": "Data!B2:B12", "rule": "greater", "value": 5}, "needs 'fill' and/or 'font_color'"),
        ({"op": "conditional_format", "range": "Data!B2:B12", "rule": "greater", "fill": "C6EFCE"}, "needs 'value'"),
        ({"op": "conditional_format", "range": "Data!B2:B12", "rule": "sparkle", "fill": "C6EFCE"}, "rule is one of greater"),
        ({"op": "conditional_format", "range": "Data!B2:B12", "rule": "greater", "value": 1, "fill": "green"}, "hex colour like FFF2CC"),
        ({"op": "conditional_format", "range": "everything", "rule": "duplicates", "fill": "C6EFCE"}, "not a cell or a range"),
        ({"op": "validation", "range": "Data!A1", "list": ["a,b"]}, "cannot contain a comma"),
        ({"op": "validation", "range": "Data!A1", "list": ["x" * 300]}, "too long for a typed list"),
        ({"op": "validation", "range": "Data!A1", "type": "whole"}, "needs 'min' and/or 'max'"),
        ({"op": "validation", "range": "Data!A1", "type": "date", "min": "soon"}, "is not a date"),
        ({"op": "validation", "range": "Data!A1"}, "validation needs 'list', 'source', or 'type'"),
        ({"op": "column_width", "sheet": "Data", "columns": "A:B", "width": 0}, "between 1 and 255"),
        ({"op": "column_width", "sheet": "Data", "columns": "1:2", "width": 9}, "a column or a span like A:C"),
        ({"op": "name", "name": "B2", "refers_to": "Data!B2"}, "not a cell address"),
        ({"op": "name", "name": "Rate", "refers_to": "B2"}, "a sheet and a range"),
        ({"op": "table", "range": "Data!A1"}, "needs a range with its heading row"),
        ({"op": "table", "range": "Data!A1:E12"}, "different, non-empty heading"),
        ({"op": "note", "cell": "Data!A1:B2", "text": "x"}, "goes on one cell"),
        ({"op": "print_setup", "sheet": "Data"}, "needs at least one of"),
        ({"op": "print_setup", "sheet": "Data", "orientation": "sideways"}, "portrait or landscape"),
        ({"op": "glitter"}, "unknown step 'glitter'"),
    ],
)
def test_a_step_that_cannot_be_made_is_said_in_words_and_changes_nothing(book, step, words):
    before = _sha(book)
    res = _edit(book, [{"op": "freeze", "sheet": "Data", "cell": "B2"}, step],
                edits=[{"sheet": "Data", "cell": "A1", "value": "CHANGED"}])
    assert not res.ok and words in res.error and res.error.endswith("Nothing was changed."), res.error
    assert "step 2" in res.error
    assert _sha(book) == before, "the earlier step and the cell edit must not have been saved either"


def test_a_one_sheet_workbook_needs_no_sheet_named(tmp_path):
    wb = Workbook()
    wb.active.title = "Only"
    wb.active.append(["a", "b"])
    wb.save(tmp_path / "one.xlsx")
    res = asyncio.run(ExcelEditTool().execute({"path": "one.xlsx", "features": [{"op": "freeze", "cell": "A2"}]}, _ctx(tmp_path)))
    assert res.ok and load_workbook(tmp_path / "one.xlsx")["Only"].freeze_panes == "A2"


def test_two_tables_may_not_overlap_or_share_a_name(book):
    assert _edit(book, [{"op": "table", "range": "Data!A1:D12", "name": "Sales"}]).ok
    again = _edit(book, [{"op": "table", "range": "Data!A1:B5", "name": "sales"}])
    assert not again.ok and "already has a table called sales" in again.error
    over = _edit(book, [{"op": "table", "range": "Data!C1:D12"}])
    assert not over.ok and "overlaps the table Sales (A1:D12)" in over.error
    free = _edit(book, [{"op": "table", "range": "Lists!A1:A2"}])
    assert free.ok and "table Table1 on Lists!A1:A2" in free.output


def test_a_features_edit_is_undone_as_one_change(book):
    tool = ExcelEditTool()
    args = {"path": book.name, "features": ALL}
    before = book.read_bytes()
    undo = asyncio.run(tool.capture_undo(args, _ctx(book.parent)))
    assert asyncio.run(tool.execute(args, _ctx(book.parent))).ok and book.read_bytes() != before
    assert asyncio.run(tool.revert(undo, _ctx(book.parent))).ok
    assert book.read_bytes() == before


def test_the_step_names_are_the_live_tools(book):
    """One vocabulary for an open workbook and a saved one: every step here is
    a step the live tool's description names, and nothing is in both lists."""
    for op in F.FEATURE_OPS + F.NEEDS_EXCEL:
        assert op in L._EDIT_HELP, op
    assert not set(F.FEATURE_OPS) & set(F.NEEDS_EXCEL)
    assert set(F._STEPS) == set(F.FEATURE_OPS)
    assert "features" in ExcelEditTool.input_schema["properties"] and "conditional_format" in ExcelEditTool.description


def test_too_many_steps_and_a_wrong_shape_are_refused(book):
    assert "at most 60 feature steps" in _edit(book, [{"op": "freeze", "sheet": "Data", "cell": "B2"}] * 61).error
    assert "'features' is a list of steps" in _edit(book, ["freeze"]).error
