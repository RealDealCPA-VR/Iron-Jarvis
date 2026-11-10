"""What-if and goal seek, checked against the REAL Excel (v1.467.0).

Skipped unless ``IJ_LIVE_EXCEL=1`` on Windows with Excel. Jarvis answers "what
would these cells come to?" on the side, in its own engine; here Excel is then
made to hold those values for real, and the two must agree. Goal seek is
compared with Excel's own Goal Seek.

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


@pytest.fixture()
def model(excel, tmp_path):
    """A small pricing model that leans on the functions people really use:
    lookups, conditions, rounding, conditional sums, dates."""
    wb = excel.Workbooks.Add()
    a = wb.Worksheets(1)
    a.Name = "Inputs"
    a.Range("A1:B6").Formula = (("Input", "Value"), ("Units", 1200), ("Price", 24.5), ("Discount tier", "B"),
                                ("Tax rate", 0.0725), ("Fixed costs", 9000))
    t = wb.Worksheets.Add(None, a)
    t.Name = "Tiers"
    t.Range("A1:B4").Formula = (("Tier", "Discount"), ("A", 0), ("B", 0.05), ("C", 0.12))
    # Costs before Calc: a formula that names a sheet which does not exist yet
    # is saved by Excel as a link to another workbook.
    k = wb.Worksheets.Add(None, t)
    k.Name = "Costs"
    k.Range("A1:C5").Formula = (("Kind", "Item", "Per unit"), ("variable", "Material", 6.2), ("fixed", "Rent", 0),
                                ("variable", "Labour", 4.1), ("variable", "Freight", 0.85))
    c = wb.Worksheets.Add(None, k)
    c.Name = "Calc"
    c.Range("A1:B10").Formula = (
        ("Line", "Amount"),
        ("Discount", "=VLOOKUP(Inputs!B4,Tiers!A2:B4,2,FALSE)"),
        ("Net price", "=ROUND(Inputs!B3*(1-B2),2)"),
        ("Revenue", "=Inputs!B2*B3"),
        ("Volume bonus", "=IF(Inputs!B2>=1000,B4*0.02,0)"),
        ("Variable cost", "=SUMIFS(Costs!C2:C5,Costs!A2:A5,\"variable\")*Inputs!B2"),
        ("Tax", "=ROUND((B4+B5)*Inputs!B5,2)"),
        ("Profit", "=B4+B5-B6-Inputs!B6"),
        ("Margin", "=IFERROR(B8/B4,0)"),
        ("Best case", "=MAX(B8,B8*1.1)+MIN(B5,500)"),
    )
    path = tmp_path / "Pricing.xlsx"
    wb.SaveAs(str(path))
    try:
        yield wb, path
    finally:
        try:
            wb.Close(False)
        except Exception:  # noqa: BLE001
            pass


def _trace(path: Path, **args):
    from iron_jarvis.documents.excel_trace import ExcelTraceTool
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=path.parent, session_id="s", agent_run_id="r",
                      config=SimpleNamespace(home=path.parent), event_bus=None, engine=None)
    return asyncio.run(ExcelTraceTool().execute({"path": str(path), **args}, ctx))


WATCH = [f"Calc!B{r}" for r in range(2, 11)]


@pytest.mark.parametrize(
    "changes",
    [
        {"Inputs!B2": 800},                                   # below the bonus threshold
        {"Inputs!B3": 31.99, "Inputs!B4": "C"},               # a new price and a deeper discount tier
        {"Inputs!B5": 0.1, "Inputs!B6": 0, "Inputs!B2": 5000},
        {"Costs!C2": 9.75, "Inputs!B4": "A"},
    ],
)
def test_a_what_if_equals_what_excel_comes_to_when_the_values_are_really_set(model, changes):
    wb, path = model
    res = _trace(path, mode="what_if", set=changes, watch=WATCH)
    assert res.ok, res.error
    rows = {r["cell"]: r for r in res.data["watch"]}
    assert not any("problem" in r for r in rows.values()), [r.get("problem") for r in rows.values()]
    for where, value in changes.items():  # now make Excel hold them for real
        sheet, cell = where.split("!")
        wb.Worksheets(sheet).Range(cell).Value = value
    for r in range(2, 11):
        excel_says = wb.Worksheets("Calc").Range(f"B{r}").Value
        jarvis_says = rows[f"Calc!B{r}"]["then"]
        assert abs(float(excel_says) - float(jarvis_says)) <= 1e-9 * max(1.0, abs(float(excel_says))), (r, excel_says, jarvis_says)


def test_goal_seek_agrees_with_excels_own(model):
    """Fixed costs feed profit in a straight line: both must find the same value."""
    wb, path = model
    res = _trace(path, mode="goal_seek", cell="Calc!B8", goal=20000, change="Inputs!B6")
    assert res.ok, res.error
    assert res.data["exact"] is True and "NOT EXACT" not in res.output
    calc, inputs = wb.Worksheets("Calc"), wb.Worksheets("Inputs")
    assert calc.Range("B8").GoalSeek(20000, inputs.Range("B6")) is True
    excels = float(inputs.Range("B6").Value)
    assert abs(float(res.data["answer"]) - excels) < 0.005, (res.data["answer"], excels)
    inputs.Range("B6").Value = res.data["answer"]  # and Excel agrees it lands there
    assert abs(float(calc.Range("B8").Value) - 20000) < 1e-6, calc.Range("B8").Value


def test_goal_seek_says_so_when_no_value_lands_on_the_goal(model):
    """The net price is ROUNDED to cents, so profit moves in steps as the price
    moves. Jarvis gives the closest value and says it is not exact, and Excel
    confirms what that value comes to."""
    wb, path = model
    res = _trace(path, mode="goal_seek", cell="Calc!B8", goal=20000, change="Inputs!B3")
    assert res.ok, res.error
    assert res.data["exact"] is False and "NOT EXACT" in res.output
    calc, inputs = wb.Worksheets("Calc"), wb.Worksheets("Inputs")
    inputs.Range("B3").Value = res.data["answer"]
    excel_says = float(calc.Range("B8").Value)
    assert abs(excel_says - float(res.data["reaches"])) < 1e-6, (excel_says, res.data["reaches"])
    assert abs(excel_says - 20000) < 15, excel_says  # within one cent of price x 1,200 units


def test_how_reads_the_same_values_excel_shows(model):
    wb, path = model
    res = _trace(path, cell="Calc!B8", depth=4)
    assert res.ok, res.error
    profit = float(wb.Worksheets("Calc").Range("B8").Value)
    assert f"Calc!B8 (Profit) = {profit:,.15g}"[:28] in res.output
    assert "Inputs!B2 (Units) = 1,200  [typed in]" in res.output and "Tiers!A2:B4" in res.output
    assert set(res.data["inputs"]) >= {"Inputs!B2", "Inputs!B3", "Inputs!B4", "Inputs!B6"}


# ------------------------------------------------------- jump to the cell --


@pytest.fixture()
def the_child_may_run():
    """The suite keeps the Excel child off (conftest); this is what it is for."""
    from iron_jarvis.documents import excel_live as L

    old = os.environ.pop(L.OFF_ENV, None)
    try:
        yield
    finally:
        if old is not None:
            os.environ[L.OFF_ENV] = old


def test_a_jump_selects_the_cell_and_changes_nothing(model, the_child_may_run):
    from iron_jarvis.documents import excel_live as L

    wb, path = model
    assert wb.Saved is True
    out = asyncio.run(L.goto(str(path), "Calc", "B8"))
    assert out == {"ok": True, "workbook": "Pricing.xlsx", "sheet": "Calc", "address": "B8"}, out
    assert wb.ActiveSheet.Name == "Calc" and wb.Application.ActiveCell.Address == "$B$8"
    assert wb.Saved is True, "showing a cell must not mark the workbook as changed"
    wb.Worksheets("Tiers").Visible = 0  # hidden
    out = asyncio.run(L.goto(str(path), "Tiers", "A1"))
    assert out["ok"] is False and "hidden in Excel" in out["error"]
    assert wb.ActiveSheet.Name == "Calc", "a refused jump leaves Excel where it was"
    out = asyncio.run(L.goto(str(path.with_name("Other.xlsx")), "Calc", "B8"))
    assert out["ok"] is False and "Other.xlsx" in out["error"], "a workbook that is not open is never opened"
