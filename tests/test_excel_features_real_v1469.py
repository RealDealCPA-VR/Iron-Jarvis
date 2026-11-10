"""The features Jarvis writes into a SAVED workbook, opened by the REAL Excel
(v1.469.0). Skipped unless ``IJ_LIVE_EXCEL=1`` on Windows with Excel.

openpyxl writing something Excel then "repairs" away would be a silent loss
in a client's workbook, so this opens the saved file in Excel (its own hidden
copy, by full path, read-only) and checks that each feature is there AND
behaves: the colour rule really colours the cells it should, the dropdown
really offers its choices, the name really means its range.

It starts its OWN hidden Excel and ends only that process.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Excel on Windows only"),
    pytest.mark.skipif(os.environ.get("IJ_LIVE_EXCEL") != "1", reason="set IJ_LIVE_EXCEL=1 to drive the real Excel"),
    pytest.mark.timeout(240),
]


@pytest.fixture(scope="module")
def excel():
    import pythoncom
    import win32com.client as wc
    import win32process

    pythoncom.CoInitialize()
    app = wc.DispatchEx("Excel.Application")
    pid = win32process.GetWindowThreadProcessId(app.Hwnd)[1]
    app.Visible = False
    app.DisplayAlerts = False
    app.IgnoreRemoteRequests = True
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


def _bgr(hex6: str) -> int:
    return int(hex6[4:6] + hex6[2:4] + hex6[0:2], 16)


def test_excel_opens_the_file_and_every_feature_is_there_and_works(excel, tmp_path):
    from iron_jarvis.documents.excel_tools import ExcelEditTool
    from iron_jarvis.tools.base import ToolContext

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Region", "Amount", "Status", "Due"])
    for i in range(1, 12):
        ws.append([["East", "West"][i % 2], i * 1000.5, "Open" if i % 3 else "Closed", None])
    ws["B13"] = "=SUM(B2:B12)"
    lists = wb.create_sheet("Lists")
    lists.append(["East"])
    lists.append(["West"])
    path = tmp_path / "Features.xlsx"
    wb.save(path)
    features = [
        {"op": "conditional_format", "range": "Data!B2:B12", "rule": "greater", "value": 5000, "fill": "C6EFCE"},
        {"op": "conditional_format", "range": "Data!C2:C12", "rule": "text_contains", "value": "Closed", "fill": "FFC7CE"},
        {"op": "conditional_format", "range": "Data!D2:D12", "rule": "formula", "formula": "=$B2<2000", "fill": "FFEB9C"},
        {"op": "validation", "range": "Data!C2:C12", "list": ["Open", "Closed"], "message": "Pick Open or Closed"},
        {"op": "validation", "range": "Data!A2:A12", "source": "Lists!A1:A2"},
        {"op": "validation", "range": "Data!B2:B12", "type": "decimal", "min": 0, "max": 1000000},
        {"op": "validation", "range": "Data!D2:D12", "type": "date", "min": "2026-01-01"},
        {"op": "freeze", "sheet": "Data", "cell": "B2"},
        {"op": "column_width", "sheet": "Data", "columns": "A:B", "width": 18},
        {"op": "name", "name": "Amounts", "refers_to": "Data!B2:B12"},
        {"op": "table", "range": "Data!A1:D12", "name": "Sales"},
        {"op": "note", "cell": "Data!B1", "text": "Net of tax"},
        {"op": "print_setup", "sheet": "Data", "orientation": "landscape", "fit_width": 1, "print_area": "A1:D12", "title_rows": "1"},
    ]
    ctx = ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r", config=SimpleNamespace(home=tmp_path),
                      event_bus=None, engine=None)
    res = asyncio.run(ExcelEditTool().execute(
        {"path": path.name, "features": features, "edits": [{"sheet": "Data", "cell": "B14", "formula": "=SUM(Amounts)"}]}, ctx))
    assert res.ok, res.error

    xl = excel.Workbooks.Open(str(path), 0, True)  # no link update, read-only
    try:
        d = xl.Worksheets("Data")
        # the colour rules colour exactly the cells they should
        green, red, amber = _bgr("C6EFCE"), _bgr("FFC7CE"), _bgr("FFEB9C")
        for row in range(2, 13):
            amount = d.Range(f"B{row}").Value
            assert (int(d.Range(f"B{row}").DisplayFormat.Interior.Color) == green) == (amount > 5000), row
            assert (int(d.Range(f"C{row}").DisplayFormat.Interior.Color) == red) == (d.Range(f"C{row}").Value == "Closed"), row
            assert (int(d.Range(f"D{row}").DisplayFormat.Interior.Color) == amber) == (amount < 2000), row
        # the dropdowns and limits
        v = d.Range("C5").Validation
        assert v.Type == 3 and v.Formula1 == "Open,Closed" and v.ErrorMessage == "Pick Open or Closed"
        assert d.Range("A5").Validation.Type == 3 and d.Range("A5").Validation.Formula1.replace("'", "") == "=Lists!$A$1:$A$2"
        vb = d.Range("B5").Validation
        assert vb.Type == 2 and float(vb.Formula1) == 0 and float(vb.Formula2) == 1000000
        assert d.Range("D5").Validation.Type == 4
        # the name means its range, and a formula using it comes to Excel's own sum
        assert xl.Names("Amounts").RefersTo.replace("'", "") == "=Data!$B$2:$B$12"
        assert abs(float(d.Range("B14").Value) - float(d.Range("B13").Value)) < 1e-9
        assert abs(float(d.Range("B13").Value) - sum(i * 1000.5 for i in range(1, 12))) < 1e-6
        # the table, the note, the widths, the printing
        assert d.ListObjects("Sales").Range.Address == "$A$1:$D$12"
        assert d.Range("B1").Comment.Text() == "Net of tax"
        assert abs(float(d.Columns("A").ColumnWidth) - 18) < 1.0
        assert d.PageSetup.Orientation == 2 and d.PageSetup.PrintArea == "$A$1:$D$12" and d.PageSetup.PrintTitleRows == "$1:$1"
        assert d.PageSetup.FitToPagesWide == 1
        # the frozen heading (a window setting: read it on the sheet's window)
        d.Activate()
        win = xl.Windows(1)
        assert win.FreezePanes is True and win.SplitRow == 1 and win.SplitColumn == 1
    finally:
        xl.Close(False)
