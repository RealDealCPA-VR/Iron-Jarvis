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
    res = asyncio.run(ExcelReadTool().execute({"path": "live:", "sheet": "Data", "range": "A1:E3"}, ctx))
    assert res.ok and '["East", "Pens", 1000, 2.5, 2500]' in res.output, res.output  # the unsaved value, calculated
